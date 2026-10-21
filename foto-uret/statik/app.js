"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

// GPU 2 dk kullanılmazsa kapanır (foto.py: scaledown_window=120).
const GPU_UYKU_MS = 110 * 1000;

const ayar = { tur: "foto", mod: "serbest", boyut: "kare", adet: 1, kalite: "standart", sure: 5 };
let modlar = [];
const sonGpu = { foto: 0, video: 0 };
const isiniyor = { foto: false, video: false };
let uretiliyor = false;
let baslangicResim = null; // video için başlangıç fotoğrafı (Blob)

// --- localStorage (gizli sekmede vb. yoksa sessizce geç) ---
const depo = {
  al(k) { try { return localStorage.getItem(k); } catch { return null; } },
  koy(k, v) { try { localStorage.setItem(k, v); } catch {} },
  sil(k) { try { localStorage.removeItem(k); } catch {} },
};

// --- Galeri: fotoğraf ve videolar telefonda IndexedDB'de saklanır ---
const vt = new Promise((tamam, hata) => {
  try {
    const r = indexedDB.open("foto-studyo", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("gorseller", { keyPath: "id" });
    r.onsuccess = () => tamam(r.result);
    r.onerror = () => hata(r.error);
  } catch (e) { hata(e); }
});

async function vtIslem(mod, fn) {
  const d = await vt;
  return new Promise((tamam, hata) => {
    const t = d.transaction("gorseller", mod);
    const istek = fn(t.objectStore("gorseller"));
    t.oncomplete = () => tamam(istek && istek.result);
    t.onerror = () => hata(t.error);
  });
}
const galeriEkle = (k) => vtIslem("readwrite", (s) => s.put(k)).catch(() => {});
const galeriSil = (id) => vtIslem("readwrite", (s) => s.delete(id)).catch(() => {});
const galeriHepsi = () => vtIslem("readonly", (s) => s.getAll()).catch(() => []);

// --- Yardımcılar ---
let bildirimZaman;
function bildir(metin) {
  const b = $("#bildirim");
  b.textContent = metin;
  b.hidden = false;
  clearTimeout(bildirimZaman);
  bildirimZaman = setTimeout(() => (b.hidden = true), 3500);
}

async function api(yol, govde) {
  const r = await fetch("/api/" + yol, {
    method: govde ? "POST" : "GET",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + (depo.al("oturum") || "") },
    body: govde ? JSON.stringify(govde) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) { cikis(); throw new Error(d.detail || "Tekrar giriş yap."); }
  if (!r.ok) {
    const detay = Array.isArray(d.detail) ? "Geçersiz istek" : d.detail;
    throw new Error(detay || "Sunucu hatası (" + r.status + ")");
  }
  return d;
}

// GPU işi arka planda çalışır; bitene kadar birkaç saniyede bir sorulur.
async function isBekle(kimlik, sinirDk = 10, ilerlemeFn = null) {
  const bas = Date.now();
  while (Date.now() - bas < sinirDk * 60 * 1000) {
    const d = await api("is/" + encodeURIComponent(kimlik));
    if (d.bitti) return d;
    if (ilerlemeFn && d.ilerleme) ilerlemeFn(d.ilerleme);
    await new Promise((r) => setTimeout(r, 2500));
  }
  throw new Error("Çok uzun sürdü, tekrar dene.");
}

function base64Blob(b64, tip) {
  const ham = atob(b64);
  const dizi = new Uint8Array(ham.length);
  for (let i = 0; i < ham.length; i++) dizi[i] = ham.charCodeAt(i);
  return new Blob([dizi], { type: tip });
}

// Başlangıç fotoğrafını küçültüp base64 JPEG yapar (yükleme hızlı olsun).
async function resimBase64(blob, enBuyuk = 1280) {
  const bmp = await createImageBitmap(blob);
  const oran = Math.min(1, enBuyuk / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * oran);
  c.height = Math.round(bmp.height * oran);
  c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
  const jpeg = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.9));
  const url = await new Promise((r) => { const f = new FileReader(); f.onload = () => r(f.result); f.readAsDataURL(jpeg); });
  return url.slice(url.indexOf(",") + 1);
}

const ORANLAR = {
  foto: { kare: "1 / 1", dikey: "4 / 7", yatay: "7 / 4", duvar: "23 / 50" },
  video: { kare: "1 / 1", dikey: "17 / 30", yatay: "30 / 17" },
};
const BOYUT_ADI = { kare: "Kare", dikey: "Dikey", yatay: "Yatay", duvar: "Duvar kâğıdı" };
const IPUCU = {
  foto: ["Örn: a red vintage car on a rainy street at night", "İngilizce yazarsan daha iyi sonuç alırsın."],
  video: ["Örn: a golden retriever running on a beach at sunset, waves crashing, camera tracking alongside",
    "Kısa yazman yeterli; açıklaman otomatik olarak ayrıntılandırılır. Video sesli üretilir. İngilizce daha iyi sonuç verir."],
};

// --- Giriş / çıkış ---
function ekranGoster() {
  const girisli = !!depo.al("oturum");
  $("#giris").hidden = girisli;
  $("#uygulama").hidden = !girisli;
  if (girisli) uygulamaBaslat();
}

$("#giris-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const buton = e.target.querySelector("button");
  buton.disabled = true;
  $("#giris-hata").textContent = "";
  try {
    const d = await api("giris", { sifre: $("#sifre").value });
    depo.koy("oturum", d.oturum);
    $("#sifre").value = "";
    ekranGoster();
  } catch (err) {
    $("#giris-hata").textContent = err.message;
  } finally {
    buton.disabled = false;
  }
});

function cikis() {
  depo.sil("oturum");
  modlar = [];
  ekranGoster();
}
$("#cikis").addEventListener("click", cikis);

