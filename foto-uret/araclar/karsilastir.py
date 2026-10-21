"""Model karşılaştırması: Qwen-Image-2.1 vs FLUX.2 [dev] (fotoğraf), LTX-2.5 vs Wan 2.2 (video).

Çalıştırma (GPU kredisi harcar, ~1,5-2 $):  modal run karsilastir.py
Mevcut modeller uygulamadaki yayındaki sınıflarla (aynı ayarlarla) çalıştırılır; Wan ve FLUX
belgelerdeki önerilen ayarlarla. Çıktılar ./tani-cikti/ klasörüne yazılır.
"""

import io
import os
import time
import traceback
import uuid
from pathlib import Path

import modal

WAN_ID = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
WAN_YOL = "/video-modeller/wan2.2-t2v-a14b"
FLUX_ID = "black-forest-labs/FLUX.2-dev"
FLUX_YOL = "/modeller/flux2-dev"

app = modal.App("foto-karsilastir")
modeller = modal.Volume.from_name("foto-modeller")
video_modeller = modal.Volume.from_name("foto-video-modeller")
hf_secret = modal.Secret.from_name("foto-hf")
teslim = modal.Dict.from_name("foto-teslim")
gpu_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch", "torchvision", "diffusers==0.41.0", "transformers", "accelerate", "safetensors", "pillow",
    "huggingface_hub", "av", "ftfy", "sentencepiece", "protobuf",
)
indirme_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub")

FOTO_PROMPTLAR = {
    "portre": "A candid close-up portrait of an elderly fisherman with deep wrinkles and a grey beard, wearing a "
              "yellow raincoat, on a harbor at dawn, soft natural light, realistic skin texture",
    "yazi": 'A cozy bakery storefront at night with a glowing neon sign that reads "Foto Studyo", warm light from '
            "the windows, rain on the street, reflections on wet pavement",
    "sahne": "Three friends of different ages having a picnic in a sunflower field, a golden retriever lying on the "
             "blanket, detailed food on plates, late afternoon golden light, realistic photo",
}
REFERANS_PROMPT = ("The same man from the reference image, now sitting at a desk in a modern glass office, "
                   "wearing a navy suit and tie. Keep his face, wrinkles and beard identical.")
VIDEO_PROMPTLAR = {
    "insan": "A young woman with curly red hair laughs and talks to a friend at a busy cafe table, close-up, "
             "natural window light",
    "hareket": "A golden retriever runs along a beach at sunset, splashing through shallow waves, the camera "
               "tracking alongside",
}
WAN_NEGATIF = ("Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, "
               "static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra "
               "fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, "
               "still picture, messy background, three legs, many people in the background, walking backwards")


@app.function(image=indirme_image, volumes={"/video-modeller": video_modeller}, timeout=3600)
def wan_indir():
    from huggingface_hub import snapshot_download

    snapshot_download(WAN_ID, local_dir=WAN_YOL)
    video_modeller.commit()


@app.function(image=indirme_image, volumes={"/modeller": modeller}, secrets=[hf_secret], timeout=3600)
def flux_indir() -> str | None:
    """FLUX.2 [dev] onay gerektirir; erişim yoksa hata metni döner (GPU hiç açılmaz)."""
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(FLUX_ID, local_dir=FLUX_YOL, token=os.environ.get("HF_TOKEN") or None,
                          ignore_patterns=["flux2-dev.safetensors", "ae.safetensors", "*.png", "*.jpg"])
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:300]}"
    modeller.commit()
    return None


def _video_paketle(frames, fps) -> bytes:
    import tempfile

    from diffusers.utils import encode_video

    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        encode_video(frames, fps=fps, output_path=f.name)
        return Path(f.name).read_bytes()


@app.cls(gpu="H200", image=gpu_image, volumes={"/video-modeller": video_modeller}, timeout=3000)
class Wan:
    @modal.enter()
    def yukle(self):
        self.hata = None
        try:
            import torch
            from diffusers import AutoencoderKLWan, WanPipeline

            vae = AutoencoderKLWan.from_pretrained(WAN_YOL, subfolder="vae", torch_dtype=torch.float32)
            self.pipe = WanPipeline.from_pretrained(WAN_YOL, vae=vae, torch_dtype=torch.bfloat16).to("cuda")
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    @modal.method()
    def uret(self, prompt: str) -> dict:
        if self.hata:
            return {"hata": self.hata[-1500:]}
        import torch

        bas = time.time()
        frames = self.pipe(prompt=prompt, negative_prompt=WAN_NEGATIF, height=720, width=1280, num_frames=81,
                           guidance_scale=4.0, guidance_scale_2=3.0, num_inference_steps=40,
                           generator=torch.Generator("cuda").manual_seed(42)).frames[0]
        return {"mp4": _video_paketle(frames, 16), "sure": round(time.time() - bas)}


