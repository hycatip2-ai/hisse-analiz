"""Foto Stüdyo: telefondan görsel ve video üretme uygulaması (Modal).

- Uretici: GPU (L40S) üzerinde Z-Image-Turbo ile fotoğraf üretir.
- VideoUretici: GPU (H200) üzerinde LTX-2.3 ile sesli video üretir
  (yazıdan veya bir fotoğraftan).
- video_modeli_indir: Video modelini (~100 GB) ucuz CPU'da bir kez indirir.
- web: Telefon uygulamasını (PWA) ve API'yi sunar; ucuz CPU üzerinde çalışır,
  GPU'lar yalnızca üretim sırasında açılır.

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
VIDEO_MODEL_ID = "diffusers/LTX-2.3-Diffusers"
VIDEO_DIR = "/video-modeller"
VIDEO_YOL = f"{VIDEO_DIR}/ltx-2.3"
STATIK_DIR = "/statik"
OTURUM_SURESI = 90 * 24 * 3600  # 90 gün
VIDEO_FPS = 24

app = modal.App("foto-uret")

gpu_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch",
    "diffusers==0.41.0",
    "transformers",
    "accelerate",
    "safetensors",
)
video_gpu_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch",
    "diffusers==0.41.0",
    "transformers",
    "accelerate",
    "safetensors",
    "av",
    "pillow",
    "sentencepiece",
    "protobuf",
    "huggingface_hub",
)
indirme_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub")
web_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]")
    .add_local_dir(Path(__file__).parent / "statik", STATIK_DIR)
)

# Model dosyaları bir kez indirilip burada saklanır;
# her açılışta yeniden indirilmez, böylece süre ve kredi boşa gitmez.
modeller = modal.Volume.from_name("foto-modeller", create_if_missing=True)
video_modeller = modal.Volume.from_name("foto-video-modeller", create_if_missing=True)
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

# (genişlik, yükseklik) – fotoğraf için standart kalite; yüksek kalitede 1.5 katı.
BOYUTLAR = {
    "kare": (1024, 1024),
    "dikey": (768, 1344),
    "yatay": (1344, 768),
    "duvar": (736, 1600),
}
# Video boyutları 32'nin katı olmalı.
VIDEO_BOYUTLARI = {
    "kare": (640, 640),
    "dikey": (512, 768),
    "yatay": (768, 512),
}
# Kare sayısı 8k+1 olmalı: 121 kare ≈ 5 sn, 241 kare ≈ 10 sn (24 fps).
VIDEO_KARELERI = {5: 121, 10: 241}


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


@app.function(image=indirme_image, volumes={VIDEO_DIR: video_modeller}, timeout=3 * 3600)
def video_modeli_indir():
    """Video modelini GPU'suz (ucuz) bir makinede indirir; zaten varsa hızlıca geçer."""
    from huggingface_hub import snapshot_download

    snapshot_download(VIDEO_MODEL_ID, local_dir=VIDEO_YOL)
    video_modeller.commit()
    print("Video modeli hazır:", VIDEO_YOL)


@app.cls(
    gpu="H200",
    image=video_gpu_image,
    volumes={VIDEO_DIR: video_modeller},
    scaledown_window=120,
    max_containers=1,
    timeout=1800,
)
class VideoUretici:
    @modal.enter()
    def yukle(self):
        import torch
        from diffusers import LTX2ImageToVideoPipeline, LTX2Pipeline
        from huggingface_hub import snapshot_download

        # Normalde video_modeli_indir zaten indirmiştir; eksik dosya varsa tamamlar.
        snapshot_download(VIDEO_MODEL_ID, local_dir=VIDEO_YOL)
        self.yazidan = LTX2Pipeline.from_pretrained(VIDEO_YOL, torch_dtype=torch.bfloat16).to("cuda")
        self.yazidan.vae.enable_tiling()
        self.fotodan = LTX2ImageToVideoPipeline(**self.yazidan.components)

    @modal.method()
    def isit(self) -> bool:
        return True

    @modal.method()
    def uret(self, prompt: str, genislik: int, yukseklik: int, kare: int, tohum: int | None,
             resim: bytes | None):
        import tempfile

        import torch
        from diffusers.pipelines.ltx2.utils import DEFAULT_NEGATIVE_PROMPT
        from diffusers.utils import encode_video
        from PIL import Image, ImageOps

        if tohum is None:
            tohum = random.randint(0, 2**31 - 1)
        ortak = dict(
            prompt=prompt,
            negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            width=genislik,
            height=yukseklik,
            num_frames=kare,
            frame_rate=float(VIDEO_FPS),
            num_inference_steps=30,
            generator=torch.Generator("cuda").manual_seed(tohum),
            output_type="np",
            return_dict=False,
        )
        if resim:
            img = Image.open(io.BytesIO(resim)).convert("RGB")
            img = ImageOps.fit(img, (genislik, yukseklik), Image.LANCZOS)
            video, ses = self.fotodan(image=img, **ortak)
        else:
            video, ses = self.yazidan(**ortak)

        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            encode_video(
                video[0],
                fps=VIDEO_FPS,
                audio=ses[0].float().cpu(),
                audio_sample_rate=self.yazidan.vocoder.config.output_sampling_rate,
                output_path=f.name,
            )
            mp4 = Path(f.name).read_bytes()
        return {"mp4": mp4, "tohum": tohum}