// --- Ana uygulama ---
async function uygulamaBaslat() {
  if (modlar.length) return;
  try {
    modlar = await api("modlar");
  } catch (err) {
    if (depo.al("oturum")) bildir(err.message);
    return;
  }
  const kutu = $("#modlar");
  kutu.innerHTML = "";
  for (const m of modlar) {
    const b = document.createElement("button");
    b.className = "mod" + (m.id === ayar.mod ? " secili" : "");
    b.dataset.v = m.id;
    b.setAttribute("role", "radio");
    b.innerHTML = `<span class="simge"></span><span></span>`;
    b.children[0].textContent = m.simge;
    b.children[1].textContent = m.ad;
    b.addEventListener("click", () => modSec(m.id));
    kutu.appendChild(b);
  }
}

function modSec(id) {
  ayar.mod = id;
  $$("#modlar .mod").forEach((b) => b.classList.toggle("secili", b.dataset.v === id));
}

function segmentKur(id, anahtar, sayi, sonra) {
  $$(`#${id} button`).forEach((b) =>
    b.addEventListener("click", () => {
      segmentSec(id, anahtar, sayi ? Number(b.dataset.v) : b.dataset.v);
      if (sonra) sonra();
    })
  );
}
function segmentSec(id, anahtar, deger) {
  ayar[anahtar] = deger;
  $$(`#${id} button`).forEach((b) => b.classList.toggle("secili", b.dataset.v === String(deger)));
}
segmentKur("tur", "tur", false, turGuncelle);
segmentKur("boyut", "boyut");
segmentKur("adet", "adet", true);
segmentKur("kalite", "kalite");
segmentKur("sure", "sure", true);

function turSec(tur) {
  segmentSec("tur", "tur", tur);
  turGuncelle();
}
function turGuncelle() {
  const video = ayar.tur === "video";
  $("#uygulama").classList.toggle("tur-video", video);
  if (video && ayar.boyut === "duvar") segmentSec("boyut", "boyut", "dikey");
  $("#prompt").placeholder = IPUCU[ayar.tur][0];
  $("#prompt-ipucu").textContent = IPUCU[ayar.tur][1];
  $("#uret span").textContent = video ? "Video üret" : "Üret";
  sunucuCiz();
}

// --- Başlangıç fotoğrafı (video) ---
function baslangicAyarla(blob) {
  baslangicResim = blob;
  const img = $("#baslangic-img");
  if (img.src) URL.revokeObjectURL(img.src);
  img.removeAttribute("src");
  if (blob) img.src = URL.createObjectURL(blob);
  $("#baslangic-bos").hidden = !!blob;
  $("#baslangic-dolu").hidden = !blob;
}
$("#baslangic-dosya").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (f) baslangicAyarla(f);
  e.target.value = "";
});
$("#baslangic-sil").addEventListener("click", () => baslangicAyarla(null));

// --- Sekmeler ---
function sekmeAc(ad) {
  $$(".sekmeler button").forEach((b) => b.classList.toggle("secili", b.dataset.sayfa === ad));
  $("#sayfa-uret").hidden = ad !== "uret";
  $("#sayfa-galeri").hidden = ad !== "galeri";
  $("#sayfa-kota").hidden = ad !== "kota";
  $("#sayfa-arastir").hidden = ad !== "arastir";
  $("#uret").hidden = ad !== "uret";
  $("#ar-yazi").hidden = ad !== "arastir";
  if (ad === "arastir") arastirAc();
  if (ad === "galeri") galeriCiz();
  if (ad === "kota") kotaYukle();
  window.scrollTo(0, 0);
}
$$(".sekmeler button").forEach((b) => b.addEventListener("click", () => sekmeAc(b.dataset.sayfa)));

// --- Sunucu (GPU) durumu: seçili türün GPU'su gösterilir ---
function sunucuCiz() {
  const t = ayar.tur;
  const s = $("#sunucu");
  const durum = isiniyor[t] || uretiliyor ? "isiniyor" : Date.now() - sonGpu[t] < GPU_UYKU_MS ? "hazir" : "uyku";
  s.className = "sunucu " + durum;
  s.querySelector("b").textContent = { isiniyor: "Hazırlanıyor", hazir: "Hazır", uyku: "Uykuda" }[durum];
}
setInterval(sunucuCiz, 5000);

async function isit() {
  const t = ayar.tur;
  if (isiniyor[t] || uretiliyor || Date.now() - sonGpu[t] < GPU_UYKU_MS) return;
  isiniyor[t] = true;
  sunucuCiz();
  try {
    const d = await api("isit", { tur: t });
    await isBekle(d.is);
    sonGpu[t] = Date.now();
  } catch {}
  isiniyor[t] = false;
  sunucuCiz();
}
// Yazmaya başlayınca GPU arka planda uyanır; Üret'e basınca beklemezsin.
$("#prompt").addEventListener("focus", isit);

// --- Üretim ---
function onizlemeOlustur(kayit) {
  const b = document.createElement("button");
  b.className = "sonuc";
  b.style.aspectRatio = ORANLAR[kayit.tur][kayit.boyut];
  const o = { kayit, url: URL.createObjectURL(kayit.blob), el: b };
  if (kayit.tur === "video") {
    const v = document.createElement("video");
    v.src = o.url;
    v.muted = true;
    v.loop = true;
    v.autoplay = true;
    v.playsInline = true;
    b.appendChild(v);
  } else {
    const img = document.createElement("img");
    img.src = o.url;
    img.alt = kayit.prompt;
    b.appendChild(img);
  }
  sonucOgeleri.push(o);
  dokunmaKur(b, () => goruntule(sonucOgeleri, sonucOgeleri.indexOf(o)), () => menuAc(o));
  return b;
}

