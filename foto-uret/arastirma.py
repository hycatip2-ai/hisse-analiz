"""Araştırma asistanı: internette arayıp kaynaklı cevap yazan, prompt hazırlayan ajan.

Dil modeli OpenRouter'daki ücretsiz modellerden gelir (varsayılan MiniMax-M3.1-Flash);
internet araması Tavily ile yapılır. Ajan döngüsü dış servislerden bağımsızdır: `llm`
ve `ara` fonksiyonları dışarıdan verilir, böylece yerelde sahte servislerle test edilebilir.
"""

import json
import os
from datetime import datetime, timezone

# Sırayla denenir: ilki kullanılamazsa (limit, yoğunluk) OpenRouter bir sonrakine geçer.
# Ücretsiz modeller zamanla değişebilir; yalnızca bu listeyi güncellemek yeterli.
MODELLER = [
    "minimax/minimax-m3.1-flash:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "thinkingmachines/inkling-small:free",
]
GUNLUK_UCRETSIZ_ISTEK = 50  # OpenRouter ücretsiz katmanı (hiç kredi yüklenmemiş hesap)

# Mod başına en fazla kaç arama turu (her tur = 1 model isteği)
TUR_SINIRI = {"soru": 3, "arastirma": 8}

ARACLAR = [
    {
        "type": "function",
        "function": {
            "name": "web_ara",
            "description": (
                "İnternette arama yapar ve sonuç sayfalarının içeriğini döndürür. Güncel veya doğrulanması "
                "gereken her bilgi için kullan. Sorguları çoğunlukla İngilizce yaz; Türkiye ile ilgili "
                "konularda Türkçe sorgu da kullan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sorgu": {"type": "string", "description": "Arama sorgusu"},
                    "haber": {"type": "boolean", "description": "Son haberler/gelişmeler aranıyorsa true"},
                },
                "required": ["sorgu"],
            },
        },
    }
]


def _tarih() -> str:
    return datetime.now(timezone.utc).strftime("%d.%m.%Y")


def sistem_mesaji(tur: str, hedef: str = "foto") -> str:
    ortak = (
        f"Bugünün tarihi: {_tarih()}. Kullanıcıyla Türkçe konuş (kullanıcı başka dil isterse o dilde). "
        "Bilmediğin veya emin olmadığın şeyleri uydurma; gerekiyorsa web_ara aracıyla araştır. "
        "Araştırdığın bilgileri kullanırken kaynağı köşeli parantezle numarasıyla göster, ör. [2]. "
        "Kaynak listesini kendin yazma; uygulama otomatik ekler. Cevabını Markdown ile biçimlendir."
    )
    if tur == "soru":
        return ortak + (
            " Kısa ve net cevap ver. Güncel bilgi gerektirmeyen basit sorularda arama yapma; gerekiyorsa "
            "en fazla 2-3 arama yap."
        )
    if tur == "arastirma":
        return ortak + (
            " Derin araştırma yapıyorsun. Önce konuyu alt sorulara böl, sonra her biri için farklı "
            "sorgularla ara (bir turda birden fazla web_ara çağrısı yapabilirsin). Kaynakları "
            "karşılaştır, çelişkileri belirt. Yeterli bilgi toplayınca kapsamlı bir rapor yaz: "
            "'## Özet' ile başla, sonra konuya uygun başlıklar, gerekiyorsa karşılaştırma tablosu, en "
            "sonda '## Sonuç'. Her önemli iddiada kaynak numarası olsun."
        )
    if tur == "prompt":
        ne = "video" if hedef == "video" else "fotoğraf"
        ek = (
            "Video için: sahneyi, kişileri ve görünüşlerini, hareketi ve zamanlamasını, kamera hareketini, "
            "ışığı ve sesi akıcı cümlelerle anlat; etiket listesi kullanma; 60-120 kelime."
            if hedef == "video"
            else "Fotoğraf için: konu, kompozisyon, ışık, ortam, stil ve ayrıntıları anlat; 40-90 kelime."
        )
        return (
            f"Kullanıcının fikrinden bir yapay zeka {ne} modeli için İngilizce prompt yaz. {ek} "
            "Kullanıcının istediği her öğeyi koru. Cevap olarak YALNIZCA prompt metnini yaz; başlık, "
            "açıklama, tırnak veya kod bloğu ekleme."
        )
    raise ValueError(f"Bilinmeyen tür: {tur}")


class Kaynaklar:
    """Bütün aramalarda tutarlı numaralı kaynak listesi."""

    def __init__(self):
        self.liste: list[dict] = []

    def numara(self, url: str, baslik: str) -> int:
        for i, k in enumerate(self.liste, 1):
            if k["url"] == url:
                return i
        self.liste.append({"url": url, "baslik": baslik or url})
        return len(self.liste)


def arama_metni(sonuclar: list[dict], kaynaklar: Kaynaklar, ham: bool) -> str:
    """Arama sonuçlarını modele verilecek numaralı metne çevirir."""
    if not sonuclar:
        return "Sonuç bulunamadı."
    parcalar = []
    for s in sonuclar:
        n = kaynaklar.numara(s["url"], s.get("title", ""))
        icerik = (s.get("raw_content") if ham else None) or s.get("content") or ""
        icerik = icerik.strip()[: 6000 if ham else 800]
        parcalar.append(f"[{n}] {s.get('title', '')}\nURL: {s['url']}\n{icerik}")
    return "\n\n---\n\n".join(parcalar)


