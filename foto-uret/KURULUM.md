# Foto Stüdyo: Kurulum

Telefona kurulabilen yapay zeka fotoğraf ve video üretme uygulaması (PWA). Modal (bulut GPU)
üzerinde fotoğraf için **Qwen-Image-2.1** (en iyi kalite ve referans görseller; Qwen araştırma
lisansı, kişisel kullanım), **FLUX.2 [dev]** (ticari olmayan lisans) ve hızlı mod için **Z-Image-Turbo** (Apache 2.0), video için **LTX-2.5** (sesli video;
yıllık geliri 10 milyon $ altı için ücretsiz LTX lisansı) çalışır. GPU süresi Modal'ın
aylık ücretsiz kredisinden düşer.

## 0. Bilgisayarsız kurulum (sadece telefon)

Deploy işini GitHub Actions yapar (`.github/workflows/foto-deploy.yml`).

1. https://modal.com adresinde hesap aç.
2. Modal'da **Settings → API Tokens → New Token**. Çıkan `Token ID` (ak-...) ve
   `Token Secret` (as-...) değerlerini kopyala.
3. GitHub'da bu repoda **Settings → Secrets and variables → Actions → New repository secret**
   ile üç secret ekle:
   - `MODAL_TOKEN_ID`: Token ID
   - `MODAL_TOKEN_SECRET`: Token Secret
   - `FOTO_SIFRE`: fotoğraf sayfası için kendi belirleyeceğin şifre
4. **Actions** sekmesinde "Foto Üret - Modal'a yükle" işini çalıştır (veya son çalışmayı
   "Re-run" et). Bitince "Deploy" adımının çıktısındaki `https://...modal.run` adresi senin adresin.
5. Modal panelinden harcama limiti koy (aşağıdaki 1.5 maddesi).

Kodda değişiklik yapılıp `foto-uret/` klasörüne push edildiğinde deploy kendiliğinden tekrarlanır.

## 1. Bir kerelik kurulum (bilgisayardan, ~10 dakika)

1. https://modal.com adresinden hesap aç (GitHub ile giriş yapabilirsin).
2. Bilgisayarında Python 3.10+ olsun. Terminal / Komut İstemi aç:
   ```
   pip install modal
   modal setup
   ```
   Tarayıcı açılır, onay ver.
3. Kendine bir şifre belirle ve Modal'a kaydet (`BENIM-SIFREM` yerine kendi şifreni yaz):
   ```
   modal secret create foto-sifre SIFRE=BENIM-SIFREM
   ```
4. Bu klasöre gel ve yayına al:
   ```
   cd foto-uret
   modal deploy foto.py
   ```
   Çıktıda `https://...--foto-studyo....modal.run` gibi bir adres çıkar. Bu senin adresin.

5. Modal panelinde (Settings → Usage / Billing) **harcama limiti** koy, böylece
   ücretsiz kredi bitince sistem durur ve sürpriz fatura gelmez.

## 2. Telefondan kullanım

1. Deploy çıktısındaki `https://...foto-studyo....modal.run` adresini Chrome'da aç.
2. Sağ üstteki indirme simgesine veya Chrome menüsündeki **Uygulamayı yükle**'ye bas.
   Ana ekrana "Foto Stüdyo" ikonu gelir; tam ekran uygulama olarak açılır.
3. Şifrenle giriş yap (90 gün hatırlanır).
4. Açıklamayı yaz, mod / boyut / adet / kalite seç, **Üret**'e bas.
5. Görseller **Galeri** sekmesinde telefonda saklanır; oradan kaydet, paylaş,
   sil veya "Tekrar kullan" ile ayarlarını forma geri yükle.

- Sağ üstteki nokta GPU durumunu gösterir: gri = uykuda, sarı = hazırlanıyor, yeşil = hazır.
  Açıklama kutusuna dokununca GPU arka planda uyanmaya başlar.
- Galeri sadece o telefonda durur; uygulama verisini silersen galeri de silinir.
- İlk üretim veya bir süre kullanmadıktan sonraki ilk üretim **1-3 dakika** sürer
  (GPU açılıp model yükleniyor; en ilk seferde model ~20 GB indirilir).
  Sonrakiler birkaç saniye sürer.