$("#uret").addEventListener("click", async () => {
  const prompt = $("#prompt").value.trim();
  if (!prompt) { bildir("Önce ne görmek istediğini yaz."); $("#prompt").focus(); return; }
  if (uretiliyor) return;
  const video = ayar.tur === "video";
  const tohumMetin = $("#tohum").value.trim();
  const istek = {
    tur: ayar.tur, prompt, mod: ayar.mod, boyut: ayar.boyut,
    adet: video ? 1 : ayar.adet, kalite: ayar.kalite, sure: ayar.sure,
    tohum: tohumMetin === "" ? null : Number(tohumMetin),
  };

  uretiliyor = true;
  sunucuCiz();
  $("#uret").disabled = true;
  const sonuclar = $("#sonuclar");
  sonuclar.className = "sonuclar" + (istek.adet === 1 ? " tek" : "");
  sonuclar.innerHTML = "";
  for (let i = 0; i < istek.adet; i++) {
    const d = document.createElement("div");
    d.className = "iskelet";
    d.style.aspectRatio = ORANLAR[istek.tur][istek.boyut];
    sonuclar.appendChild(d);
  }
  const durum = $("#durum");
  durum.hidden = false;
  const soguk = Date.now() - sonGpu[istek.tur] > GPU_UYKU_MS;
  const bas = Date.now();
  const sayac = setInterval(() => {
    const sn = Math.round((Date.now() - bas) / 1000);
    const sure = sn < 60 ? `${sn} sn` : `${Math.floor(sn / 60)} dk ${sn % 60} sn`;
    if (video) durum.textContent = `Video üretiliyor (genelde 2-5 dk${soguk ? ", ilk seferde daha uzun" : ""})… ${sure}`;
    else durum.textContent = soguk && sn > 8 ? `Sunucu uyanıyor, ilk seferde 1-3 dk sürebilir… ${sure}` : `Üretiliyor… ${sure}`;
  }, 500);
  durum.textContent = "Başlatılıyor…";
  sonuclar.scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    if (video && baslangicResim) istek.resim = await resimBase64(baslangicResim);
    const baslangic = await api("uret", istek);
    const d = { ...baslangic, ...(await isBekle(baslangic.is, video ? 30 : 10)) };
    sonGpu[istek.tur] = Date.now();
    sonuclar.innerHTML = "";
    sonucOgeleri.forEach((o) => URL.revokeObjectURL(o.url));
    sonucOgeleri = [];
    const ortak = {
      tur: istek.tur, prompt, mod: istek.mod, boyut: istek.boyut, kalite: istek.kalite,
      genislik: d.genislik, yukseklik: d.yukseklik, tarih: Date.now(),
    };
    const kayitlar = video
      ? [{ ...ortak, id: Date.now() + "-v" + d.tohum, blob: base64Blob(d.video, "video/mp4"), tohum: d.tohum,
           sure: istek.sure, fotodan: !!istek.resim, gelismisPrompt: d.gelismis_prompt || null }]
      : d.gorseller.map((g) => ({ ...ortak, id: Date.now() + "-" + g.tohum, blob: base64Blob(g.veri, "image/jpeg"), tohum: g.tohum }));
    for (const k of kayitlar) {
      await galeriEkle(k);
      sonuclar.appendChild(onizlemeOlustur(k));
    }
    durum.hidden = true;
    bildir(`Hazır (${Math.round((Date.now() - bas) / 1000)} sn). Galeriye kaydedildi.`);
  } catch (err) {
    sonuclar.innerHTML = "";
    durum.hidden = true;
    bildir("Olmadı: " + err.message);
  } finally {
    clearInterval(sayac);
    uretiliyor = false;
    $("#uret").disabled = false;
    sunucuCiz();
  }
});

// --- Galeri ---
// Her öğe: { kayit, url, el } – aynı kayıt hem sonuçlarda hem galeride ayrı öğe olabilir.
let galeriOgeleri = [];
let sonucOgeleri = [];

async function galeriCiz() {
  galeriOgeleri.forEach((o) => URL.revokeObjectURL(o.url));
  const kayitlar = (await galeriHepsi()).sort((a, b) => b.tarih - a.tarih);
  const kutu = $("#galeri");
  kutu.innerHTML = "";
  $("#galeri-bos").hidden = kayitlar.length > 0;
  galeriOgeleri = kayitlar.map((k) => ({ kayit: k, url: URL.createObjectURL(k.blob) }));
  galeriOgeleri.forEach((o, i) => {
    const b = document.createElement("button");
    if (o.kayit.tur === "video") {
      const v = document.createElement("video");
      v.src = o.url + "#t=0.1";
      v.muted = true;
      v.playsInline = true;
      v.preload = "metadata";
      b.appendChild(v);
      const r = document.createElement("span");
      r.className = "rozet";
      r.textContent = "▶";
      b.appendChild(r);
    } else {
      const img = document.createElement("img");
      img.src = o.url;
      img.alt = o.kayit.prompt;
      img.loading = "lazy";
      b.appendChild(img);
    }
    dokunmaKur(b, () => goruntule(galeriOgeleri, galeriOgeleri.indexOf(o)), () => menuAc(o));
    o.el = b;
    kutu.appendChild(b);
  });
}

// --- Kısa dokunuş / uzun basış ---
function dokunmaKur(el, kisa, uzun) {
  let zaman = null;
  let uzunOldu = false;
  let bx = 0;
  let by = 0;
  const iptal = () => { clearTimeout(zaman); zaman = null; };
  el.addEventListener("pointerdown", (e) => {
    if (!e.isPrimary) { iptal(); return; }
    uzunOldu = false;
    bx = e.clientX;
    by = e.clientY;
    iptal();
    zaman = setTimeout(() => {
      zaman = null;
      uzunOldu = true;
      if (navigator.vibrate) navigator.vibrate(15);
      uzun();
    }, 500);
  });
  el.addEventListener("pointermove", (e) => {
    if (zaman && Math.hypot(e.clientX - bx, e.clientY - by) > 10) iptal();
  });
  el.addEventListener("pointerup", iptal);
  el.addEventListener("pointercancel", iptal);
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("click", (e) => {
    if (uzunOldu) { e.preventDefault(); uzunOldu = false; return; }
    if (kisa) kisa();
  });
}

// --- Katmanlar ve geri tuşu ---
// Görüntüleyici ve menü history'ye kayıt ekler; Android geri tuşu en üstteki katmanı kapatır.
const katman = { g: false, m: false };
function katmanEkle(ad) {
  katman[ad] = true;
  history.pushState({ g: katman.g, m: katman.m }, "");
}
function geri(adim = 1) {
  return new Promise((tamam) => {
    window.addEventListener("popstate", () => tamam(), { once: true });
    history.go(-adim);
  });
}
window.addEventListener("popstate", (e) => {
  const durum = e.state || {};
  if (katman.m && !durum.m) menuKapat();
  if (katman.g && !durum.g) goruntuleyiciKapat();
});

