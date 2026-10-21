"""Foto Stüdyo: telefondan görsel üretme uygulaması (Modal + Z-Image-Turbo).

- Uretici: GPU üzerinde Z-Image-Turbo ile görsel üretir.
- web: Telefon uygulamasını (PWA) ve API'yi sunar; ucuz CPU üzerinde çalışır,
  GPU yalnızca üretim sırasında açılır.

Kurulum ve kullanım için KURULUM.md dosyasına bakın.
Yayına almak için:  modal deploy foto.py
"""

import io
import os
import random
from pathlib import Path

import modal

MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"
MODEL_DIR = "/modeller"
STATIK_DIR = "/statik"
OTURUM_SURESI = 90 * 24 * 3600  # 90 gün

app = modal.App("foto-uret")

gpu_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch",
    "diffusers==0.41.0",
    "transformers",
    "accelerate",
    "safetensors",
)
web_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]")
    .add_local_dir(Path(__file__).parent / "statik", STATIK_DIR)
)

# Model dosyaları (~20 GB) bir kez indirilip burada saklanır;
# her açılışta yeniden indirilmez, böylece süre ve kredi boşa gitmez.
modeller = modal.Volume.from_name("foto-modeller", create_if_missing=True)
sifre_secret = modal.Secret.from_name("foto-sifre")

# Modlar: kullanıcının yazdığı açıklamanın sonuna eklenen stil tarifleri.
MODLAR = [
    {"id": "serbest", "ad": "Serbest", "simge": "✨", "ek": ""},
    {"id": "gercekci", "ad": "Gerçekçi", "simge": "📷",
     "ek": "photorealistic, ultra detailed, natural lighting, shot on a DSLR, 50mm lens, high dynamic range"},
    {"id": "portre", "ad": "Portre", "simge": "👤",
     "ek": "professional portrait photography, soft studio lighting, shallow depth of field, sharp focus on the eyes, creamy bokeh background"},
    {"id": "sinematik", "ad": "Sinematik", "simge": "🎬",
     "ek": "cinematic film still, dramatic lighting, anamorphic lens, color graded, epic composition, subtle film grain"},
    {"id": "anime", "ad": "Anime", "simge": "🌸",
     "ek": "anime style illustration, vibrant colors, clean line art, detailed background, high quality key visual"},
    {"id": "3d", "ad": "3D Karakter", "simge": "🧸",
     "ek": "3D render, stylized animated movie character, soft global illumination, subsurface scattering, highly detailed"},
    {"id": "yagliboya", "ad": "Yağlı Boya", "simge": "🎨",
     "ek": "oil painting on canvas, visible brush strokes, rich textures, classical fine art, museum quality"},
    {"id": "logo", "ad": "Logo / İkon", "simge": "🔷",
     "ek": "minimalist vector logo design, flat colors, clean geometric shapes, centered on a plain white background, professional branding"},
    {"id": "urun", "ad": "Ürün", "simge": "🛍️",
     "ek": "professional product photography, studio lighting, clean seamless background, commercial advertising shot, crisp detail"},
]

# (genişlik, yükseklik) – standart kalite; yüksek kalitede 1.5 katı.
BOYUTLAR = {
    "kare": (1024, 1024),
    "dikey": (768, 1344),
    "yatay": (1344, 768),
    "duvar": (736, 1600),
}


def boyut_hesapla(boyut: str, kalite: str) -> tuple[int, int]:
    genislik, yukseklik = BOYUTLAR.get(boyut, BOYUTLAR["kare"])
    if kalite == "yuksek":
        genislik, yukseklik = int(genislik * 1.5), int(yukseklik * 1.5)
    # Model 16'nın katı olan boyutlar ister.
    return genislik // 16 * 16, yukseklik // 16 * 16


def son_prompt(prompt: str, mod_id: str) -> str:
    mod = next((m for m in MODLAR if m["id"] == mod_id), MODLAR[0])
    prompt = prompt.strip()[:2000]
    return f"{prompt}, {mod['ek']}" if mod["ek"] else prompt