- İngilizce açıklamalar en iyi sonucu verir.
- Aynı "tohum" sayısı ile aynı açıklama aynı resmi verir; beğendiğin bir sonucu
  küçük değişikliklerle tekrar üretmek için işe yarar.

## Fotoğraf: kalite ve referans görseller

- **Kalite:** *Hızlı* = Z-Image-Turbo (birkaç saniye); *En iyi* = Qwen-Image-2.1 (~1 MP, ~20 sn);
  *HD* = Qwen-Image-2.1 (~2 MP, ~40 sn). Fotoğraf başına maliyet ~1 sent.
  *FLUX* = FLUX.2 [dev] (~1 MP, ~30 sn, referanslı ~1 dk; fotoğraf başına ~4-6 sent; ticari olmayan
  lisans). Referansla düzenlemede (kişiyi başka sahneye koyma) Qwen istediğini yapmazsa FLUX'u dene.
  İndirmek için https://huggingface.co/black-forest-labs/FLUX.2-dev sayfasında lisansı kabul etmiş
  olmak gerekir (video için kullanılan HF_TOKEN yeterli).
- **Referans görseller:** Fotoğraf modunda 10'a kadar görsel ekleyip ne yapılacağını yaz
  ("bu kadını sahilde göster", "arka planı değiştir", "bu iki kişiyi aynı karede göster").
  Kişi/ürün kimliği korunur. Galeride uzun basıp **Referans olarak kullan** ile de eklenir.
  Referans varken Qwen (FLUX seçiliyse FLUX) kullanılır; boyut son referansın oranından alınır.
- Sadece kendi fotoğraflarını veya izni olan kişilerinkini kullan.

## Video

- İki video modeli var:
  - **LTX · sesli** (LTX-2.5): sesli, 5 veya 10 sn, 1080p'ye kadar; fotoğraftan video da üretir.
  - **Wan · gerçekçi insan** (Wan 2.2, Apache 2.0): karşılaştırmada insan yüzlerini daha doğal üretti.
    Yalnızca yazıdan, sessiz, 5 sn, 720p, saniyede 16 kare. Hızlandırıcı (Lightning) LoRA ile 40 yerine
    4 adımda üretir (~1 dk + GPU uyanıyorsa ~1 dk; video başına ~0,10-0,15 $). Açıklama ayrıntılandırılmaz; kişiyi, hareketi, ortamı ve ışığı kendin ayrıntılı yaz.
    Model (~70 GB) deploy sırasında "Wan modelini indir" adımında indirilir.