// --- Tam ekran görüntüleyici: yalnızca resim / video, kaydırarak gezinme ---
const gz = { liste: [], idx: 0 };

function goruntule(liste, idx) {
  gz.liste = liste;
  gz.idx = idx;
  $("#bildirim").hidden = true;
  const serit = $("#g-serit");
  serit.innerHTML = "";
  serit.style.transform = "";
  serit.style.opacity = "";
  liste.forEach((o) => {
    const s = document.createElement("div");
    s.className = "slayt";
    dokunmaKur(s, () => slaytDokun(s), () => menuAc(o));
    serit.appendChild(s);
  });
  $("#goruntuleyici").hidden = false;
  katmanEkle("g");
  try {
    const p = document.documentElement.requestFullscreen?.({ navigationUI: "hide" });
    if (p) p.catch(() => {});
  } catch {}
  requestAnimationFrame(() => {
    serit.scrollLeft = idx * serit.clientWidth;
    slaytlariGuncelle();
  });
  ipucuGoster();
}

// Yalnızca açık slayt ve komşuları medya taşır; uzaktakiler boşaltılır (bellek için).
function slaytlariGuncelle() {
  [...$("#g-serit").children].forEach((s, i) => {
    const o = gz.liste[i];
    const yakin = Math.abs(i - gz.idx) <= 1;
    if (yakin && !s.firstChild) {
      if (o.kayit.tur === "video") {
        const v = document.createElement("video");
        v.src = o.url;
        v.loop = true;
        v.playsInline = true;
        v.preload = "auto";
        s.appendChild(v);
      } else {
        const img = document.createElement("img");
        img.src = o.url;
        img.alt = "";
        s.appendChild(img);
      }
    } else if (!yakin && s.firstChild) {
      const v = s.querySelector("video");
      if (v) v.pause();
      s.innerHTML = "";
      s.classList.remove("duraklatildi");
    }
    const v = s.querySelector("video");
    if (v) {
      if (i === gz.idx) { s.classList.remove("duraklatildi"); v.play().catch(() => s.classList.add("duraklatildi")); }
      else { v.pause(); v.currentTime = 0; }
    }
  });
}

function slaytDokun(s) {
  const v = s.querySelector("video");
  if (!v) return;
  if (v.paused) { v.play().catch(() => {}); s.classList.remove("duraklatildi"); }
  else { v.pause(); s.classList.add("duraklatildi"); }
}

let seritCerceve = 0;
$("#g-serit").addEventListener("scroll", () => {
  cancelAnimationFrame(seritCerceve);
  seritCerceve = requestAnimationFrame(() => {
    const serit = $("#g-serit");
    const i = Math.round(serit.scrollLeft / serit.clientWidth);
    if (i !== gz.idx && i >= 0 && i < gz.liste.length) {
      zoomSifirla(false);
      gz.idx = i;
      slaytlariGuncelle();
    }
  });
});

// Aşağı çekince kapanır.
(() => {
  const serit = $("#g-serit");
  let bx = 0;
  let by = 0;
  let dikey = false;
  serit.addEventListener("touchstart", (e) => {
    if (e.touches.length > 1 || zoom.olcek > 1) { dikey = false; by = Infinity; return; }
    bx = e.touches[0].clientX;
    by = e.touches[0].clientY;
    dikey = false;
  }, { passive: true });
  serit.addEventListener("touchmove", (e) => {
    const dx = e.touches[0].clientX - bx;
    const dy = e.touches[0].clientY - by;
    if (e.touches.length > 1 || zoom.olcek > 1 || !Number.isFinite(dy)) return;
    if (!dikey && dy > 12 && dy > Math.abs(dx) * 1.5) dikey = true;
    if (dikey) {
      serit.style.transition = "none";
      serit.style.transform = `translateY(${Math.max(0, dy)}px)`;
      serit.style.opacity = String(Math.max(0.3, 1 - dy / 500));
    }
  }, { passive: true });
  serit.addEventListener("touchend", (e) => {
    if (!dikey) return;
    const dy = e.changedTouches[0].clientY - by;
    serit.style.transition = "";
    serit.style.transform = "";
    serit.style.opacity = "";
    if (dy > 110) geri();
  });
})();

function ipucuGoster() {
  if (depo.al("ipucu-goruldu")) return;
  depo.koy("ipucu-goruldu", "1");
  const ip = $("#g-ipucu");
  ip.hidden = false;
  setTimeout(() => (ip.hidden = true), 3000);
}

function goruntuleyiciKapat() {
  zoomSifirla(false);
  katman.g = false;
  $$("#g-serit video").forEach((v) => v.pause());
  $("#g-serit").innerHTML = "";
  $("#g-ipucu").hidden = true;
  $("#goruntuleyici").hidden = true;
  gz.liste = [];
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
// Tam ekrandayken Android geri tuşu önce tam ekrandan çıkar; o an görüntüleyiciyi de kapat.
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && katman.g && !katman.m) geri();
});

// --- Uzun basma menüsü ---
let menuOge = null;
function menuAc(o) {
  menuOge = o;
  $("#bildirim").hidden = true;
  const k = o.kayit;
  const video = k.tur === "video";
  $('#menu [data-is="canlandir"]').hidden = video;
  $("#menu-bilgi").hidden = true;
  $("#m-prompt").textContent = k.prompt;
  const mod = modlar.find((m) => m.id === k.mod);
  $("#m-detay").textContent = [
    video ? `Video · ${k.sure} sn${k.fotodan ? " · fotoğraftan" : ""}` : "Fotoğraf",
    mod ? mod.ad : k.mod, BOYUT_ADI[k.boyut], `${k.genislik}×${k.yukseklik}`, `Tohum ${k.tohum}`,
    new Date(k.tarih).toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" }),
  ].filter(Boolean).join(" · ");
  const gelismis = video && k.gelismisPrompt && k.gelismisPrompt !== k.prompt;
  $("#m-gelismis-kutu").hidden = !gelismis;
  $("#m-gelismis").textContent = gelismis ? k.gelismisPrompt : "";
  $("#menu").hidden = false;
  katmanEkle("m");
}
function menuKapat() {
  katman.m = false;
  $("#menu").hidden = true;
  menuOge = null;
}
$("#menu-arka").addEventListener("click", () => geri());