@app.cls(gpu="H200", image=gpu_image, volumes={"/modeller": modeller}, timeout=3000)
class Flux:
    @modal.enter()
    def yukle(self):
        self.hata = None
        try:
            import torch
            from diffusers import Flux2Pipeline

            self.pipe = Flux2Pipeline.from_pretrained(FLUX_YOL, torch_dtype=torch.bfloat16).to("cuda")
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    @modal.method()
    def uret(self, isler: list[tuple]) -> dict:
        """isler: [(ad, prompt, referans_jpeg | None), ...]"""
        if self.hata:
            return {"hata": self.hata[-1500:]}
        import torch
        from PIL import Image

        sonuc = {}
        for ad, prompt, ref in isler:
            bas = time.time()
            ek = {"image": [Image.open(io.BytesIO(ref)).convert("RGB")]} if ref else {"width": 1024, "height": 1024}
            img = self.pipe(prompt=prompt, num_inference_steps=50, guidance_scale=4.0,
                            generator=torch.Generator("cuda").manual_seed(42), **ek).images[0]
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=92)
            sonuc[ad] = {"jpeg": buf.getvalue(), "sure": round(time.time() - bas)}
        return sonuc


def _yayindaki(sinif: str, *args):
    """Uygulamadaki yayında olan sınıfı aynı teslim kutusu yoluyla çalıştırır."""
    anahtar = uuid.uuid4().hex
    teslim[f"girdi:{anahtar}"] = args
    cagri = modal.Cls.from_name("foto-uret", sinif)().uret.spawn(anahtar)
    return cagri, anahtar


def _yayindaki_sonuc(cagri, anahtar):
    cagri.get()
    return teslim.pop(f"sonuc:{anahtar}")


@app.local_entrypoint()
def main():
    cikti = Path("tani-cikti")
    cikti.mkdir(exist_ok=True)
    t0 = time.time()

    # 1) İndirmeler (GPU'suz) ve uygulamadaki modellerin işleri aynı anda başlar
    wan_ind, flux_ind = wan_indir.spawn(), flux_indir.spawn()
    qwen = {ad: _yayindaki("QwenUretici", p, 1024, 1024, 1, 42, None, 1024) for ad, p in FOTO_PROMPTLAR.items()}
    ltx = {ad: _yayindaki("VideoUretici", p, 1920, 1088, 121, 42, None) for ad, p in VIDEO_PROMPTLAR.items()}

    qwen_sonuc = {ad: _yayindaki_sonuc(*c)[0]["jpeg"] for ad, c in qwen.items()}
    for ad, jpeg in qwen_sonuc.items():
        (cikti / f"qwen_{ad}.jpg").write_bytes(jpeg)
    # Referans testi: iki model de Qwen'in balıkçı portresini referans alır
    qwen_ref = _yayindaki_sonuc(*_yayindaki("QwenUretici", REFERANS_PROMPT, None, None, 1, 42,
                                            [qwen_sonuc["portre"]], 1024))
    (cikti / "qwen_referans.jpg").write_bytes(qwen_ref[0]["jpeg"])
    print(f"Qwen bitti: {time.time() - t0:.0f} sn")

    # 2) FLUX (erişim varsa) ve Wan
    flux_hata = flux_ind.get()
    flux_cagri = None
    if flux_hata:
        print("FLUX.2 [dev] indirilemedi (Hugging Face onayı gerekebilir):", flux_hata)
    else:
        isler = [(ad, p, None) for ad, p in FOTO_PROMPTLAR.items()] + [("referans", REFERANS_PROMPT, qwen_sonuc["portre"])]
        flux_cagri = Flux().uret.spawn(isler)
    wan_ind.get()
    wan = {ad: Wan().uret.spawn(p) for ad, p in VIDEO_PROMPTLAR.items()}

    for ad, (cagri, anahtar) in ltx.items():
        v = _yayindaki_sonuc(cagri, anahtar)
        (cikti / f"ltx_{ad}.mp4").write_bytes(v["mp4"])
    print(f"LTX bitti: {time.time() - t0:.0f} sn")

    if flux_cagri:
        f = flux_cagri.get()
        if "hata" in f:
            print("FLUX hatası:", f["hata"])
        else:
            for ad, v in f.items():
                (cikti / f"flux_{ad}.jpg").write_bytes(v["jpeg"])
            print("FLUX süreleri (sn):", {ad: v["sure"] for ad, v in f.items()})
    for ad, cagri in wan.items():
        v = cagri.get()
        if "hata" in v:
            print(f"Wan {ad} hatası:", v["hata"])
            continue
        (cikti / f"wan_{ad}.mp4").write_bytes(v["mp4"])
        print(f"Wan {ad}: {v['sure']} sn")
    print(f"Toplam: {time.time() - t0:.0f} sn")
