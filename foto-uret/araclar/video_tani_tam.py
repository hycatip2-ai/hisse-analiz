"""Video kalitesi teşhisi 2: rehberli tam (SFT) model.

video_tani.py ile aynı iki açıklamayı ve tohumu kullanır; yalnızca model değişir:
  1. aşama: transformer_full, 30 adım, CFG + STG + modality rehberliği, negatif liste
  2. aşama: 2x büyütme + distilled LoRA ile 3 adım (belgedeki "Two-stages Generation")

Çalıştırma (GPU kredisi harcar, ~1 $):  modal run araclar/video_tani_tam.py
Çıktılar ./tani-cikti/ klasörüne yazılır (F_tam_uzun, G_tam_kisa).
"""

import time
from pathlib import Path

import modal

from video_tani import (
    FPS,
    GENISLIK,
    KARE,
    KISA_PROMPT,
    TOHUM,
    UZUN_PROMPT,
    VIDEO_DIR,
    VIDEO_MODEL_ID,
    VIDEO_YOL,
    _paketle,
    hf_secret,
    video_modeller,
    YUKSEKLIK,
)

LORA = "ltx-2.5-22b-distilled-lora-450-bf16.safetensors"

app = modal.App("foto-tani-tam")
gpu_image = modal.Image.debian_slim(python_version="3.12").pip_install(
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
    "peft",  # LoRA yükleme için
).add_local_python_source("video_tani")
indirme_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub")


@app.function(image=indirme_image, volumes={VIDEO_DIR: video_modeller}, secrets=[hf_secret],
              timeout=3600)
def ek_dosyalari_indir():
    """Tam transformer ve 2. aşama LoRA'sını GPU'suz makinede indirir."""
    import os

    from huggingface_hub import snapshot_download

    snapshot_download(VIDEO_MODEL_ID, local_dir=VIDEO_YOL, allow_patterns=["transformer_full/*", LORA],
                      token=os.environ.get("HF_TOKEN") or None)
    video_modeller.commit()


@app.cls(gpu="H200", image=gpu_image, volumes={VIDEO_DIR: video_modeller}, timeout=3600)
class TamTani:
    @modal.enter()
    def yukle(self):
        import torch
        from diffusers import LTX2LatentUpsamplePipeline, LTX2Pipeline, LTX2VideoTransformer3DModel
        from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel

        transformer = LTX2VideoTransformer3DModel.from_pretrained(
            VIDEO_YOL, subfolder="transformer_full", torch_dtype=torch.bfloat16
        )
        self.pipe = LTX2Pipeline.from_pretrained(
            VIDEO_YOL, transformer=transformer, torch_dtype=torch.bfloat16,
            prompt_enhancer=None, processor=None,
        ).to("cuda")
        self.pipe.vae.enable_tiling()
        self.dagitik_ayar = self.pipe.scheduler.config
        buyutucu = LTX2LatentUpsamplerModel.from_pretrained(
            VIDEO_YOL, subfolder="latent_upsampler", torch_dtype=torch.bfloat16
        ).to("cuda")
        self.buyut = LTX2LatentUpsamplePipeline(vae=self.pipe.vae, latent_upsampler=buyutucu)

    @modal.method()
    def calistir(self):
        import torch
        from diffusers import FlowMatchEulerDiscreteScheduler
        from diffusers.pipelines.ltx2.utils import DEFAULT_NEGATIVE_PROMPT, STAGE_2_DISTILLED_SIGMA_VALUES

        sureler = {}
        # 1. aşama: tam model, belgedeki rehberlik ayarları
        self.pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
            self.dagitik_ayar, use_dynamic_shifting=True, shift_terminal=0.1
        )
        asama1 = {}
        for ad, prompt in [("F_tam_uzun", UZUN_PROMPT), ("G_tam_kisa", KISA_PROMPT)]:
            bas = time.time()
            asama1[ad] = (prompt, *self.pipe(
                prompt=prompt, negative_prompt=DEFAULT_NEGATIVE_PROMPT,
                width=GENISLIK // 2, height=YUKSEKLIK // 2, num_frames=KARE, frame_rate=float(FPS),
                num_inference_steps=30, guidance_scale=3.0, stg_scale=1.0, modality_scale=3.0,
                guidance_rescale=0.7, audio_guidance_scale=7.0, audio_stg_scale=1.0,
                audio_modality_scale=3.0, audio_guidance_rescale=0.7,
                spatio_temporal_guidance_blocks=[28], use_cross_timestep=True,
                generator=torch.Generator("cuda").manual_seed(TOHUM),
                output_type="latent", return_dict=False,
            ))
            sureler[f"{ad}_asama1"] = round(time.time() - bas)

        # 2. aşama: distilled LoRA + distilled sigma'lar, rehbersiz
        self.pipe.load_lora_weights(VIDEO_YOL, adapter_name="stage_2_distilled", weight_name=LORA)
        self.pipe.set_adapters("stage_2_distilled", 1.0)
        self.pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
            self.dagitik_ayar, use_dynamic_shifting=False, shift_terminal=None
        )
        sonuc = {}
        for ad, (prompt, latent, ses) in asama1.items():
            bas = time.time()
            buyuk = self.buyut(latents=latent, latents_normalized=False, output_type="latent",
                               return_dict=False)[0]
            video, _ = self.pipe(
                prompt=prompt, latents=buyuk, audio_latents=ses, width=GENISLIK, height=YUKSEKLIK,
                num_frames=KARE, frame_rate=float(FPS), sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
                noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0], guidance_scale=1.0,
                audio_guidance_scale=1.0, generator=torch.Generator("cuda").manual_seed(TOHUM + 1),
                output_type="np", return_dict=False,
            )
            sureler[f"{ad}_asama2"] = round(time.time() - bas)
            sonuc[ad] = _paketle(ad, video[0])
        return sonuc | {"_sureler": sureler}


@app.local_entrypoint()
def main():
    cikti = Path("tani-cikti")
    cikti.mkdir(exist_ok=True)
    bas = time.time()
    ek_dosyalari_indir.remote()
    print(f"Ek dosyalar hazır: {time.time() - bas:.0f} sn")
    bas = time.time()
    sonuc = TamTani().calistir.remote()
    print(f"GPU toplam: {time.time() - bas:.0f} sn, süreler: {sonuc.pop('_sureler')}")
    for ad, v in sonuc.items():
        (cikti / f"{ad}.mp4").write_bytes(v["mp4"])
        (cikti / f"{ad}.jpg").write_bytes(v["serit"])
        print(f"\n== {ad}  boyut={v['boyut']}")
        print("kare  parlaklık  doygunluk  siyah_oranı")
        for i, p, d, s in v["olcum"]:
            print(f"{i:4d}  {p:9.3f}  {d:9.3f}  {s:10.3f}")