const dosyaAdi = (k) => `foto-studyo-${k.tohum}.${k.tur === "video" ? "mp4" : "jpg"}`;

// Görüntüleyici ve menüden çıkıp Üret sayfasına dönerken açık katmanları kapat.
async function hepsiniKapat() {
  const adim = (katman.g ? 1 : 0) + (katman.m ? 1 : 0);
  if (adim) await geri(adim);
}

async function ogeSil(o) {
  if (!confirm(o.kayit.tur === "video" ? "Bu video silinsin mi?" : "Bu fotoğraf silinsin mi?")) return;
  const id = o.kayit.id;
  await galeriSil(id);
  for (const liste of [galeriOgeleri, sonucOgeleri]) {
    for (let i = liste.length - 1; i >= 0; i--) {
      if (liste[i].kayit.id !== id) continue;
      if (liste[i].el) liste[i].el.remove();
      URL.revokeObjectURL(liste[i].url);
      liste.splice(i, 1);
    }
  }
  $("#galeri-bos").hidden = galeriOgeleri.length > 0;
  if (katman.m) await geri();
  if (katman.g) {
    // Görüntüleyicideki slaytı da kaldır; liste boşaldıysa kapat.
    const serit = $("#g-serit");
    const slayt = serit.children[gz.idx];
    if (gz.liste.length === 0) { await geri(); return; }
    if (slayt && !gz.liste.some((x) => x.kayit.id === id)) {
      const v = slayt.querySelector("video");
      if (v) v.pause();
      slayt.remove();
      gz.idx = Math.min(gz.idx, gz.liste.length - 1);
      serit.scrollLeft = gz.idx * serit.clientWidth;
      slaytlariGuncelle();
    }
  }
  bildir("Silindi.");
}

$$("#menu [data-is]").forEach((b) => b.addEventListener("click", async () => {
  const o = menuOge;
  if (!o) return;
  const k = o.kayit;
  const is = b.dataset.is;
  if (is === "bilgi") {
    $("#menu-bilgi").hidden = !$("#menu-bilgi").hidden;
    return;
  }
  if (is === "kaydet") {
    const a = document.createElement("a");
    a.href = o.url;
    a.download = dosyaAdi(k);
    document.body.appendChild(a);
    a.click();
    a.remove();
    await geri();
    bildir("İndiriliyor: telefonun İndirilenler klasörüne kaydedilir.");
  } else if (is === "paylas") {
    const dosya = new File([k.blob], dosyaAdi(k), { type: k.blob.type });
    if (navigator.canShare && navigator.canShare({ files: [dosya] })) {
      await geri();
      try { await navigator.share({ files: [dosya] }); } catch {}
    } else {
      bildir("Bu cihaz paylaşımı desteklemiyor; Kaydet'i kullan.");
    }
  } else if (is === "sil") {
    await ogeSil(o);
  } else if (is === "canlandir") {
    await hepsiniKapat();
    baslangicAyarla(k.blob);
    turSec("video");
    segmentSec("boyut", "boyut", k.boyut === "duvar" ? "dikey" : k.boyut);
    modSec("serbest"); // stil zaten fotoğrafta var
    $("#prompt").value = k.prompt;
    $("#tohum").value = "";
    sekmeAc("uret");
    bildir("Fotoğraf eklendi. Nasıl hareket etsin, açıklamaya ekle ve Video üret'e bas.");
  } else if (is === "tekrar") {
    await hepsiniKapat();
    turSec(k.tur || "foto");
    $("#prompt").value = k.prompt;
    $("#tohum").value = k.tohum;
    $(".gelismis").open = true;
    modSec(k.mod);
    segmentSec("boyut", "boyut", k.boyut);
    segmentSec("kalite", "kalite", k.kalite);
    segmentSec("adet", "adet", 1);
    if (k.sure) segmentSec("sure", "sure", k.sure);
    if (k.tur !== "video") baslangicAyarla(null);
    sekmeAc("uret");
    bildir("Ayarlar yüklendi. Açıklamayı değiştirip tekrar üretebilirsin.");
  }
}));

// --- İki parmakla yakınlaştırma, sürükleme, çift dokunuşla yakınlaştırma ---
// Medya transform'u: translate(x, y) scale(olcek), merkez noktası etrafında.
const zoom = { olcek: 1, x: 0, y: 0, el: null };

function aktifMedya() {
  const s = $("#g-serit").children[gz.idx];
  return s ? s.querySelector("img, video") : null;
}
function zoomUygula(gecis) {
  if (!zoom.el) return;
  zoom.el.style.transition = gecis ? "transform .2s" : "none";
  zoom.el.style.transform = `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.olcek})`;
  $("#g-serit").classList.toggle("yakin", zoom.olcek > 1);
}
function zoomSifirla(gecis = true) {
  zoom.olcek = 1;
  zoom.x = 0;
  zoom.y = 0;
  if (zoom.el) {
    zoom.el.style.transition = gecis ? "transform .2s" : "none";
    zoom.el.style.transform = "";
  }
  zoom.el = null;
  $("#g-serit").classList.remove("yakin");
}
// Resim ekran dışına kaçmasın: büyütülmüş boyut kadar kaydırmaya izin ver.
function zoomSinirla() {
  const el = zoom.el;
  const kap = $("#g-serit");
  const mx = Math.max(0, (el.offsetWidth * zoom.olcek - kap.clientWidth) / 2);
  const my = Math.max(0, (el.offsetHeight * zoom.olcek - kap.clientHeight) / 2);
  zoom.x = Math.min(mx, Math.max(-mx, zoom.x));
  zoom.y = Math.min(my, Math.max(-my, zoom.y));
}
// (px, py) ekran noktası yerinde kalacak şekilde ölçeği değiştir.
function zoomNoktaya(yeniOlcek, px, py, bas) {
  const kap = $("#g-serit").getBoundingClientRect();
  const cx = kap.left + kap.width / 2;
  const cy = kap.top + kap.height / 2;
  const qx = (px - cx - bas.x) / bas.olcek;
  const qy = (py - cy - bas.y) / bas.olcek;
  zoom.olcek = Math.min(5, Math.max(1, yeniOlcek));
  zoom.x = px - cx - zoom.olcek * qx;
  zoom.y = py - cy - zoom.olcek * qy;
  zoomSinirla();
}

