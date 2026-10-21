"""Telefondan fotoğraf üretme servisi: Modal + Z-Image-Turbo.

Kurulum ve kullanım için KURULUM.md dosyasına bakın.
Yayına almak için:  modal deploy foto.py
"""

import io
import os

import modal

MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"
MODEL_DIR = "/modeller"

app = modal.App("foto-uret")

image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch",
    "diffusers==0.41.0",
    "transformers",
    "accelerate",
    "safetensors",
    "fastapi[standard]",
)

# Model dosyaları (~20 GB) bir kez indirilip burada saklanır;
# her açılışta yeniden indirilmez, böylece süre ve kredi boşa gitmez.
modeller = modal.Volume.from_name("foto-modeller", create_if_missing=True)

BOYUTLAR = {
    "kare": (1024, 1024),
    "dikey": (768, 1344),
    "yatay": (1344, 768),
}


@app.cls(
    gpu="L40S",
    image=image,
    volumes={MODEL_DIR: modeller},
    secrets=[modal.Secret.from_name("foto-sifre")],
    scaledown_window=120,  # 2 dk kullanılmazsa GPU kapanır, ücret durur
    max_containers=1,  # aynı anda en fazla 1 GPU: sürpriz fatura olmasın
    timeout=600,
)
class Uretici:
    @modal.enter()
    def yukle(self):
        import torch
        from diffusers import ZImagePipeline

        os.environ["HF_HOME"] = MODEL_DIR
        self.pipe = ZImagePipeline.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16, cache_dir=MODEL_DIR
        ).to("cuda")
        modeller.commit()

    @modal.asgi_app()
    def web(self):
        import hmac

        import torch
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.responses import HTMLResponse, Response
        from pydantic import BaseModel

        sifre = os.environ["SIFRE"]
        api = FastAPI()

        class Istek(BaseModel):
            prompt: str
            boyut: str = "kare"
            tohum: int | None = None

        @api.get("/", response_class=HTMLResponse)
        def anasayfa():
            return SAYFA

        @api.post("/uret")
        def uret(istek: Istek, x_sifre: str = Header(default="")):
            if not hmac.compare_digest(x_sifre.encode(), sifre.encode()):
                raise HTTPException(status_code=403, detail="Şifre yanlış")
            if not istek.prompt.strip():
                raise HTTPException(status_code=400, detail="Açıklama boş")
            genislik, yukseklik = BOYUTLAR.get(istek.boyut, BOYUTLAR["kare"])
            generator = None
            if istek.tohum is not None:
                generator = torch.Generator("cuda").manual_seed(istek.tohum)
            img = self.pipe(
                prompt=istek.prompt[:2000],
                width=genislik,
                height=yukseklik,
                num_inference_steps=9,
                guidance_scale=0.0,
                generator=generator,
            ).images[0]
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            return Response(buf.getvalue(), media_type="image/jpeg")

        return api


SAYFA = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#111">
<title>Foto Üret</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee; }
  main { max-width: 560px; margin: 0 auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  h1 { font-size: 1.3rem; margin: 4px 0; }
  textarea, input, select, button { width: 100%; font: inherit; font-size: 16px; border-radius: 10px;
    border: 1px solid #333; background: #1c1c1c; color: #eee; padding: 12px; }
  textarea { min-height: 110px; resize: vertical; }
  .satir { display: flex; gap: 8px; }
  button { background: #4f7cff; border: none; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: .5; }
  #durum { min-height: 1.2em; color: #aaa; font-size: .9rem; }
  #sonuc img { width: 100%; border-radius: 10px; display: block; }
  a { color: #8ab0ff; }
</style>
</head>
<body>
<main>
  <h1>Foto Üret</h1>
  <input id="sifre" type="password" placeholder="Şifre" autocomplete="current-password">
  <textarea id="prompt" placeholder="Ne görmek istiyorsun? (İngilizce daha iyi sonuç verir)"></textarea>
  <div class="satir">
    <select id="boyut">
      <option value="kare">Kare</option>
      <option value="dikey">Dikey (telefon)</option>
      <option value="yatay">Yatay</option>
    </select>
    <input id="tohum" type="number" inputmode="numeric" placeholder="Tohum (boş = rastgele)">
  </div>
  <button id="uret">Üret</button>
  <div id="durum"></div>
  <div id="sonuc"></div>
</main>
<script>
  const $ = (id) => document.getElementById(id);
  try { $("sifre").value = localStorage.getItem("sifre") || ""; } catch (e) {}
  $("uret").onclick = async () => {
    const prompt = $("prompt").value.trim();
    if (!prompt) { $("durum").textContent = "Önce bir açıklama yaz."; return; }
    try { localStorage.setItem("sifre", $("sifre").value); } catch (e) {}
    $("uret").disabled = true;
    const bas = Date.now();
    $("durum").textContent = "Üretiliyor... (ilk seferde 1-3 dakika sürebilir)";
    try {
      const tohum = $("tohum").value === "" ? null : Number($("tohum").value);
      const r = await fetch("uret", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Sifre": $("sifre").value },
        body: JSON.stringify({ prompt, boyut: $("boyut").value, tohum }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || ("Hata " + r.status));
      }
      const url = URL.createObjectURL(await r.blob());
      $("sonuc").innerHTML = `<img src="${url}" alt=""><p><a href="${url}" download="foto.jpg">İndir</a> veya resme basılı tutup kaydet.</p>`;
      $("durum").textContent = "Hazır (" + ((Date.now() - bas) / 1000).toFixed(1) + " sn)";
    } catch (e) {
      $("durum").textContent = "Olmadı: " + e.message;
    } finally {
      $("uret").disabled = false;
    }
  };
</script>
</body>
</html>
"""