def uygulama_olustur(sifre: str, baslat, sonuc_al, statik_dir: str):
    """Web uygulamasını kurar. Yerel testte sahte fonksiyonlar verilebilir.

    GPU işleri arka planda başlatılır ve iş kimliği hemen döner; telefon
    sonucu /api/is/{kimlik} ile birkaç saniyede bir sorar. Böylece hiçbir
    istek uzun sürmez (Modal 150 sn'yi aşan istekleri yönlendirir ve
    tarayıcıda "invalid function call" hatası çıkabiliyordu).

    baslat(is_turu, *args) -> iş kimliği; is_turu: foto, video, isit_foto, isit_video
    sonuc_al(kimlik) -> sonuç; hazır değilse TimeoutError
    """
    import base64
    import binascii
    import hashlib
    import hmac
    import time
    from typing import Literal

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

    class Isit(BaseModel):
        tur: Literal["foto", "video"] = "foto"

    class Istek(BaseModel):
        tur: Literal["foto", "video"] = "foto"
        prompt: str = Field(min_length=1, max_length=2000)
        mod: str = "serbest"
        boyut: str = "kare"
        kalite: str = "standart"
        adet: int = Field(default=1, ge=1, le=4)
        tohum: int | None = Field(default=None, ge=0, le=2**31 - 5)
        sure: Literal[5, 10] = 5
        resim: str | None = Field(default=None, max_length=12_000_000)  # base64 JPEG/PNG

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
    def isit(i: Isit | None = None):
        return {"is": baslat("isit_" + (i.tur if i else "foto"))}

    @api.post("/api/uret", dependencies=[Depends(oturum_kontrol)])
    def uret(istek: Istek):
        if not istek.prompt.strip():
            raise HTTPException(status_code=400, detail="Açıklama boş")
        prompt = son_prompt(istek.prompt, istek.mod)
        if istek.tur == "video":
            resim = None
            if istek.resim:
                try:
                    resim = base64.b64decode(istek.resim, validate=True)
                except binascii.Error:
                    raise HTTPException(status_code=400, detail="Fotoğraf okunamadı")
            genislik, yukseklik = VIDEO_BOYUTLARI.get(istek.boyut, VIDEO_BOYUTLARI["yatay"])
            kimlik = baslat("video", prompt, genislik, yukseklik, VIDEO_KARELERI[istek.sure], istek.tohum, resim)
        else:
            genislik, yukseklik = boyut_hesapla(istek.boyut, istek.kalite)
            kimlik = baslat("foto", prompt, genislik, yukseklik, istek.adet, istek.tohum)
        return {"is": kimlik, "genislik": genislik, "yukseklik": yukseklik}

    @api.get("/api/is/{kimlik}", dependencies=[Depends(oturum_kontrol)])
    def is_durumu(kimlik: str):
        try:
            sonuc = sonuc_al(kimlik)
        except TimeoutError:
            return {"bitti": False}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Üretim başarısız: {type(e).__name__}: {e}")
        if isinstance(sonuc, dict):  # video
            return {"bitti": True, "video": base64.b64encode(sonuc["mp4"]).decode(), "tohum": sonuc["tohum"]}
        if not isinstance(sonuc, list):  # isit işi
            return {"bitti": True}
        return {
            "bitti": True,
            "gorseller": [
                {"veri": base64.b64encode(g["jpeg"]).decode(), "tohum": g["tohum"]} for g in sonuc
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


def _modal_baslat(is_turu: str, *args) -> str:
    sinif, metot = {
        "foto": (Uretici, "uret"),
        "isit_foto": (Uretici, "isit"),
        "video": (VideoUretici, "uret"),
        "isit_video": (VideoUretici, "isit"),
    }[is_turu]
    return getattr(sinif(), metot).spawn(*args).object_id


@app.function(image=web_image, secrets=[sifre_secret], timeout=900)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label="foto-studyo")
def web():
    return uygulama_olustur(
        sifre=os.environ["SIFRE"],
        baslat=_modal_baslat,
        sonuc_al=lambda kimlik: modal.FunctionCall.from_id(kimlik).get(timeout=0),
        statik_dir=STATIK_DIR,
    )
