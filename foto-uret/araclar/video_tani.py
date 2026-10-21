"""Video kalitesi teşhisi: aynı üretimi farklı yollarla çözüp kareleri karşılaştırır.

Çalıştırma (GPU kredisi harcar, ~0,5-1 $):  modal run araclar/video_tani.py
Çıktılar ./tani-cikti/ klasörüne yazılır: her varyant için mp4, kare şeridi (png) ve
kare kare parlaklık / renk doygunluğu tablosu.

Varyantlar (aynı tohum, aynı 1. aşama):
  A_yarim        yalnızca 1. aşama (yarım çözünürlük), büyütme yok
  B_mevcut       uygulamadaki kurulum: büyüt + 2. aşama + parça parça (tiling) çözme
  C_tilingsiz    B ile aynı gizli sonuç, parça parça çözme kapalı
  D_adain        büyütmede renk eşitleme (adain_factor=1.0) + 2. aşama + tiling
  E_kisa_prompt  kısa açıklama + "Gerçekçi" mod eki, uygulamadaki kurulum
"""

import io
import os
import time
from pathlib import Path

import modal

# foto.py ile aynı tanımlar (aynı imaj önbellekten gelir, aynı birim ve secret kullanılır).
VIDEO_MODEL_ID = "Lightricks/LTX-2.5-Diffusers"
VIDEO_DIR = "/video-modeller"
VIDEO_YOL = f"{VIDEO_DIR}/ltx-2.5"
FPS = 24

app = modal.App("foto-tani")
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
video_modeller = modal.Volume.from_name("foto-video-modeller")
hf_secret = modal.Secret.from_name("foto-hf")

UZUN_PROMPT = (
    "A close-up shot of a young woman with long dark hair and warm olive skin, standing on a city "
    "street at golden hour. She looks into the camera, smiles softly and then laughs, tilting her head "
    "slightly. Natural warm sunlight falls on the left side of her face, the background is softly "
    "blurred with passing cars. The camera holds steady at eye level. Realistic, natural skin texture."
)
KISA_PROMPT = (
    "a woman smiling and talking to the camera, close-up, photorealistic, ultra detailed, natural "
    "lighting, shot on a DSLR, 50mm lens, high dynamic range"
)
TOHUM = 1234
GENISLIK, YUKSEKLIK, KARE = 1088, 1920, 121
ORNEK_KARELER = [0, 30, 60, 90, 120]


@app.cls(gpu="H200", image=video_gpu_image, volumes={VIDEO_DIR: video_modeller},
         secrets=[hf_secret], timeout=3600)