(() => {
  const serit = $("#g-serit");
  let mod = null; // "sikistir" | "surukle"
  let bas = null;
  let sonDokunus = { t: 0, x: 0, y: 0 };
  const mesafe = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  const orta = (a, b) => ({ x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 });

  serit.addEventListener("touchstart", (e) => {
    if (e.touches.length === 2) {
      const el = aktifMedya();
      if (!el) return;
      if (zoom.el !== el) { zoomSifirla(false); zoom.el = el; }
      mod = "sikistir";
      bas = { olcek: zoom.olcek, x: zoom.x, y: zoom.y, d: mesafe(e.touches[0], e.touches[1]), m: orta(e.touches[0], e.touches[1]) };
      serit.classList.add("yakin"); // iki parmak varken sayfa kaymasın
    } else if (e.touches.length === 1 && zoom.olcek > 1) {
      mod = "surukle";
      bas = { x: zoom.x, y: zoom.y, px: e.touches[0].clientX, py: e.touches[0].clientY };
    }
  }, { passive: true });

  serit.addEventListener("touchmove", (e) => {
    if (mod === "sikistir" && e.touches.length === 2) {
      e.preventDefault();
      const m = orta(e.touches[0], e.touches[1]);
      const yeni = bas.olcek * (mesafe(e.touches[0], e.touches[1]) / bas.d);
      zoomNoktaya(yeni, bas.m.x, bas.m.y, bas);
      // İki parmağın birlikte kayması resmi de kaydırır.
      zoom.x += m.x - bas.m.x;
      zoom.y += m.y - bas.m.y;
      zoomSinirla();
      zoomUygula(false);
    } else if (mod === "surukle" && e.touches.length === 1) {
      e.preventDefault();
      zoom.x = bas.x + e.touches[0].clientX - bas.px;
      zoom.y = bas.y + e.touches[0].clientY - bas.py;
      zoomSinirla();
      zoomUygula(false);
    }
  }, { passive: false });

  serit.addEventListener("touchend", (e) => {
    if (mod === "sikistir" && e.touches.length < 2) {
      if (zoom.olcek < 1.05) zoomSifirla();
      else zoomUygula(true);
      // Bir parmak kaldıysa sürüklemeye devam et.
      mod = e.touches.length === 1 && zoom.olcek > 1 ? "surukle" : null;
      if (mod) bas = { x: zoom.x, y: zoom.y, px: e.touches[0].clientX, py: e.touches[0].clientY };
      return;
    }
    if (mod === "surukle" && e.touches.length === 0) mod = null;

    // Çift dokunuş: yakınlaştır / geri al (yalnızca fotoğraflarda; videoda dokunuş oynat/durdur).
    if (e.touches.length || e.changedTouches.length !== 1) return;
    const t = e.changedTouches[0];
    const simdi = Date.now();
    const cift = simdi - sonDokunus.t < 300 && Math.hypot(t.clientX - sonDokunus.x, t.clientY - sonDokunus.y) < 30;
    sonDokunus = { t: cift ? 0 : simdi, x: t.clientX, y: t.clientY };
    const el = aktifMedya();
    if (!cift || !el || el.tagName !== "IMG") return;
    if (zoom.olcek > 1) { zoomUygula(true); zoomSifirla(); return; }
    zoom.el = el;
    zoomNoktaya(2.5, t.clientX, t.clientY, { olcek: 1, x: 0, y: 0 });
    zoomUygula(true);
  });
})();

// --- Kota ---
const para = (n) => new Intl.NumberFormat("tr-TR", { style: "currency", currency: "USD" }).format(n);
const KAYNAK_ADI = {
  H200: "Video GPU (H200)",
  L40S: "Fotoğraf GPU (L40S)",
  CPU: "İşlemci",
  Memory: "Bellek",
  "Network Egress": "İnternet trafiği",
};

async function kotaYukle() {
  const durum = $("#kota-durum");
  durum.textContent = "Yükleniyor…";
  $("#kota-yenile").disabled = true;
  try {
    const k = await api("kota");
    const kalan = Math.max(0, k.kredi - k.kullanilan);
    const oran = Math.min(1, k.kullanilan / k.kredi);
    $("#kota-kalan").textContent = para(kalan);
    $("#kota-dolu").style.width = (oran * 100).toFixed(1) + "%";
    const olcer = $("#kota-olcer");
    olcer.setAttribute("aria-valuemax", String(k.kredi));
    olcer.setAttribute("aria-valuenow", k.kullanilan.toFixed(2));
    $("#kota-kullanilan").textContent = `${para(k.kullanilan)} / ${para(k.kredi)}`;
    const bugun = new Date().toISOString().slice(0, 10);
    $("#kota-bugun").textContent = para(k.gunluk[bugun] || 0);
    $("#kota-odenecek").textContent = para(k.odenecek);
    $("#kota-yenilenme").textContent = k.donem_bitis
      ? new Date(k.donem_bitis).toLocaleDateString("tr-TR", { day: "numeric", month: "long" })
      : "Ay başında";
    const uyari = $("#kota-uyari");
    uyari.hidden = oran < 0.8;
    if (oran >= 1) uyari.textContent = "⚠ Ücretsiz kredi bitti. Harcama limitin varsa üretim durabilir.";
    else uyari.textContent = `⚠ Ücretsiz kredinin %${Math.round(oran * 100)}'i kullanıldı.`;

    const satirlar = Object.entries(k.kaynaklar).filter(([, v]) => v >= 0.005).sort((a, b) => b[1] - a[1]);
    const enBuyuk = satirlar.length ? satirlar[0][1] : 1;
    const kutu = $("#kota-dagilim");
    kutu.innerHTML = "";
    if (!satirlar.length) kutu.innerHTML = '<p class="soluk" style="margin:0">Bu ay henüz harcama yok.</p>';
    for (const [ad, tutar] of satirlar) {
      const d = document.createElement("div");
      d.className = "dagilim-satir";
      d.innerHTML = '<span></span><span></span><div class="dagilim-cubuk"><i></i></div>';
      d.children[0].textContent = KAYNAK_ADI[ad] || ad;
      d.children[1].textContent = para(tutar);
      d.querySelector("i").style.width = ((tutar / enBuyuk) * 100).toFixed(1) + "%";
      kutu.appendChild(d);
    }
    $("#kota-icerik").hidden = false;
    durum.textContent = "";
  } catch (err) {
    durum.textContent = "Kota okunamadı: " + err.message;
  } finally {
    $("#kota-yenile").disabled = false;
  }
}
$("#kota-yenile").addEventListener("click", kotaYukle);

