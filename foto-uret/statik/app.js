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
async function isBekle(kimlik, sinirDk = 10) {
  const bas = Date.now();
  while (Date.now() - bas < sinirDk * 60 * 1000) {
    const d = await api("is/" + encodeURIComponent(kimlik));
    if (d.bitti) return d;
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
  video: { kare: "1 / 1", dikey: "2 / 3", yatay: "3 / 2" },
};
const BOYUT_ADI = { kare: "Kare", dikey: "Dikey", yatay: "Yatay", duvar: "Duvar kâğıdı" };
const IPUCU = {
  foto: ["Örn: a red vintage car on a rainy street at night", "İngilizce yazarsan daha iyi sonuç alırsın."],
  video: ["Örn: a golden retriever running on a beach at sunset, waves crashing, camera tracking alongside",
    "Sahneyi, hareketi ve kamerayı ayrıntılı anlat. Video sesli üretilir. İngilizce daha iyi sonuç verir."],
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
  $("#uret").hidden = ad !== "uret";
  if (ad === "galeri") galeriCiz();
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
  const url = URL.createObjectURL(kayit.blob);
  if (kayit.tur === "video") {
    const v = document.createElement("video");
    v.src = url;
    v.muted = true;
    v.loop = true;
    v.autoplay = true;
    v.playsInline = true;
    b.appendChild(v);
  } else {
    const img = document.createElement("img");
    img.src = url;
    img.alt = kayit.prompt;
    b.appendChild(img);
  }
  b.addEventListener("click", () => goruntule(kayit, url));
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
    const ortak = {
      tur: istek.tur, prompt, mod: istek.mod, boyut: istek.boyut, kalite: istek.kalite,
      genislik: d.genislik, yukseklik: d.yukseklik, tarih: Date.now(),
    };
    const kayitlar = video
      ? [{ ...ortak, id: Date.now() + "-v" + d.tohum, blob: base64Blob(d.video, "video/mp4"), tohum: d.tohum,
           sure: istek.sure, fotodan: !!istek.resim }]
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
let galeriUrller = [];
async function galeriCiz() {
  galeriUrller.forEach(URL.revokeObjectURL);
  galeriUrller = [];
  const kayitlar = (await galeriHepsi()).sort((a, b) => b.tarih - a.tarih);
  const kutu = $("#galeri");
  kutu.innerHTML = "";
  $("#galeri-bos").hidden = kayitlar.length > 0;
  for (const k of kayitlar) {
    const url = URL.createObjectURL(k.blob);
    galeriUrller.push(url);
    const b = document.createElement("button");
    if (k.tur === "video") {
      const v = document.createElement("video");
      v.src = url + "#t=0.1";
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
      img.src = url;
      img.alt = k.prompt;
      img.loading = "lazy";
      b.appendChild(img);
    }
    b.addEventListener("click", () => goruntule(k, url));
    kutu.appendChild(b);
  }
}

// --- Tam ekran görüntüleyici ---
let acik = null;
function goruntule(kayit, url) {
  acik = { kayit, url };
  $("#bildirim").hidden = true;
  const video = kayit.tur === "video";
  const v = $("#g-video");
  $("#g-img").hidden = video;
  v.hidden = !video;
  if (video) {
    v.src = url;
    v.play().catch(() => {});
  } else {
    $("#g-img").src = url;
  }
  $("#g-canlandir").hidden = video;
  $("#g-prompt").textContent = kayit.prompt;
  const mod = modlar.find((m) => m.id === kayit.mod);
  $("#g-bilgi").textContent = [
    video ? `Video · ${kayit.sure} sn${kayit.fotodan ? " · fotoğraftan" : ""}` : null,
    mod ? mod.ad : kayit.mod, BOYUT_ADI[kayit.boyut], `${kayit.genislik}×${kayit.yukseklik}`, `Tohum ${kayit.tohum}`,
  ].filter(Boolean).join(" · ");
  $("#goruntuleyici").hidden = false;
  history.pushState({ goruntuleyici: true }, "");
}
function kapat() {
  const v = $("#g-video");
  v.pause();
  v.removeAttribute("src");
  $("#goruntuleyici").hidden = true;
  acik = null;
}
$("#g-kapat").addEventListener("click", () => history.back());
// Android geri tuşu görüntüleyiciyi kapatsın, uygulamadan çıkmasın.
window.addEventListener("popstate", kapat);

const dosyaAdi = (k) => `foto-studyo-${k.tohum}.${k.tur === "video" ? "mp4" : "jpg"}`;

$("#g-kaydet").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = acik.url;
  a.download = dosyaAdi(acik.kayit);
  document.body.appendChild(a);
  a.click();
  a.remove();
});

$("#g-paylas").addEventListener("click", async () => {
  const k = acik.kayit;
  const dosya = new File([k.blob], dosyaAdi(k), { type: k.blob.type });
  if (navigator.canShare && navigator.canShare({ files: [dosya] })) {
    try { await navigator.share({ files: [dosya] }); } catch {}
  } else {
    bildir("Bu cihaz paylaşımı desteklemiyor; Kaydet'i kullan.");
  }
});

$("#g-canlandir").addEventListener("click", () => {
  const k = acik.kayit;
  baslangicAyarla(k.blob);
  turSec("video");
  segmentSec("boyut", "boyut", k.boyut === "duvar" ? "dikey" : k.boyut);
  modSec("serbest"); // stil zaten fotoğrafta var
  $("#prompt").value = k.prompt;
  $("#tohum").value = "";
  history.back();
  sekmeAc("uret");
  bildir("Fotoğraf eklendi. Nasıl hareket etsin, açıklamaya ekle ve Video üret'e bas.");
});

$("#g-tekrar").addEventListener("click", () => {
  const k = acik.kayit;
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
  history.back();
  sekmeAc("uret");
  bildir("Ayarlar yüklendi. Açıklamayı değiştirip tekrar üretebilirsin.");
});

$("#g-sil").addEventListener("click", async () => {
  if (!confirm("Silinsin mi?")) return;
  await galeriSil(acik.kayit.id);
  history.back();
  if (!$("#sayfa-galeri").hidden) galeriCiz();
});

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