class Tani:
    @modal.enter()
    def yukle(self):
        import torch
        from diffusers import LTX2LatentUpsamplePipeline, LTX2Pipeline
        from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel

        self.pipe = LTX2Pipeline.from_pretrained(
            VIDEO_YOL, torch_dtype=torch.bfloat16, prompt_enhancer=None, processor=None
        ).to("cuda")
        buyutucu = LTX2LatentUpsamplerModel.from_pretrained(
            VIDEO_YOL, subfolder="latent_upsampler", torch_dtype=torch.bfloat16
        ).to("cuda")
        self.buyut = LTX2LatentUpsamplePipeline(vae=self.pipe.vae, latent_upsampler=buyutucu)

    def _coz(self, latent, tiling: bool):
        """Pipeline'ın kendi çözme adımının aynısı (decode_timestep=0, gürültü yok)."""
        import torch

        vae = self.pipe.vae
        vae.enable_tiling() if tiling else vae.disable_tiling()
        timestep = None
        if vae.config.timestep_conditioning:
            timestep = torch.tensor([0.0], device="cuda", dtype=latent.dtype)
        with torch.no_grad():
            video = vae.decode(latent.to(vae.dtype), timestep, return_dict=False)[0]
        return self.pipe.video_processor.postprocess_video(video, output_type="np")[0]

    def _asama1(self, prompt):
        import torch
        from diffusers.pipelines.ltx2.utils import DISTILLED_SIGMA_VALUES

        return self.pipe(
            prompt=prompt, width=GENISLIK // 2, height=YUKSEKLIK // 2, num_frames=KARE,
            frame_rate=float(FPS), sigmas=DISTILLED_SIGMA_VALUES, guidance_scale=1.0,
            audio_guidance_scale=1.0, generator=torch.Generator("cuda").manual_seed(TOHUM),
            output_type="latent", return_dict=False,
        )

    def _asama2(self, prompt, latent, ses, adain):
        import torch
        from diffusers.pipelines.ltx2.utils import STAGE_2_DISTILLED_SIGMA_VALUES

        buyuk = self.buyut(latents=latent, latents_normalized=False, adain_factor=adain,
                           output_type="latent", return_dict=False)[0]
        return self.pipe(
            prompt=prompt, latents=buyuk, audio_latents=ses, width=GENISLIK, height=YUKSEKLIK,
            num_frames=KARE, frame_rate=float(FPS), sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
            noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0], guidance_scale=1.0, audio_guidance_scale=1.0,
            generator=torch.Generator("cuda").manual_seed(TOHUM + 1), output_type="latent",
            return_dict=False,
        )[0]

    @modal.method()
    def calistir(self):
        sonuc = {}
        sureler = {}

        bas = time.time()
        l1, ses = self._asama1(UZUN_PROMPT)
        sureler["asama1"] = time.time() - bas
        sonuc["A_yarim"] = self._coz(l1, tiling=True)

        bas = time.time()
        l2 = self._asama2(UZUN_PROMPT, l1, ses, adain=0.0)
        sureler["asama2"] = time.time() - bas
        sonuc["B_mevcut"] = self._coz(l2, tiling=True)
        try:
            sonuc["C_tilingsiz"] = self._coz(l2, tiling=False)
        except Exception as e:  # bellek yetmezse
            print("C_tilingsiz çözülemedi:", type(e).__name__, e)
        import torch
        torch.cuda.empty_cache()

        l2d = self._asama2(UZUN_PROMPT, l1, ses, adain=1.0)
        sonuc["D_adain"] = self._coz(l2d, tiling=True)

        k1, kses = self._asama1(KISA_PROMPT)
        k2 = self._asama2(KISA_PROMPT, k1, kses, adain=0.0)
        sonuc["E_kisa_prompt"] = self._coz(k2, tiling=True)

        return {ad: _paketle(ad, v) for ad, v in sonuc.items()} | {"_sureler": sureler}


def _paketle(ad, video):
    """mp4, örnek kare şeridi ve kare kare ölçümler."""
    import tempfile

    import numpy as np
    from diffusers.utils import encode_video
    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        encode_video(video, fps=FPS, output_path=f.name)
        mp4 = Path(f.name).read_bytes()

    kareler = [Image.fromarray((video[min(i, len(video) - 1)] * 255).clip(0, 255).astype("uint8"))
               for i in ORNEK_KARELER]
    h = 640
    kareler = [k.resize((round(k.width * h / k.height), h)) for k in kareler]
    serit = Image.new("RGB", (sum(k.width for k in kareler) + 8 * (len(kareler) - 1), h), "white")
    x = 0
    for k in kareler:
        serit.paste(k, (x, 0))
        x += k.width + 8
    buf = io.BytesIO()
    serit.save(buf, format="JPEG", quality=90)

    # Parlaklık ve doygunluk: kararma / renk kayması zamanla artıyor mu?
    olcum = []
    for i in range(0, len(video), 10):
        k = video[i]
        parlaklik = float((0.2126 * k[..., 0] + 0.7152 * k[..., 1] + 0.0722 * k[..., 2]).mean())
        doygunluk = float((k.max(-1) - k.min(-1)).mean())
        karanlik = float(((k.max(-1)) < 0.08).mean())  # neredeyse siyah piksel oranı
        olcum.append((i, round(parlaklik, 3), round(doygunluk, 3), round(karanlik, 3)))
    return {"mp4": mp4, "serit": buf.getvalue(), "olcum": olcum, "boyut": list(video.shape)}


@app.local_entrypoint()
def main():
    cikti = Path("tani-cikti")
    cikti.mkdir(exist_ok=True)
    bas = time.time()
    sonuc = Tani().calistir.remote()
    print(f"Toplam: {time.time() - bas:.0f} sn, aşama süreleri: {sonuc.pop('_sureler')}")
    for ad, v in sonuc.items():
        (cikti / f"{ad}.mp4").write_bytes(v["mp4"])
        (cikti / f"{ad}.jpg").write_bytes(v["serit"])
        print(f"\n== {ad}  boyut={v['boyut']}")
        print("kare  parlaklık  doygunluk  siyah_oranı")
        for i, p, d, s in v["olcum"]:
            print(f"{i:4d}  {p:9.3f}  {d:9.3f}  {s:10.3f}")
