"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

// GPU 2 dk kullanılmazsa kapanır (foto.py: scaledown_window=120).
const GPU_UYKU_MS = 110 * 1000;

const ayar = { mod: "serbest", boyut: "kare", adet: 1, kalite: "standart" };
let modlar = [];
let sonGpu = 0;
let isiniyor = false;
let uretiliyor = false;

// --- localStorage (gizli sekmede vb. yoksa sessizce geç) ---
const depo = {
  al(k) { try { return localStorage.getItem(k); } catch { return null; } },
  koy(k, v) { try { localStorage.setItem(k, v); } catch {} },
  sil(k) { try { localStorage.removeItem(k); } catch {} },
};

// --- Galeri: görseller telefonda IndexedDB'de saklanır ---
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
async function isBekle(kimlik) {
  const bas = Date.now();
  while (Date.now() - bas < 10 * 60 * 1000) {
    const d = await api("is/" + encodeURIComponent(kimlik));
    if (d.bitti) return d;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error("Çok uzun sürdü, tekrar dene.");
}

function base64Blob(b64) {
  const ham = atob(b64);
  const dizi = new Uint8Array(ham.length);
  for (let i = 0; i < ham.length; i++) dizi[i] = ham.charCodeAt(i);
  return new Blob([dizi], { type: "image/jpeg" });
}

const ORANLAR = { kare: "1 / 1", dikey: "4 / 7", yatay: "7 / 4", duvar: "23 / 50" };
const BOYUT_ADI = { kare: "Kare", dikey: "Dikey", yatay: "Yatay", duvar: "Duvar kâğıdı" };

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

function segmentKur(id, anahtar, sayi) {
  $$(`#${id} button`).forEach((b) =>
    b.addEventListener("click", () => segmentSec(id, anahtar, sayi ? Number(b.dataset.v) : b.dataset.v))
  );
}
function segmentSec(id, anahtar, deger) {
  ayar[anahtar] = deger;
  $$(`#${id} button`).forEach((b) => b.classList.toggle("secili", b.dataset.v === String(deger)));
}
segmentKur("boyut", "boyut");
segmentKur("adet", "adet", true);
segmentKur("kalite", "kalite");

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

// --- Sunucu (GPU) durumu ---
function sunucuCiz() {
  const s = $("#sunucu");
  const durum = isiniyor || uretiliyor ? "isiniyor" : Date.now() - sonGpu < GPU_UYKU_MS ? "hazir" : "uyku";
  s.className = "sunucu " + durum;
  s.querySelector("b").textContent = { isiniyor: "Hazırlanıyor", hazir: "Hazır", uyku: "Uykuda" }[durum];
}
setInterval(sunucuCiz, 5000);

async function isit() {
  if (isiniyor || uretiliyor || Date.now() - sonGpu < GPU_UYKU_MS) return;
  isiniyor = true;
  sunucuCiz();
  try {
    const d = await api("isit", {});
    await isBekle(d.is);
    sonGpu = Date.now();
  } catch {}
  isiniyor = false;
  sunucuCiz();
}
// Yazmaya başlayınca GPU arka planda uyanır; Üret'e basınca beklemezsin.
$("#prompt").addEventListener("focus", isit);

// --- Üretim ---
$("#uret").addEventListener("click", async () => {
  const prompt = $("#prompt").value.trim();
  if (!prompt) { bildir("Önce ne görmek istediğini yaz."); $("#prompt").focus(); return; }
  if (uretiliyor) return;
  const tohumMetin = $("#tohum").value.trim();
  const istek = { prompt, ...ayar, tohum: tohumMetin === "" ? null : Number(tohumMetin) };

  uretiliyor = true;
  sunucuCiz();
  $("#uret").disabled = true;
  const sonuclar = $("#sonuclar");
  sonuclar.className = "sonuclar" + (istek.adet === 1 ? " tek" : "");
  sonuclar.innerHTML = "";
  for (let i = 0; i < istek.adet; i++) {
    const d = document.createElement("div");
    d.className = "iskelet";
    d.style.aspectRatio = ORANLAR[istek.boyut];
    sonuclar.appendChild(d);
  }
  const durum = $("#durum");
  durum.hidden = false;
  const soguk = Date.now() - sonGpu > GPU_UYKU_MS;
  const bas = Date.now();
  const sayac = setInterval(() => {
    const sn = Math.round((Date.now() - bas) / 1000);
    durum.textContent = soguk && sn > 8
      ? `Sunucu uyanıyor, ilk seferde 1-3 dk sürebilir… ${sn} sn`
      : `Üretiliyor… ${sn} sn`;
  }, 500);
  durum.textContent = "Üretiliyor…";
  sonuclar.scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    const baslangic = await api("uret", istek);
    const d = { ...baslangic, ...(await isBekle(baslangic.is)) };
    sonGpu = Date.now();
    sonuclar.innerHTML = "";
    for (const g of d.gorseller) {
      const kayit = {
        id: Date.now() + "-" + g.tohum,
        blob: base64Blob(g.veri),
        prompt, mod: istek.mod, boyut: istek.boyut, kalite: istek.kalite,
        tohum: g.tohum, genislik: d.genislik, yukseklik: d.yukseklik, tarih: Date.now(),
      };
      await galeriEkle(kayit);
      const b = document.createElement("button");
      b.className = "sonuc";
      b.style.aspectRatio = ORANLAR[istek.boyut];
      const img = document.createElement("img");
      img.src = URL.createObjectURL(kayit.blob);
      img.alt = prompt;
      b.appendChild(img);
      b.addEventListener("click", () => goruntule(kayit, img.src));
      sonuclar.appendChild(b);
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
    const img = document.createElement("img");
    img.src = url;
    img.alt = k.prompt;
    img.loading = "lazy";
    b.appendChild(img);
    b.addEventListener("click", () => goruntule(k, url));
    kutu.appendChild(b);
  }
}

// --- Tam ekran görüntüleyici ---
let acik = null;
function goruntule(kayit, url) {
  acik = { kayit, url };
  $("#g-img").src = url;
  $("#g-prompt").textContent = kayit.prompt;
  const mod = modlar.find((m) => m.id === kayit.mod);
  $("#g-bilgi").textContent = [
    mod ? mod.ad : kayit.mod, BOYUT_ADI[kayit.boyut], `${kayit.genislik}×${kayit.yukseklik}`, `Tohum ${kayit.tohum}`,
  ].join(" · ");
  $("#goruntuleyici").hidden = false;
  history.pushState({ goruntuleyici: true }, "");
}
function kapat() {
  $("#goruntuleyici").hidden = true;
  acik = null;
}
$("#g-kapat").addEventListener("click", () => history.back());
// Android geri tuşu görüntüleyiciyi kapatsın, uygulamadan çıkmasın.
window.addEventListener("popstate", kapat);

const dosyaAdi = (k) => `foto-studyo-${k.tohum}.jpg`;

$("#g-kaydet").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = acik.url;
  a.download = dosyaAdi(acik.kayit);
  document.body.appendChild(a);
  a.click();
  a.remove();
});

$("#g-paylas").addEventListener("click", async () => {
  const dosya = new File([acik.kayit.blob], dosyaAdi(acik.kayit), { type: "image/jpeg" });
  if (navigator.canShare && navigator.canShare({ files: [dosya] })) {
    try { await navigator.share({ files: [dosya] }); } catch {}
  } else {
    bildir("Bu cihaz paylaşımı desteklemiyor; Kaydet'i kullan.");
  }
});

$("#g-tekrar").addEventListener("click", () => {
  const k = acik.kayit;
  $("#prompt").value = k.prompt;
  $("#tohum").value = k.tohum;
  $(".gelismis").open = true;
  modSec(k.mod);
  segmentSec("boyut", "boyut", k.boyut);
  segmentSec("kalite", "kalite", k.kalite);
  segmentSec("adet", "adet", 1);
  history.back();
  sekmeAc("uret");
  bildir("Ayarlar yüklendi. Açıklamayı değiştirip tekrar üretebilirsin.");
});

$("#g-sil").addEventListener("click", async () => {
  if (!confirm("Bu görsel silinsin mi?")) return;
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
sunucuCiz();