- Aşağıdaki maddeler LTX içindir.
- Üret ekranında **Video**'yu seç. Yazıdan video üretebilir ya da bir başlangıç fotoğrafı
  ekleyebilirsin (telefondan seç veya Galeri'de bir fotoğrafı açıp **Canlandır**).
- 5 veya 10 saniyelik, sesli, 24 fps video. Boyutlar: kare 1280×1280, dikey 1088×1920,
  yatay 1920×1088.
- Açıklaman önce küçük bir yapay zeka (Gemma 4) ile ayrıntılı sahne tarifine çevrilir; kısa
  yazman yeterli. Modelin kullandığı metni galeride uzun basıp **Bilgi**'de görebilirsin.
- İki aşamalı üretim: önce tam model, rehberlik ve negatif listeyle yarım çözünürlükte hareket ve
  ses üretilir; sonra 2 kat büyütülüp tam çözünürlükte ayrıntılar iyileştirilir.
- Videoda mod (Gerçekçi, Portre...) kullanılmaz; bu ekler fotoğraf terimleri olduğu için video
  modeli onları sahneye nesne olarak koyuyordu.
- Video ayrı ve daha güçlü bir GPU'da (H200) çalışır. GPU uykudaysa model yüklenirken
  birkaç dakika eklenir.
- Video modeli (~110 GB) deploy sırasında GPU'suz ucuz bir makinede bir kez indirilir
  (GitHub Actions'taki "Video modelini indir" adımı; ilk seferde 10-30 dk sürebilir).

### Video için Hugging Face erişimi (bir kerelik)

LTX-2.5 ücretsizdir ama indirmek için lisansı kabul etmiş bir Hugging Face hesabı gerekir.

1. https://huggingface.co adresinde ücretsiz hesap aç ve e-postanı doğrula.
2. https://huggingface.co/Lightricks/LTX-2.5-Diffusers sayfasında istenen bilgileri doldurup
   erişim koşullarını kabul et (onay otomatik gelir).
3. **Settings → Access Tokens → Create new token** → tür **Read** → oluştur ve kopyala (`hf_...`).
4. GitHub'da bu repoda **Settings → Secrets and variables → Actions** altına
   `HF_TOKEN` adıyla ekle.
5. Actions sekmesinden "Foto Üret - Modal'a yükle" işini yeniden çalıştır.

## Araştırma asistanı

**Araştır** sekmesinde üç mod var:
- **Soru:** hızlı cevap; gerekirse internette arar.
- **Derin araştırma:** konuyu birçok kaynaktan araştırıp kaynaklı rapor yazar (1-4 dk).
- **Prompt:** Türkçe fikrini fotoğraf/video için İngilizce prompta çevirir; **Üret'e gönder** ile
  doğrudan Üret ekranına yerleştirir.

Ücretsiz çalışır: dil modeli OpenRouter'ın ücretsiz modelleri (varsayılan MiniMax-M3.1-Flash;
yedekleri `arastirma.py` içindeki `MODELLER` listesinde), internet araması Tavily'nin ücretsiz
planı. OpenRouter ücretsiz modellerde günde 50 istek sınırı uygular (bir derin araştırma 3-9
istek kullanır); sayaç Araştır ekranının üstünde görünür. Yazdıkların bu servislere gider;
kişisel/gizli bilgi yazma.

Kurulum (bir kerelik, kredi kartı gerekmez):
1. https://openrouter.ai adresinde hesap aç → **Keys → Create Key** → anahtarı kopyala (`sk-or-...`).
2. https://tavily.com adresinde hesap aç → panelde API anahtarını kopyala (`tvly-...`).
3. GitHub'da **Settings → Secrets and variables → Actions** altına `OPENROUTER_API_KEY` ve
   `TAVILY_API_KEY` adlarıyla ekle, sonra "Foto Üret - Modal'a yükle" işini yeniden çalıştır.

## Gizlilik

- Fotoğraf/video üretimi (referans fotoğraflar dahil) yalnızca senin Modal hesabında çalışır;
  model üreticisine veya başka bir yapay zeka şirketine veri gönderilmez.
- Girdiler (açıklama, referans fotoğraflar) ve sonuçlar Modal'ın iş kayıtlarından geçmez; geçici
  bir kutuya (`foto-teslim` Dict) konur. Girdi GPU okuyunca, sonuç telefon teslim alınca hemen
  silinir. Teslim alınamayan kayıtları Modal 7 gün işlem görmeyince kendisi siler.
- Galeri yalnızca telefonda saklanır.
- Araştırma asistanına yazılanlar dış servislere (OpenRouter, model sağlayıcı, Tavily) gider.

## Maliyet

- 2 dakika kullanılmazsa GPU kendiliğinden kapanır; kapalıyken ücret yok.
- Fotoğraf ve video için ayrı ayrı, aynı anda en fazla 1 GPU açılır (`max_containers=1`).
- Kabaca fotoğraf başına birkaç kuruş, video başına yaklaşık 0,15-0,50 $; aylık ücretsiz kredi kişisel kullanım için
  genelde yeterli. Güncel fiyatlar: https://modal.com/pricing

## Değiştirmek istersen

- Kodu değiştirdikten sonra tekrar `modal deploy foto.py` çalıştır.
- Şifreyi değiştirmek: `modal secret create foto-sifre SIFRE=YENI --force`, ardından tekrar deploy.
- Kayıtlar / hatalar: https://modal.com/apps adresinde `foto-uret` uygulaması.