// --- Araştırma asistanı ---
const ar = { tur: "soru", hedef: "foto", sohbet: [], calisiyor: false };
const AR_IPUCU = {
  soru: "Bir soru sor…",
  arastirma: "Araştırılacak konuyu yaz… (1-4 dk sürebilir)",
  prompt: "Fikrini Türkçe yaz, İngilizce prompt hazırlayayım…",
};
const AR_ETIKET = { soru: "Soru", arastirma: "Derin araştırma", prompt: "Prompt" };

try { ar.sohbet = JSON.parse(depo.al("arastirma-sohbet") || "[]"); } catch { ar.sohbet = []; }
const arKaydet = () => depo.koy("arastirma-sohbet", JSON.stringify(ar.sohbet.slice(-60)));

function arSegment(id, anahtar) {
  $$(`#${id} button`).forEach((b) => b.addEventListener("click", () => {
    ar[anahtar] = b.dataset.v;
    $$(`#${id} button`).forEach((x) => x.classList.toggle("secili", x === b));
    arAyarGuncelle();
  }));
}
arSegment("ar-tur", "tur");
arSegment("ar-hedef", "hedef");

function arAyarGuncelle() {
  $("#ar-hedef").hidden = ar.tur !== "prompt";
  $("#ar-girdi").placeholder = AR_IPUCU[ar.tur];
}

let arAcildi = false;
function arastirAc() {
  if (!arAcildi) { arAcildi = true; arCiz(); }
  arSayacGuncelle();
}

async function arSayacGuncelle(bugun) {
  try {
    const d = bugun === undefined ? await api("arastirma-sayac") : { bugun, limit: 50 };
    $("#ar-sayac").textContent = `Bugün ${d.bugun}/${d.limit}`;
    $("#ar-sayac").title = "Bugün kullanılan ücretsiz model isteği";
  } catch {}
}

// Güvenli, küçük bir Markdown dönüştürücü: önce her şey kaçırılır, sonra izin verilen biçimler eklenir.
function kacir(t) {
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function satirIci(t, kaynakSayisi) {
  return t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>')
    .replace(/\[(\d{1,3})\]/g, (m, n) => (n >= 1 && n <= kaynakSayisi ? `<a class="atif" href="#" data-n="${n}">[${n}]</a>` : m));
}
function md(metin, kaynakSayisi = 0) {
  const satirlar = kacir(metin).split("\n");
  const html = [];
  let i = 0;
  while (i < satirlar.length) {
    const s = satirlar[i];
    if (/^```/.test(s)) {
      const kod = [];
      i++;
      while (i < satirlar.length && !/^```/.test(satirlar[i])) kod.push(satirlar[i++]);
      i++;
      html.push(`<pre><code>${kod.join("\n")}</code></pre>`);
      continue;
    }
    const b = s.match(/^(#{1,4})\s+(.*)/);
    if (b) { const n = Math.min(3, b[1].length); html.push(`<h${n}>${satirIci(b[2], kaynakSayisi)}</h${n}>`); i++; continue; }
    if (/^\s*\|/.test(s)) {
      const tablo = [];
      while (i < satirlar.length && /^\s*\|/.test(satirlar[i])) tablo.push(satirlar[i++]);
      const hucre = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((h) => satirIci(h.trim(), kaynakSayisi));
      const satir = tablo.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r));
      const [bas, ...govde] = satir;
      html.push('<div class="tablo"><table><thead><tr>' + hucre(bas).map((h) => `<th>${h}</th>`).join("") + "</tr></thead><tbody>" +
        govde.map((r) => "<tr>" + hucre(r).map((h) => `<td>${h}</td>`).join("") + "</tr>").join("") + "</tbody></table></div>");
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(s)) {
      const sirali = /^\s*\d+[.)]/.test(s);
      const maddeler = [];
      while (i < satirlar.length && /^\s*([-*•]|\d+[.)])\s+/.test(satirlar[i])) {
        maddeler.push(satirlar[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      }
      const e = sirali ? "ol" : "ul";
      html.push(`<${e}>` + maddeler.map((m) => `<li>${satirIci(m, kaynakSayisi)}</li>`).join("") + `</${e}>`);
      continue;
    }
    if (!s.trim()) { i++; continue; }
    const p = [];
    while (i < satirlar.length && satirlar[i].trim() && !/^(#{1,4}\s|```|\s*\||\s*([-*•]|\d+[.)])\s)/.test(satirlar[i])) p.push(satirlar[i++]);
    html.push(`<p>${satirIci(p.join("<br>"), kaynakSayisi)}</p>`);
  }
  return html.join("");
}

function arBalon(m) {
  const d = document.createElement("div");
  if (m.rol === "kullanici") {
    d.className = "balon kullanici";
    d.textContent = m.metin;
    return d;
  }
  d.className = "balon asistan" + (m.hata ? " hata" : "");
  const etiket = document.createElement("div");
  etiket.className = "etiketcik";
  etiket.textContent = AR_ETIKET[m.tur] + (m.tur === "prompt" ? (m.hedef === "video" ? " · Video" : " · Fotoğraf") : "");
  d.appendChild(etiket);
  const icerik = document.createElement("div");
  icerik.className = "md";
  if (m.hata) icerik.textContent = m.hata;
  else if (m.tur === "prompt") icerik.textContent = m.metin;
  else icerik.innerHTML = md(m.metin, (m.kaynaklar || []).length);
  d.appendChild(icerik);

  const kaynaklar = m.kaynaklar || [];
  if (kaynaklar.length) {
    const det = document.createElement("details");
    det.className = "kaynaklar";
    det.innerHTML = `<summary>Kaynaklar (${kaynaklar.length})</summary><ol></ol>`;
    for (const k of kaynaklar) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = k.url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = k.baslik || k.url;
      li.appendChild(a);
      det.querySelector("ol").appendChild(li);
    }
    d.appendChild(det);
    // Metindeki [n] atıfına dokununca kaynağı aç
    icerik.addEventListener("click", (e) => {
      const at = e.target.closest(".atif");
      if (!at) return;
      e.preventDefault();
      const k = kaynaklar[Number(at.dataset.n) - 1];
      if (k) window.open(k.url, "_blank", "noopener");
    });
  }

  if (!m.hata) {
    const alt = document.createElement("div");
    alt.className = "balon-alt";
    const bilgi = document.createElement("span");
    bilgi.className = "soluk";
    bilgi.textContent = (m.model || "").replace(/:free$/, "").split("/").pop();
    alt.appendChild(bilgi);
    const kopya = document.createElement("button");
    kopya.className = "kucuk-buton";
    kopya.innerHTML = '<svg><use href="#i-kopyala"/></svg>Kopyala';
    kopya.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(m.metin); bildir("Kopyalandı."); } catch { bildir("Kopyalanamadı."); }
    });
    alt.appendChild(kopya);
    if (m.tur === "prompt") {
      const gonder = document.createElement("button");
      gonder.className = "kucuk-buton vurgulu";
      gonder.innerHTML = '<svg><use href="#i-parilti"/></svg>Üret\'e gönder';
      gonder.addEventListener("click", () => {
        turSec(m.hedef === "video" ? "video" : "foto");
        if (m.hedef !== "video") modSec("serbest");
        $("#prompt").value = m.metin;
        sekmeAc("uret");
        bildir("Prompt Üret ekranına yerleştirildi.");
      });
      alt.appendChild(gonder);
    }
    d.appendChild(alt);
  }
  return d;
}