@app.cls(
    gpu="L40S",
    image=gpu_image,
    volumes={MODEL_DIR: modeller},
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

    @modal.method()
    def isit(self) -> bool:
        """Uygulama açılınca GPU'yu önceden hazırlamak için boş çağrı."""
        return True

    @modal.method()
    def uret(self, prompt: str, genislik: int, yukseklik: int, adet: int, tohum: int | None):
        import torch

        if tohum is None:
            tohum = random.randint(0, 2**31 - 1)
        sonuc = []
        # Görseller tek tek üretilir; yüksek çözünürlükte bellek taşmasın.
        for i in range(adet):
            img = self.pipe(
                prompt=prompt,
                width=genislik,
                height=yukseklik,
                num_inference_steps=9,
                guidance_scale=0.0,
                generator=torch.Generator("cuda").manual_seed(tohum + i),
            ).images[0]
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            sonuc.append({"jpeg": buf.getvalue(), "tohum": tohum + i})
        return sonuc


def uygulama_olustur(sifre: str, uret_fn, isit_fn, statik_dir: str):
    """Web uygulamasını kurar. Yerel testte sahte uret_fn/isit_fn verilebilir."""
    import base64
    import hashlib
    import hmac
    import time

    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field

    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def imza(bitis: int) -> str:
        return hmac.new(sifre.encode(), f"oturum:{bitis}".encode(), hashlib.sha256).hexdigest()

    def oturum_kontrol(authorization: str = Header(default="")):
        anahtar = authorization.removeprefix("Bearer ").strip()
        bitis, _, sig = anahtar.partition(".")
        if not bitis.isdigit() or int(bitis) < time.time() or not hmac.compare_digest(sig, imza(int(bitis))):
            raise HTTPException(status_code=401, detail="Oturum geçersiz, tekrar giriş yap.")

    class Giris(BaseModel):
        sifre: str

    class Istek(BaseModel):
        prompt: str = Field(min_length=1, max_length=2000)
        mod: str = "serbest"
        boyut: str = "kare"
        kalite: str = "standart"
        adet: int = Field(default=1, ge=1, le=4)
        tohum: int | None = Field(default=None, ge=0, le=2**31 - 5)

    @api.post("/api/giris")
    def giris(g: Giris):
        if not hmac.compare_digest(g.sifre.encode(), sifre.encode()):
            time.sleep(1)  # şifre denemelerini yavaşlat
            raise HTTPException(status_code=403, detail="Şifre yanlış")
        bitis = int(time.time()) + OTURUM_SURESI
        return {"oturum": f"{bitis}.{imza(bitis)}"}

    @api.get("/api/modlar", dependencies=[Depends(oturum_kontrol)])
    def modlar():
        return [{k: m[k] for k in ("id", "ad", "simge")} for m in MODLAR]

    @api.post("/api/isit", dependencies=[Depends(oturum_kontrol)])
    def isit():
        isit_fn()
        return {"hazir": True}

    @api.post("/api/uret", dependencies=[Depends(oturum_kontrol)])
    def uret(istek: Istek):
        if not istek.prompt.strip():
            raise HTTPException(status_code=400, detail="Açıklama boş")
        genislik, yukseklik = boyut_hesapla(istek.boyut, istek.kalite)
        bas = time.time()
        gorseller = uret_fn(son_prompt(istek.prompt, istek.mod), genislik, yukseklik, istek.adet, istek.tohum)
        return {
            "sure": round(time.time() - bas, 1),
            "genislik": genislik,
            "yukseklik": yukseklik,
            "gorseller": [
                {"veri": base64.b64encode(g["jpeg"]).decode(), "tohum": g["tohum"]} for g in gorseller
            ],
        }

    # PWA dosyaları: service worker ve manifest kökten sunulmalı.
    @api.get("/sw.js", include_in_schema=False)
    def sw():
        return FileResponse(f"{statik_dir}/sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})

    @api.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(f"{statik_dir}/manifest.webmanifest", media_type="application/manifest+json")

    @api.get("/", include_in_schema=False)
    def anasayfa():
        return FileResponse(f"{statik_dir}/index.html", headers={"Cache-Control": "no-cache"})

    api.mount("/static", StaticFiles(directory=statik_dir), name="static")
    return api


@app.function(image=web_image, secrets=[sifre_secret], timeout=900)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label="foto-studyo")
def web():
    return uygulama_olustur(
        sifre=os.environ["SIFRE"],
        uret_fn=lambda *a: Uretici().uret.remote(*a),
        isit_fn=lambda: Uretici().isit.remote(),
        statik_dir=STATIK_DIR,
    )