def yurut(tur: str, mesajlar: list[dict], hedef: str, llm, ara, ilerleme=lambda metin: None) -> dict:
    """Ajan döngüsü.

    mesajlar: [{"rol": "kullanici"|"asistan", "metin": str}, ...] (son mesaj kullanıcının)
    llm(messages, tools=None, tool_choice=None) -> (message_dict, model_adi)
    ara(sorgu, haber, ham) -> [{"title", "url", "content", "raw_content"?}, ...]
    """
    sohbet = [{"role": "system", "content": sistem_mesaji(tur, hedef)}]
    for m in mesajlar[-12:]:
        sohbet.append({"role": "user" if m["rol"] == "kullanici" else "assistant", "content": m["metin"]})

    kaynaklar = Kaynaklar()
    istek = 0
    model = None

    if tur == "prompt":
        ilerleme("✍️ Prompt hazırlanıyor")
        cevap, model = llm(sohbet)
        metin = (cevap.get("content") or "").strip().strip('"').strip("`").strip()
        return {"tur": tur, "cevap": metin, "kaynaklar": [], "model": model, "istek": 1, "hedef": hedef}

    ham = tur == "arastirma"
    sinir = TUR_SINIRI[tur]
    for tur_no in range(sinir + 1):
        son_tur = tur_no == sinir
        if son_tur:
            # Sınıra gelindi: artık arama yok, topladıklarıyla cevap yazsın.
            sohbet.append({"role": "user", "content": "Arama hakkın doldu. Topladığın bilgilerle şimdi cevabını yaz."})
        ilerleme("✍️ Cevap yazılıyor" if son_tur else "🤔 Düşünüyor" if tur_no == 0 else "🧠 Bulguları değerlendiriyor")
        cevap, model = llm(sohbet, tools=None if son_tur else ARACLAR, tool_choice=None if son_tur else "auto")
        istek += 1
        cagrilar = cevap.get("tool_calls") or []
        if not cagrilar:
            metin = (cevap.get("content") or "").strip()
            kullanilan = [k for i, k in enumerate(kaynaklar.liste, 1) if f"[{i}]" in metin]
            return {"tur": tur, "cevap": metin, "kaynaklar": kaynaklar.liste, "kullanilan": len(kullanilan),
                    "model": model, "istek": istek}

        sohbet.append({"role": "assistant", "content": cevap.get("content") or "", "tool_calls": cagrilar})
        for c in cagrilar[:4]:  # bir turda en fazla 4 arama
            try:
                arg = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                arg = {}
            sorgu = str(arg.get("sorgu") or arg.get("query") or "").strip()
            if c["function"]["name"] != "web_ara" or not sorgu:
                sonuc = "Geçersiz araç çağrısı."
            else:
                ilerleme(f"🔎 Aranıyor: {sorgu}")
                try:
                    sonuc = arama_metni(ara(sorgu, bool(arg.get("haber")), ham), kaynaklar, ham)
                except Exception as e:  # arama hatası ajanı durdurmasın
                    sonuc = f"Arama başarısız: {e}"
            sohbet.append({"role": "tool", "tool_call_id": c["id"], "content": sonuc})
        for c in cagrilar[4:]:
            sohbet.append({"role": "tool", "tool_call_id": c["id"], "content": "Atlandı: bir turda en fazla 4 arama."})
    raise RuntimeError("Cevap üretilemedi")


class KullaniciHatasi(Exception):
    """Kullanıcıya olduğu gibi gösterilecek hata."""


def openrouter_llm(sayac=lambda: None):
    import httpx

    anahtar = os.environ.get("OPENROUTER_API_KEY", "")
    if not anahtar or anahtar == "yok":
        raise KullaniciHatasi("Araştırma için OpenRouter anahtarı eklenmemiş (GitHub'da OPENROUTER_API_KEY).")

    def llm(messages, tools=None, tool_choice=None):
        govde = {"model": MODELLER[0], "models": MODELLER, "messages": messages, "max_tokens": 12000}
        if tools:
            govde["tools"] = tools
            govde["tool_choice"] = tool_choice or "auto"
        sayac()
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {anahtar}", "X-Title": "Foto Studyo"},
            json=govde,
            timeout=240,
        )
        if r.status_code == 429:
            raise KullaniciHatasi("Ücretsiz modellerin günlük istek sınırı doldu ya da servis çok yoğun. "
                                  "Biraz sonra veya yarın tekrar dene.")
        if r.status_code in (401, 403):
            raise KullaniciHatasi("OpenRouter anahtarı geçersiz görünüyor.")
        veri = r.json()
        if r.status_code >= 400 or "error" in veri:
            hata = (veri.get("error") or {}).get("message") or r.text[:300]
            raise KullaniciHatasi(f"Model hatası: {hata}")
        return veri["choices"][0]["message"], veri.get("model")

    return llm


def tavily_ara():
    import httpx

    anahtar = os.environ.get("TAVILY_API_KEY", "")

    def ara(sorgu, haber=False, ham=False):
        if not anahtar or anahtar == "yok":
            raise RuntimeError("Arama anahtarı (TAVILY_API_KEY) eklenmemiş; arama yapılamıyor.")
        govde = {"query": sorgu, "max_results": 5, "search_depth": "basic"}
        if ham:
            govde["include_raw_content"] = "markdown"
        if haber:
            govde["topic"] = "news"
        r = httpx.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {anahtar}"},
                       json=govde, timeout=60)
        if r.status_code in (432, 433):
            raise RuntimeError("Aylık ücretsiz arama kotası doldu.")
        r.raise_for_status()
        return r.json().get("results", [])

    return ara