function arCiz() {
  const kutu = $("#ar-mesajlar");
  kutu.innerHTML = "";
  ar.sohbet.forEach((m) => kutu.appendChild(arBalon(m)));
  $("#ar-bos").hidden = ar.sohbet.length > 0;
}

function asagiKaydir() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

async function arGonder() {
  const metin = $("#ar-girdi").value.trim();
  if (!metin || ar.calisiyor) return;
  ar.calisiyor = true;
  $("#ar-gonder").disabled = true;
  $("#ar-girdi").value = "";
  girdiBoyutla();
  const tur = ar.tur;
  const hedef = ar.hedef;
  ar.sohbet.push({ rol: "kullanici", metin, tur });
  arKaydet();
  arCiz();

  // Bekleme balonu: sunucudan gelen adımlar ve geçen süre
  const bekle = document.createElement("div");
  bekle.className = "balon asistan bekliyor";
  bekle.innerHTML = `<div class="etiketcik">${AR_ETIKET[tur]}</div><ul class="adimlar"><li>⏳ Başlatılıyor</li></ul><div class="sure"></div>`;
  $("#ar-mesajlar").appendChild(bekle);
  asagiKaydir();
  const bas = Date.now();
  const sayac = setInterval(() => {
    const sn = Math.round((Date.now() - bas) / 1000);
    bekle.querySelector(".sure").textContent = sn < 60 ? `${sn} sn` : `${Math.floor(sn / 60)} dk ${sn % 60} sn`;
  }, 1000);

  try {
    // Prompt modunda yalnızca son fikir; diğerlerinde sohbet geçmişi (hatalar hariç) gönderilir.
    const gecmis = tur === "prompt"
      ? [{ rol: "kullanici", metin }]
      : ar.sohbet.filter((m) => !m.hata && m.tur !== "prompt").map((m) => ({ rol: m.rol, metin: m.metin })).slice(-12);
    const b = await api("arastir", { tur, hedef, mesajlar: gecmis });
    const d = await isBekle(b.is, 15, (adimlar) => {
      bekle.querySelector(".adimlar").innerHTML = adimlar.map((a) => `<li>${kacir(a)}</li>`).join("");
    });
    if (d.hata) {
      ar.sohbet.push({ rol: "asistan", tur, hata: d.hata, metin: "" });
    } else {
      ar.sohbet.push({ rol: "asistan", tur, hedef, metin: d.cevap || "(boş cevap)", kaynaklar: d.kaynaklar || [], model: d.model });
    }
    if (d.bugun_istek !== undefined) arSayacGuncelle(d.bugun_istek);
  } catch (err) {
    ar.sohbet.push({ rol: "asistan", tur, hata: "Olmadı: " + err.message, metin: "" });
  } finally {
    clearInterval(sayac);
    ar.calisiyor = false;
    $("#ar-gonder").disabled = false;
    arKaydet();
    arCiz();
    asagiKaydir();
  }
}
$("#ar-gonder").addEventListener("click", arGonder);

function girdiBoyutla() {
  const g = $("#ar-girdi");
  g.style.height = "auto";
  g.style.height = Math.min(160, g.scrollHeight + 2) + "px";
}
$("#ar-girdi").addEventListener("input", girdiBoyutla);

$("#ar-yeni").addEventListener("click", () => {
  if (ar.calisiyor) return;
  if (ar.sohbet.length && !confirm("Yeni sohbet başlatılsın mı? Bu sohbet silinir.")) return;
  ar.sohbet = [];
  arKaydet();
  arCiz();
});
arAyarGuncelle();

// --- Uygulama olarak yükleme (Android "Ana ekrana ekle") ---
let yuklemeIstemi = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  yuklemeIstemi = e;
  $("#yukle").hidden = false;
});
$("#yukle").addEventListener("click", async () => {
  if (!yuklemeIstemi) return;
  yuklemeIstemi.prompt();
  await yuklemeIstemi.userChoice;
  yuklemeIstemi = null;
  $("#yukle").hidden = true;
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

ekranGoster();
turGuncelle();
