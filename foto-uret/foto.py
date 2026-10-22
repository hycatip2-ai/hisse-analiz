"""Foto Stüdyo: telefondan görsel ve video üretme uygulaması (Modal).

- Uretici: GPU (L40S) üzerinde Z-Image-Turbo ile hızlı fotoğraf üretir ("Hızlı" kalite).
- QwenUretici: GPU (L40S) üzerinde Qwen-Image-2.1 ile en kaliteli fotoğrafı üretir; 10'a kadar
  referans görselle (kişi/ürün kimliği korunur) üretim ve düzenleme yapar.
- FluxUretici: GPU (H200) üzerinde FLUX.2 [dev] ile fotoğraf üretir ("FLUX" kalitesi); referansla
  düzenlemede (kişiyi başka sahneye koyma) karşılaştırmada Qwen'den daha güvenilirdi.
- VideoUretici: GPU (H200) üzerinde LTX-2.5 ile sesli video üretir
  (yazıdan veya bir fotoğraftan). Açıklama önce Gemma 4 ile ayrıntılandırılır;
  1. aşamada tam (SFT) model rehberlikle (CFG + STG + negatif liste) yarım
  çözünürlükte üretir, 2. aşamada 2 kat büyütülüp distilled LoRA ile tam
  çözünürlükte (1920×1088'e kadar) iyileştirilir.
- WanUretici / WanFotodanUretici: GPU (H200) üzerinde Wan 2.2 (A14B) ile yazıdan / fotoğraftan
  sessiz video üretir. Lightning
  (lightx2v) hızlandırıcı LoRA'sıyla 40 adım yerine 4 adım: ~17 dk yerine birkaç dakika.
  Karşılaştırmada insan yüzlerini LTX'ten daha doğal üretti.
- video_modeli_indir / wan_modeli_indir: Video modellerini ucuz CPU'da bir kez indirir.
- arastir: Araştırma asistanı (arastirma.py); ücretsiz OpenRouter modeli + Tavily araması,
  GPU'suz çalışır.
- web: Telefon uygulamasını (PWA) ve API'yi sunar; ucuz CPU üzerinde çalışır,
  GPU'lar yalnızca üretim sırasında açılır.

Kurulum ve kullanım için KURULUM.md dosyasına bakın.
Yayına almak için:  modal deploy foto.py
"""

import io
import os
import random
import uuid
from pathlib import Path

import modal

MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"
MODEL_DIR = "/modeller"
QWEN_MODEL_ID = "Qwen/Qwen-Image-2.1"  # Qwen araştırma lisansı (kişisel kullanım)
QWEN_YOL = f"{MODEL_DIR}/qwen-image-2.1"
FLUX_MODEL_ID = "black-forest-labs/FLUX.2-dev"  # ticari olmayan lisans; HF'de onay + token gerekir
FLUX_YOL = f"{MODEL_DIR}/flux2-dev"
# Kök klasördeki tek parça kopyalar (diffusers alt klasörleri yeterli) ve örnek görseller indirilmez.
FLUX_INDIRME_HARIC = ["flux2-dev.safetensors", "ae.safetensors", "*.png", "*.jpg"]
VIDEO_MODEL_ID = "Lightricks/LTX-2.5-Diffusers"  # erişim için HF hesabı + token gerekir
VIDEO_DIR = "/video-modeller"
VIDEO_YOL = f"{VIDEO_DIR}/ltx-2.5"
ESKI_VIDEO_YOL = f"{VIDEO_DIR}/ltx-2.3"
VIDEO_LORA = "ltx-2.5-22b-distilled-lora-450-bf16.safetensors"  # 2. aşama
# Kullanmadığımız büyük parçalar indirilmez. Distilled transformer (~76 GB) yerine
# tam model + LoRA kullanılıyor: teşhiste distilled model yağlı/sert cilt ve zamanla
# değişen yüzler üretti.
VIDEO_INDIRME_HARIC = [
    "transformer/*",
    "diffusion_decoder/*",
    "temporal_latent_upsampler/*",
]
# Wan 2.2 (Apache 2.0). BF16 kopyası resmi deponun (fp32, ~126 GB) yarısı; yükleme hızlanır.
WAN_MODEL_ID = "linoyts/Wan2.2-T2V-A14B-Diffusers-BF16"
WAN_YOL = f"{VIDEO_DIR}/wan2.2-t2v-bf16"
ESKI_WAN_YOL = f"{VIDEO_DIR}/wan2.2-t2v-a14b"  # karşılaştırmada indirilen fp32 kopya
WAN_LORA_ID = "Kijai/WanVideo_comfy"
WAN_LORA = "Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank128_bf16.safetensors"
# Fotoğraftan video (Canlandır): resmi I2V deposundan küçük parçalar, BF16 transformer'lar ayrı kopyadan.
WAN_I2V_MODEL_ID = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"
WAN_I2V_BF16_ID = "cbensimon/Wan2.2-I2V-A14B-bf16-Diffusers"
WAN_I2V_YOL = f"{VIDEO_DIR}/wan2.2-i2v-bf16"
WAN_I2V_LORA = "Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors"
WAN_FPS = 16
WAN_KARE = 81  # 5 sn; model 81 kareyle eğitildi, daha uzunu bozuluyor
WAN_BOYUTLARI = {"kare": (960, 960), "dikey": (720, 1280), "yatay": (1280, 720)}
WAN_NEGATIF = (
    "Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, "
    "overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, "
    "poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still "
    "picture, messy background, three legs, many people in the background, walking backwards"
)
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
qwen_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "torch",
        "torchvision",  # Qwen3VL işleyicisi içe aktarıyor
        "diffusers==0.41.0",
        "transformers>=5.17",
        "accelerate",
        "safetensors",
        "pillow",
        "huggingface_hub",
    )
    .env({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})
)
flux_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch",
    "torchvision",
    "diffusers==0.41.0",
    "transformers",
    "accelerate",
    "safetensors",
    "pillow",
    "huggingface_hub",
    "sentencepiece",
    "protobuf",
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
    "torchvision",  # Gemma4Processor (prompt geliştirme) bunu içe aktarıyor
    "peft",  # 2. aşama LoRA'sı / Wan hızlandırıcı LoRA'sı
    "ftfy",  # Wan açıklama temizleme
)
indirme_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub")
web_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]")
    .add_local_dir(Path(__file__).parent / "statik", STATIK_DIR)
    .add_local_python_source("arastirma")
)
arastirma_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("httpx")
    .add_local_python_source("arastirma")
)

# Model dosyaları bir kez indirilip burada saklanır;
# her açılışta yeniden indirilmez, böylece süre ve kredi boşa gitmez.
modeller = modal.Volume.from_name("foto-modeller", create_if_missing=True)
video_modeller = modal.Volume.from_name("foto-video-modeller", create_if_missing=True)
sifre_secret = modal.Secret.from_name("foto-sifre")
hf_secret = modal.Secret.from_name("foto-hf")  # HF_TOKEN: LTX-2.5 erişimi için
# KOTA_TOKEN_ID / KOTA_TOKEN_SECRET: Kota ekranının Modal kullanım bilgisini okuması için
kota_secret = modal.Secret.from_name("foto-kota")
AYLIK_KREDI = 30  # Modal Starter planının aylık ücretsiz kredisi ($)
# OPENROUTER_API_KEY / TAVILY_API_KEY: araştırma asistanı için
arastirma_secret = modal.Secret.from_name("foto-arastirma")
# Araştırma ilerlemesi (iş kimliği -> son adımlar) ve günlük istek sayacı
arastirma_durum = modal.Dict.from_name("foto-arastirma-durum", create_if_missing=True)
# Gizlilik: GPU işlerinin girdileri (açıklama, referans fotoğraflar) ve sonuçları Modal'ın iş
# kayıtlarından geçmez; bu geçici kutuya konur. Girdi GPU okuyunca, sonuç telefon teslim alınca
# silinir. Alınmayan kayıtları Modal 7 gün işlem görmeyince kendisi siler.
teslim = modal.Dict.from_name("foto-teslim", create_if_missing=True)


def _teslim_calistir(anahtar: str, fn) -> dict:
    """Girdiyi kutudan alıp hemen siler, sonucu kutuya bırakır; iş kaydında yalnızca bu işaret kalır."""
    args = teslim.pop(f"girdi:{anahtar}")
    teslim[f"sonuc:{anahtar}"] = fn(*args)
    return {"teslim": True}

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
# Video son boyutları; iki aşamalı üretim için 64'ün katı olmalı (1. aşama yarısı).
VIDEO_BOYUTLARI = {
    "kare": (1280, 1280),
    "dikey": (1088, 1920),
    "yatay": (1920, 1088),
}
# Kare sayısı 8k+1 olmalı: 121 kare ≈ 5 sn, 241 kare ≈ 10 sn (24 fps).
VIDEO_KARELERI = {5: 121, 10: 241}


# Qwen-Image-2.1: "En iyi" ~1 MP (L40S'te ~20 sn), "HD" ~2 MP (~40 sn); 4 MP L40S belleğine sığmıyor.
QWEN_BOYUTLARI = {
    "eniyi": {"kare": (1024, 1024), "dikey": (768, 1344), "yatay": (1344, 768), "duvar": (736, 1600)},
    "hd": {"kare": (1440, 1440), "dikey": (1088, 1920), "yatay": (1920, 1088), "duvar": (960, 2080)},
}
QWEN_COZUNURLUK = {"eniyi": 1024, "hd": 1440}  # referans varken boyut referansın oranından türetilir
FLUX_BOYUTLARI = {"kare": (1024, 1024), "dikey": (768, 1344), "yatay": (1344, 768), "duvar": (736, 1600)}


def qwen_mu(kalite: str, referans_var: bool) -> bool:
    """FLUX seçilmediyse: referans görsel varsa ya da en iyi kalite isteniyorsa Qwen, yoksa hızlı Z-Image."""
    return kalite != "flux" and (referans_var or kalite in QWEN_BOYUTLARI)


def referans_boyutu(genislik: int, yukseklik: int, piksel: int = 1024 * 1024) -> tuple[int, int]:
    """Referansın oranını koruyarak ~1 MP, 16'nın katı boyut."""
    oran = genislik / yukseklik
    y = (piksel / oran) ** 0.5
    return max(256, round(y * oran / 16) * 16), max(256, round(y / 16) * 16)


def boyut_hesapla(boyut: str, kalite: str) -> tuple[int, int]:
    if kalite in QWEN_BOYUTLARI:
        return QWEN_BOYUTLARI[kalite].get(boyut, QWEN_BOYUTLARI[kalite]["kare"])
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
    def uret(self, anahtar: str):
        return _teslim_calistir(anahtar, self._uret)

    def _uret(self, prompt: str, genislik: int, yukseklik: int, adet: int, tohum: int | None):
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


def _qwen_modelini_indir():
    from huggingface_hub import snapshot_download

    snapshot_download(QWEN_MODEL_ID, local_dir=QWEN_YOL, ignore_patterns=["assets/*"])


@app.function(image=indirme_image, volumes={MODEL_DIR: modeller}, timeout=3600)
def foto_modeli_indir():
    """Qwen-Image-2.1'i (~33 GB) GPU'suz makinede indirir; zaten varsa hızlıca geçer."""
    _qwen_modelini_indir()
    modeller.commit()
    print("Fotoğraf modeli hazır:", QWEN_YOL)


@app.cls(
    gpu="L40S",
    image=qwen_image,
    volumes={MODEL_DIR: modeller},
    scaledown_window=120,
    max_containers=1,
    timeout=900,
)
class QwenUretici:
    @modal.enter()
    def yukle(self):
        # Yükleme hatası konteyneri çökertmesin (çöken konteyner tekrar tekrar başlatılır).
        import traceback

        self.hata = None
        try:
            import torch
            from diffusers import QwenImage21Pipeline

            _qwen_modelini_indir()
            self.pipe = QwenImage21Pipeline.from_pretrained(QWEN_YOL, torch_dtype=torch.bfloat16).to("cuda")
            if hasattr(self.pipe.vae, "enable_tiling"):
                self.pipe.vae.enable_tiling()
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    def _hata_varsa(self):
        if self.hata:
            raise RuntimeError("Fotoğraf modeli yüklenemedi: " + self.hata.strip().splitlines()[-1])

    @modal.method()
    def isit(self) -> bool:
        self._hata_varsa()
        return True

    @modal.method()
    def uret(self, anahtar: str):
        return _teslim_calistir(anahtar, self._uret)

    def _uret(self, prompt: str, genislik: int | None, yukseklik: int | None, adet: int, tohum: int | None,
              referanslar: list[bytes] | None = None, cozunurluk: int = 1024):
        self._hata_varsa()
        import torch
        from PIL import Image

        if tohum is None:
            tohum = random.randint(0, 2**31 - 1)
        ek = {"width": genislik, "height": yukseklik}
        if referanslar:
            resimler = []
            for r in referanslar[:10]:
                img = Image.open(io.BytesIO(r)).convert("RGB")
                img.thumbnail((2048, 2048))
                resimler.append(img)
            # Boyut, son referansın oranından ve seçilen çözünürlükten türetilir (düzenlemede kadraj korunur).
            ek = {"image": resimler, "output_resolution": cozunurluk}
        sonuc = []
        for i in range(adet):
            torch.cuda.empty_cache()
            img = self.pipe(prompt=prompt, generator=torch.Generator("cuda").manual_seed(tohum + i), **ek).images[0]
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=92)
            sonuc.append({"jpeg": buf.getvalue(), "tohum": tohum + i, "genislik": img.width, "yukseklik": img.height})
        return sonuc


def _flux_modelini_indir():
    from huggingface_hub import snapshot_download

    snapshot_download(FLUX_MODEL_ID, local_dir=FLUX_YOL, ignore_patterns=FLUX_INDIRME_HARIC,
                      token=os.environ.get("HF_TOKEN", "").replace("yok", "") or None)


@app.function(image=indirme_image, volumes={MODEL_DIR: modeller}, secrets=[hf_secret], timeout=3600)
def flux_modeli_indir():
    """FLUX.2 [dev]'i (~110 GB) GPU'suz makinede indirir; zaten varsa hızlıca geçer."""
    _flux_modelini_indir()
    modeller.commit()
    print("FLUX modeli hazır:", FLUX_YOL)


@app.cls(
    gpu="H200",  # 32B transformer + 24B metin kodlayıcı (~112 GB) L40S'e sığmaz
    image=flux_image,
    volumes={MODEL_DIR: modeller},
    secrets=[hf_secret],
    scaledown_window=120,
    max_containers=1,
    timeout=900,
)
class FluxUretici:
    @modal.enter()
    def yukle(self):
        import traceback

        self.hata = None
        try:
            import torch
            from diffusers import Flux2Pipeline

            _flux_modelini_indir()
            self.pipe = Flux2Pipeline.from_pretrained(FLUX_YOL, torch_dtype=torch.bfloat16).to("cuda")
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    def _hata_varsa(self):
        if self.hata:
            raise RuntimeError("FLUX modeli yüklenemedi: " + self.hata.strip().splitlines()[-1])

    @modal.method()
    def isit(self) -> bool:
        self._hata_varsa()
        return True

    @modal.method()
    def uret(self, anahtar: str):
        return _teslim_calistir(anahtar, self._uret)

    def _uret(self, prompt: str, genislik: int | None, yukseklik: int | None, adet: int, tohum: int | None,
              referanslar: list[bytes] | None = None):
        self._hata_varsa()
        import torch
        from PIL import Image

        if tohum is None:
            tohum = random.randint(0, 2**31 - 1)
        ek = {}
        if referanslar:
            resimler = []
            for r in referanslar[:10]:
                img = Image.open(io.BytesIO(r)).convert("RGB")
                img.thumbnail((1536, 1536))
                resimler.append(img)
            ek["image"] = resimler
            # Qwen'deki gibi boyut son referansın oranından türetilir.
            genislik, yukseklik = referans_boyutu(*resimler[-1].size)
        sonuc = []
        for i in range(adet):
            img = self.pipe(prompt=prompt, width=genislik, height=yukseklik, num_inference_steps=50,
                            guidance_scale=4.0, generator=torch.Generator("cuda").manual_seed(tohum + i),
                            **ek).images[0]
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=92)
            sonuc.append({"jpeg": buf.getvalue(), "tohum": tohum + i, "genislik": img.width, "yukseklik": img.height})
        return sonuc


def _video_modelini_indir():
    from huggingface_hub import snapshot_download

    snapshot_download(
        VIDEO_MODEL_ID,
        local_dir=VIDEO_YOL,
        ignore_patterns=VIDEO_INDIRME_HARIC,
        token=os.environ.get("HF_TOKEN", "").replace("yok", "") or None,
    )


@app.function(image=indirme_image, volumes={VIDEO_DIR: video_modeller}, secrets=[hf_secret],
              timeout=3 * 3600)
def video_modeli_indir():
    """Video modelini GPU'suz (ucuz) bir makinede indirir; zaten varsa hızlıca geçer."""
    import shutil

    _video_modelini_indir()
    # Artık kullanılmayan dosyalar: önceki LTX-2.3 ve distilled transformer.
    shutil.rmtree(ESKI_VIDEO_YOL, ignore_errors=True)
    shutil.rmtree(f"{VIDEO_YOL}/transformer", ignore_errors=True)
    video_modeller.commit()
    print("Video modeli hazır:", VIDEO_YOL)


@app.cls(
    gpu="H200",
    image=video_gpu_image,
    volumes={VIDEO_DIR: video_modeller},
    secrets=[hf_secret],
    scaledown_window=120,
    max_containers=1,
    timeout=1800,
)
class VideoUretici:
    @modal.enter()
    def yukle(self):
        # Yükleme hatası olursa konteyner çökmesin: Modal çökeni tekrar tekrar başlatır,
        # her seferinde ~115 GB yükleyip GPU süresi yakar. Hata saklanır, istek hemen döner.
        import traceback

        self.hata = None
        try:
            self._yukle()
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    def _yukle(self):
        import torch
        from diffusers import (
            FlowMatchEulerDiscreteScheduler,
            LTX2ImageToVideoPipeline,
            LTX2LatentUpsamplePipeline,
            LTX2Pipeline,
            LTX2VideoTransformer3DModel,
        )
        from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel
        from transformers import AutoModelForImageTextToText, AutoProcessor

        # Normalde video_modeli_indir zaten indirmiştir; eksik dosya varsa tamamlar.
        _video_modelini_indir()
        transformer = LTX2VideoTransformer3DModel.from_pretrained(
            VIDEO_YOL, subfolder="transformer_full", torch_dtype=torch.bfloat16
        )
        # Prompt geliştirici ayrıca yüklenir; yüklenemezse video yine de üretilir.
        self.yazidan = LTX2Pipeline.from_pretrained(
            VIDEO_YOL, transformer=transformer, torch_dtype=torch.bfloat16,
            prompt_enhancer=None, processor=None,
        ).to("cuda")
        self.yazidan.vae.enable_tiling()
        self.yazidan.load_lora_weights(VIDEO_YOL, adapter_name="asama2", weight_name=VIDEO_LORA)
        self.yazidan.set_adapters("asama2", 1.0)
        self.fotodan = LTX2ImageToVideoPipeline(**self.yazidan.components)
        # Depodaki zamanlayıcı distilled model için ayarlı; 1. aşama (tam model) dinamik kaydırma ister.
        ayar = self.yazidan.scheduler.config
        self.zamanlayici1 = FlowMatchEulerDiscreteScheduler.from_config(
            ayar, use_dynamic_shifting=True, shift_terminal=0.1
        )
        self.zamanlayici2 = FlowMatchEulerDiscreteScheduler.from_config(
            ayar, use_dynamic_shifting=False, shift_terminal=None
        )
        buyutucu = LTX2LatentUpsamplerModel.from_pretrained(
            VIDEO_YOL, subfolder="latent_upsampler", torch_dtype=torch.bfloat16
        ).to("cuda")
        self.buyut = LTX2LatentUpsamplePipeline(vae=self.yazidan.vae, latent_upsampler=buyutucu)

        self.gelistirici_hata = None
        try:
            gelistirici = AutoModelForImageTextToText.from_pretrained(
                f"{VIDEO_YOL}/prompt_enhancer", torch_dtype=torch.bfloat16
            ).to("cuda")
            isleyici = AutoProcessor.from_pretrained(f"{VIDEO_YOL}/processor")
            for pipe in (self.yazidan, self.fotodan):
                pipe.prompt_enhancer = gelistirici
                pipe.processor = isleyici
        except Exception as e:
            import traceback

            self.gelistirici_hata = f"{type(e).__name__}: {e}"
            print("Prompt geliştirici yüklenemedi; açıklama olduğu gibi kullanılacak.\n" + traceback.format_exc())

    def _hata_varsa(self):
        if self.hata:
            son = self.hata.strip().splitlines()[-1]
            raise RuntimeError(f"Video modeli yüklenemedi: {son}")

    @modal.method()
    def isit(self) -> bool:
        self._hata_varsa()
        return True

    @modal.method()
    def uret(self, anahtar: str):
        return _teslim_calistir(anahtar, self._uret)

    def _uret(self, prompt: str, genislik: int, yukseklik: int, kare: int, tohum: int | None,
              resim: bytes | None):
        self._hata_varsa()
        import tempfile

        import torch
        from diffusers.pipelines.ltx2.utils import (
            DEFAULT_NEGATIVE_PROMPT,
            LTX2_5_I2V_DEFAULT_SYSTEM_PROMPT,
            LTX2_5_T2V_DEFAULT_SYSTEM_PROMPT,
            STAGE_2_DISTILLED_SIGMA_VALUES,
        )
        from diffusers.utils import encode_video
        from PIL import Image, ImageOps

        if tohum is None:
            tohum = random.randint(0, 2**31 - 1)
        pipe, ek, img = self.yazidan, {}, None
        if resim:
            img = Image.open(io.BytesIO(resim)).convert("RGB")
            img = ImageOps.fit(img, (genislik, yukseklik), Image.LANCZOS)
            pipe, ek = self.fotodan, {"image": img}

        # Kısa açıklamayı modelin eğitildiği türde ayrıntılı sahne tarifine çevir
        # (LTX-2.5 belgesi: "strongly recommended"). İki aşamada da aynı metin kullanılır.
        gelismis = prompt
        if self.gelistirici_hata is None:
            try:
                gelismis = pipe.enhance_prompt(
                    prompt=prompt,
                    system_prompt=LTX2_5_I2V_DEFAULT_SYSTEM_PROMPT if img else LTX2_5_T2V_DEFAULT_SYSTEM_PROMPT,
                    image=img,
                    device="cuda",
                )[0] or prompt
            except Exception as e:
                # Gizlilik: hata mesajı açıklamadan parça içerebilir; yalnızca türü yazılır.
                print("Prompt geliştirme başarısız, açıklama olduğu gibi kullanılıyor:", type(e).__name__)

        ortak = dict(prompt=gelismis, num_frames=kare, frame_rate=float(VIDEO_FPS), return_dict=False, **ek)

        # 1. aşama: tam model, yarım çözünürlük, belgedeki rehberlik ayarları (LoRA kapalı).
        pipe.disable_lora()
        pipe.scheduler = self.zamanlayici1
        video_latent, ses_latent = pipe(
            negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            width=genislik // 2,
            height=yukseklik // 2,
            num_inference_steps=30,
            guidance_scale=3.0,
            stg_scale=1.0,
            modality_scale=3.0,
            guidance_rescale=0.7,
            audio_guidance_scale=7.0,
            audio_stg_scale=1.0,
            audio_modality_scale=3.0,
            audio_guidance_rescale=0.7,
            spatio_temporal_guidance_blocks=[28],
            use_cross_timestep=True,
            generator=torch.Generator("cuda").manual_seed(tohum),
            output_type="latent",
            **ortak,
        )
        # Gizli uzayda 2 kat büyüt.
        buyuk_latent = self.buyut(
            latents=video_latent, latents_normalized=False, output_type="latent", return_dict=False
        )[0]
        # 2. aşama: distilled LoRA ile tam çözünürlükte ayrıntıları iyileştir (3 adım, rehbersiz).
        pipe.enable_lora()
        pipe.scheduler = self.zamanlayici2
        video, ses = pipe(
            latents=buyuk_latent,
            audio_latents=ses_latent,
            width=genislik,
            height=yukseklik,
            sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
            noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
            guidance_scale=1.0,
            audio_guidance_scale=1.0,
            generator=torch.Generator("cuda").manual_seed(tohum + 1),
            output_type="np",
            **ortak,
        )

        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            encode_video(
                video[0],
                fps=VIDEO_FPS,
                audio=ses[0].float().cpu(),
                audio_sample_rate=self.yazidan.vocoder.config.output_sampling_rate,
                output_path=f.name,
            )
            mp4 = Path(f.name).read_bytes()
        return {"mp4": mp4, "tohum": tohum, "gelismis_prompt": gelismis}


def _wan_modelini_indir():
    from huggingface_hub import hf_hub_download, snapshot_download

    snapshot_download(WAN_MODEL_ID, local_dir=WAN_YOL)
    hf_hub_download(WAN_LORA_ID, WAN_LORA, local_dir=f"{WAN_YOL}/lora")


def _wan_i2v_modelini_indir():
    from huggingface_hub import hf_hub_download, snapshot_download

    # Resmi depodaki fp32 transformer'lar (~115 GB) yerine BF16 kopyaları (~57 GB) indirilir.
    snapshot_download(WAN_I2V_MODEL_ID, local_dir=WAN_I2V_YOL,
                      ignore_patterns=["transformer/*", "transformer_2/*", "assets/*", "examples/*"])
    snapshot_download(WAN_I2V_BF16_ID, local_dir=WAN_I2V_YOL, allow_patterns=["transformer/*", "transformer_2/*"])
    hf_hub_download(WAN_LORA_ID, WAN_I2V_LORA, local_dir=f"{WAN_I2V_YOL}/lora")


@app.function(image=indirme_image, volumes={VIDEO_DIR: video_modeller}, timeout=3 * 3600)
def wan_modeli_indir():
    """Wan 2.2 yazıdan (~69 GB) ve fotoğraftan (~70 GB) modellerini ve hızlandırıcı LoRA'ları GPU'suz makinede indirir; zaten varsa hızlıca geçer."""
    import shutil

    _wan_modelini_indir()
    _wan_i2v_modelini_indir()
    shutil.rmtree(ESKI_WAN_YOL, ignore_errors=True)
    video_modeller.commit()
    print("Wan modelleri hazır:", WAN_YOL, WAN_I2V_YOL)


def _wan_yukle(pipe_sinifi, yol: str, lora_adi: str, yuksek_agirlik: float):
    import torch
    from diffusers import AutoencoderKLWan

    vae = AutoencoderKLWan.from_pretrained(yol, subfolder="vae", torch_dtype=torch.float32)
    pipe = pipe_sinifi.from_pretrained(yol, vae=vae, torch_dtype=torch.bfloat16).to("cuda")
    # Lightning (lightx2v adım damıtma) LoRA'sı iki uzmana da (yüksek ve düşük gürültü) yüklenip
    # kalıcı olarak birleştirilir; her adımda LoRA hesabı yapılmaz. Ağırlıklar Hugging Face'teki
    # zerogpu-aoti/wan2-2-fp8da-aoti(-faster) tarifinden.
    pipe.load_lora_weights(f"{yol}/lora", weight_name=lora_adi, adapter_name="hizli")
    pipe.load_lora_weights(f"{yol}/lora", weight_name=lora_adi, adapter_name="hizli_2",
                           load_into_transformer_2=True)
    pipe.set_adapters(["hizli", "hizli_2"], adapter_weights=[1.0, 1.0])
    pipe.fuse_lora(adapter_names=["hizli"], lora_scale=yuksek_agirlik, components=["transformer"])
    pipe.fuse_lora(adapter_names=["hizli_2"], lora_scale=1.0, components=["transformer_2"])
    pipe.unload_lora_weights()
    return pipe


def _wan_uret(pipe, tohum: int | None, **ek) -> dict:
    import tempfile

    import torch
    from diffusers.utils import encode_video

    if tohum is None:
        tohum = random.randint(0, 2**31 - 1)
    kareler = pipe(negative_prompt=WAN_NEGATIF, num_frames=WAN_KARE, num_inference_steps=4,
                   generator=torch.Generator("cuda").manual_seed(tohum), **ek).frames[0]
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        encode_video(kareler, fps=WAN_FPS, output_path=f.name)
        mp4 = Path(f.name).read_bytes()
    return {"mp4": mp4, "tohum": tohum, "gelismis_prompt": None}


@app.cls(
    gpu="H200",
    image=video_gpu_image,
    volumes={VIDEO_DIR: video_modeller},
    scaledown_window=120,
    max_containers=1,
    timeout=1800,
)
class WanUretici:
    """Yazıdan video."""

    @modal.enter()
    def yukle(self):
        import traceback

        self.hata = None
        try:
            from diffusers import WanPipeline

            _wan_modelini_indir()
            self.pipe = _wan_yukle(WanPipeline, WAN_YOL, WAN_LORA, 3.0)
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    def _hata_varsa(self):
        if self.hata:
            raise RuntimeError("Wan modeli yüklenemedi: " + self.hata.strip().splitlines()[-1])

    @modal.method()
    def isit(self) -> bool:
        self._hata_varsa()
        return True

    @modal.method()
    def uret(self, anahtar: str):
        return _teslim_calistir(anahtar, self._uret)

    def _uret(self, prompt: str, genislik: int, yukseklik: int, tohum: int | None):
        self._hata_varsa()
        return _wan_uret(
            self.pipe, tohum, prompt=prompt, width=genislik, height=yukseklik,
            guidance_scale=1.0,  # yüksek gürültü uzmanı: rehbersiz (LoRA rehberliği damıttı)
            guidance_scale_2=3.0,  # düşük gürültü uzmanı: hafif rehberlik ayrıntıyı artırıyor
        )


@app.cls(
    gpu="H200",
    image=video_gpu_image,
    volumes={VIDEO_DIR: video_modeller},
    scaledown_window=120,
    max_containers=1,
    timeout=1800,
)
class WanFotodanUretici:
    """Fotoğraftan video (Canlandır)."""

    @modal.enter()
    def yukle(self):
        import traceback

        self.hata = None
        try:
            from diffusers import WanImageToVideoPipeline

            _wan_i2v_modelini_indir()
            self.pipe = _wan_yukle(WanImageToVideoPipeline, WAN_I2V_YOL, WAN_I2V_LORA, 3.0)
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    def _hata_varsa(self):
        if self.hata:
            raise RuntimeError("Wan (fotoğraftan) modeli yüklenemedi: " + self.hata.strip().splitlines()[-1])

    @modal.method()
    def isit(self) -> bool:
        self._hata_varsa()
        return True

    @modal.method()
    def uret(self, anahtar: str):
        return _teslim_calistir(anahtar, self._uret)

    def _uret(self, prompt: str, genislik: int, yukseklik: int, tohum: int | None, resim: bytes):
        self._hata_varsa()
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(resim)).convert("RGB")
        img = ImageOps.fit(img, (genislik, yukseklik), Image.LANCZOS)
        return _wan_uret(self.pipe, tohum, image=img, prompt=prompt, width=genislik, height=yukseklik,
                         guidance_scale=1.0, guidance_scale_2=1.0)


def _sayac_anahtari() -> str:
    """Günlük ücretsiz istek sayacının anahtarı (OpenRouter günü UTC'ye göre sayar)."""
    from datetime import datetime, timezone

    return "sayac:" + datetime.now(timezone.utc).date().isoformat()


@app.function(image=arastirma_image, secrets=[arastirma_secret], timeout=900)
def arastir(anahtar: str) -> dict:
    return _teslim_calistir(anahtar, _arastir)


def _arastir(tur: str, mesajlar: list[dict], hedef: str) -> dict:
    """Araştırma asistanını çalıştırır; ilerlemeyi telefon görebilsin diye Dict'e yazar."""
    import arastirma

    kimlik = modal.current_function_call_id()
    gun = _sayac_anahtari()
    adimlar: list[str] = []

    def ilerleme(metin):
        adimlar.append(metin)
        arastirma_durum[kimlik] = adimlar[-8:]

    def sayac():
        arastirma_durum[gun] = arastirma_durum.get(gun, 0) + 1

    try:
        sonuc = arastirma.yurut(tur, mesajlar, hedef, arastirma.openrouter_llm(sayac),
                                arastirma.tavily_ara(), ilerleme)
    except arastirma.KullaniciHatasi as e:
        sonuc = {"tur": tur, "hata": str(e)}
    finally:
        arastirma_durum.pop(kimlik, None)
    sonuc["bugun_istek"] = arastirma_durum.get(gun, 0)
    return sonuc


def _uzak(metot, *args):
    """Denemeler için: uygulamadaki gibi girdiyi kutuya koy, çalıştır, sonucu kutudan al ve sil."""
    anahtar = uuid.uuid4().hex
    teslim[f"girdi:{anahtar}"] = args
    metot.remote(anahtar)
    return teslim.pop(f"sonuc:{anahtar}")


@app.local_entrypoint()
def flux_dene():
    """FLUX.2 [dev]'i uygulamadaki yoldan dener (yazıdan + referanslı): modal run foto.py::flux_dene"""
    import time

    Path("tani-cikti").mkdir(exist_ok=True)
    bas = time.time()
    g, y = FLUX_BOYUTLARI["dikey"]
    ilk = _uzak(FluxUretici().uret, "a smiling woman with short black hair and a green coat at a flower market, "
                "realistic photo", g, y, 1, 5)
    print(f"yazıdan: {ilk[0]['genislik']}x{ilk[0]['yukseklik']}, {time.time() - bas:.0f} sn (yükleme dahil)")
    Path("tani-cikti/flux_yazidan.jpg").write_bytes(ilk[0]["jpeg"])
    bas = time.time()
    ref = _uzak(FluxUretici().uret, "The same woman from the reference image, now hiking on a mountain trail, "
                "wearing a red jacket", None, None, 1, 6, [ilk[0]["jpeg"]])
    print(f"referanslı: {ref[0]['genislik']}x{ref[0]['yukseklik']}, {time.time() - bas:.0f} sn")
    Path("tani-cikti/flux_referansli.jpg").write_bytes(ref[0]["jpeg"])


@app.local_entrypoint()
def foto_dene():
    """Qwen-Image-2.1'i uygulamadaki yoldan dener (yazıdan + referanslı): modal run foto.py::foto_dene"""
    import time

    Path("tani-cikti").mkdir(exist_ok=True)
    bas = time.time()
    g, y = QWEN_BOYUTLARI["eniyi"]["dikey"]
    ilk = _uzak(QwenUretici().uret, "a smiling man with a red scarf in a snowy park, realistic photo", g, y, 1, 5)
    print(f"yazıdan: {ilk[0]['genislik']}x{ilk[0]['yukseklik']}, {time.time() - bas:.0f} sn (yükleme dahil)")
    Path("tani-cikti/foto_yazidan.jpg").write_bytes(ilk[0]["jpeg"])
    bas = time.time()
    ref = _uzak(QwenUretici().uret, "The same man from the reference, now sitting in a cozy library reading a book",
                None, None, 1, 6, [ilk[0]["jpeg"]], QWEN_COZUNURLUK["eniyi"])
    print(f"referanslı: {ref[0]['genislik']}x{ref[0]['yukseklik']}, {time.time() - bas:.0f} sn")
    Path("tani-cikti/foto_referansli.jpg").write_bytes(ref[0]["jpeg"])


@app.local_entrypoint()
def wan_dene():
    """Wan 2.2 hızlı modunu uygulamadaki yoldan dener: modal run foto.py::wan_dene"""
    import time

    Path("tani-cikti").mkdir(exist_ok=True)
    bas = time.time()
    WanUretici().isit.remote()
    print(f"Wan yüklendi: {time.time() - bas:.0f} sn")
    denemeler = {
        "insan": ("A young woman with curly red hair laughs and talks to a friend at a busy cafe table, "
                  "close-up, natural window light", "yatay"),
        "hareket": ("A golden retriever runs along a beach at sunset, splashing through shallow waves, "
                    "the camera tracking alongside", "yatay"),
        "dikey": ("An elderly man in a wool coat walks through a snowy park, turns to the camera and smiles, "
                  "soft winter light", "dikey"),
    }
    for ad, (prompt, boyut) in denemeler.items():
        g, y = WAN_BOYUTLARI[boyut]
        bas = time.time()
        sonuc = _uzak(WanUretici().uret, prompt, g, y, 42)
        print(f"Wan {ad}: {g}x{y}, {time.time() - bas:.0f} sn")
        Path(f"tani-cikti/wan_hizli_{ad}.mp4").write_bytes(sonuc["mp4"])


@app.local_entrypoint()
def canlandir_dene():
    """Fotoğraftan video: Qwen ile fotoğraf üretir, Wan ve LTX ile canlandırır: modal run foto.py::canlandir_dene"""
    import time

    Path("tani-cikti").mkdir(exist_ok=True)
    denemeler = {
        "kadin": ("A woman in her thirties with shoulder-length brown hair and a cream knit sweater sitting by a "
                  "cafe window, realistic photo", "dikey",
                  "She looks up from her coffee, smiles warmly at the camera and tucks her hair behind her ear"),
        "aile": ("A grandfather and his young granddaughter on a park bench in autumn, realistic photo", "yatay",
                 "The girl laughs and points at the falling leaves, the grandfather smiles and hugs her"),
    }
    for ad, (foto_prompt, boyut, hareket) in denemeler.items():
        g, y = QWEN_BOYUTLARI["eniyi"][boyut]
        foto = _uzak(QwenUretici().uret, foto_prompt, g, y, 1, 11)[0]["jpeg"]
        Path(f"tani-cikti/canlandir_{ad}.jpg").write_bytes(foto)
        g, y = WAN_BOYUTLARI[boyut]
        bas = time.time()
        v = _uzak(WanFotodanUretici().uret, hareket, g, y, 42, foto)
        print(f"Wan fotoğraftan {ad}: {g}x{y}, {time.time() - bas:.0f} sn")
        Path(f"tani-cikti/canlandir_{ad}_wan.mp4").write_bytes(v["mp4"])
        g, y = VIDEO_BOYUTLARI[boyut]
        bas = time.time()
        v = _uzak(VideoUretici().uret, hareket, g, y, VIDEO_KARELERI[5], 42, foto)
        print(f"LTX fotoğraftan {ad}: {g}x{y}, {time.time() - bas:.0f} sn")
        Path(f"tani-cikti/canlandir_{ad}_ltx.mp4").write_bytes(v["mp4"])


@app.local_entrypoint()
def video_dene():
    """Kısa, düşük çözünürlüklü bir video üretip ölçer: modal run foto.py::video_dene"""
    import time

    bas = time.time()
    VideoUretici().isit.remote()
    print(f"Model yüklendi: {time.time() - bas:.0f} sn")
    g, y = VIDEO_BOYUTLARI["dikey"]
    bas = time.time()
    sonuc = _uzak(VideoUretici().uret, "a woman smiling and talking to the camera, close-up", g, y,
                  VIDEO_KARELERI[5], 1234, None)
    print(f"tam boy 5 sn: {g}x{y} -> {len(sonuc['mp4']) / 1e6:.1f} MB, {time.time() - bas:.0f} sn")
    print("Geliştirilmiş açıklama:", sonuc["gelismis_prompt"])
    Path("tani-cikti").mkdir(exist_ok=True)
    Path("tani-cikti/video_dene.mp4").write_bytes(sonuc["mp4"])


def kota_bilgisi() -> dict:
    """Bu ayın Modal kullanımını özetler (Kota ekranı için)."""
    from datetime import datetime, timezone

    client = modal.Client.from_credentials(os.environ["KOTA_TOKEN_ID"], os.environ["KOTA_TOKEN_SECRET"])
    calisma = modal.Workspace.from_context(client=client)
    ozet = calisma.billing.summary()
    ay_basi = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    kaynaklar: dict[str, float] = {}
    gunluk: dict[str, float] = {}
    # Günlük rapor tamamlanmamış günü (bugünü) içermez; saatlik rapor ise en fazla 7 gün
    # kapsar. Bu yüzden ayın geçmiş günleri günlük, bugün saatlik okunur.
    bugun_basi = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    satirlar = []
    if bugun_basi > ay_basi:
        satirlar += calisma.billing.report(start=ay_basi, end=bugun_basi, resolution="d")
    satirlar += calisma.billing.report(start=bugun_basi, resolution="h")
    for satir in satirlar:
        gun = satir.interval_start.date().isoformat()
        gunluk[gun] = gunluk.get(gun, 0.0) + float(satir.cost)
        for kaynak, tutar in satir.cost_by_resource.items():
            kaynaklar[kaynak] = kaynaklar.get(kaynak, 0.0) + float(tutar)
    return {
        "kredi": AYLIK_KREDI,
        "kullanilan": float(ozet.metered_cost),
        "odenecek": float(ozet.billed_cost),
        "kaynaklar": kaynaklar,
        "gunluk": gunluk,
        "donem_bitis": ozet.end.isoformat() if ozet.end else None,
    }


def uygulama_olustur(sifre: str, baslat, sonuc_al, statik_dir: str, kota_al=None,
                     ilerleme_al=None, istek_sayaci=None, teslim_onayla=None):
    """Web uygulamasını kurar. Yerel testte sahte fonksiyonlar verilebilir.

    GPU işleri arka planda başlatılır ve iş kimliği hemen döner; telefon
    sonucu /api/is/{kimlik} ile birkaç saniyede bir sorar. Böylece hiçbir
    istek uzun sürmez (Modal 150 sn'yi aşan istekleri yönlendirir ve
    tarayıcıda "invalid function call" hatası çıkabiliyordu).

    baslat(is_turu, *args) -> iş kimliği; is_turu: foto, qwen, video, wan, arastir, isit_*
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

    # Modal dosyaları 1970 tarihli sunar; Cache-Control olmazsa tarayıcı bunları
    # yıllarca taze sayıp eski app.js'i kullanır. Her seferinde ETag ile doğrulat.
    @api.middleware("http")
    async def onbellek(request, call_next):
        yanit = await call_next(request)
        if request.url.path.startswith("/static/"):
            yanit.headers["Cache-Control"] = "no-cache"
        return yanit

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
        kalite: str = "eniyi"
        video_model: Literal["ltx", "wan"] = "ltx"
        fotodan: bool = False  # Wan'da fotoğraftan video ayrı GPU'da çalışır

    class Istek(BaseModel):
        tur: Literal["foto", "video"] = "foto"
        prompt: str = Field(min_length=1, max_length=2000)
        mod: str = "serbest"
        boyut: str = "kare"
        kalite: Literal["hizli", "standart", "yuksek", "eniyi", "hd", "flux"] = "eniyi"
        adet: int = Field(default=1, ge=1, le=4)
        tohum: int | None = Field(default=None, ge=0, le=2**31 - 5)
        sure: Literal[5, 10] = 5
        # Video modeli: ltx = LTX-2.5 (sesli, 5-10 sn), wan = Wan 2.2 hızlı (sessiz, 5 sn); ikisi de fotoğraftan
        video_model: Literal["ltx", "wan"] = "ltx"
        resim: str | None = Field(default=None, max_length=12_000_000)  # base64 JPEG/PNG
        # Fotoğraf için referans görseller (base64); varsa Qwen-Image-2.1 kullanılır.
        referanslar: list[str] = Field(default_factory=list, max_length=10)

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

    class Mesaj(BaseModel):
        rol: Literal["kullanici", "asistan"]
        metin: str = Field(min_length=1, max_length=30000)

    class Arastirma(BaseModel):
        tur: Literal["soru", "arastirma", "prompt"] = "soru"
        mesajlar: list[Mesaj] = Field(min_length=1, max_length=40)
        hedef: Literal["foto", "video"] = "foto"

    @api.post("/api/arastir", dependencies=[Depends(oturum_kontrol)])
    def arastir_baslat(a: Arastirma):
        if a.mesajlar[-1].rol != "kullanici":
            raise HTTPException(status_code=400, detail="Son mesaj kullanıcının olmalı")
        return {"is": baslat("arastir", a.tur, [m.model_dump() for m in a.mesajlar], a.hedef)}

    @api.get("/api/arastirma-sayac", dependencies=[Depends(oturum_kontrol)])
    def arastirma_sayac():
        import arastirma

        return {"bugun": istek_sayaci() if istek_sayaci else 0, "limit": arastirma.GUNLUK_UCRETSIZ_ISTEK}

    @api.get("/api/kota", dependencies=[Depends(oturum_kontrol)])
    def kota():
        if kota_al is None:
            raise HTTPException(status_code=503, detail="Kota bilgisi ayarlanmamış")
        try:
            return kota_al()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Kota okunamadı: {type(e).__name__}: {e}")

    @api.post("/api/isit", dependencies=[Depends(oturum_kontrol)])
    def isit(i: Isit | None = None):
        i = i or Isit()
        if i.tur == "foto" and i.kalite == "flux":
            return {"is": baslat("isit_flux")}
        if i.tur == "foto" and qwen_mu(i.kalite, False):
            return {"is": baslat("isit_qwen")}
        if i.tur == "video" and i.video_model == "wan":
            return {"is": baslat("isit_wan_fotodan" if i.fotodan else "isit_wan")}
        return {"is": baslat("isit_" + i.tur)}

    @api.post("/api/uret", dependencies=[Depends(oturum_kontrol)])
    def uret(istek: Istek):
        if not istek.prompt.strip():
            raise HTTPException(status_code=400, detail="Açıklama boş")
        if istek.tur == "video":
            # Mod ekleri fotoğraf terimleri; video modeli onları sahneye nesne olarak koyuyor.
            prompt = istek.prompt.strip()[:2000]
            resim = None
            if istek.resim:
                try:
                    resim = base64.b64decode(istek.resim, validate=True)
                except binascii.Error:
                    raise HTTPException(status_code=400, detail="Fotoğraf okunamadı")
            if istek.video_model == "wan":
                genislik, yukseklik = WAN_BOYUTLARI.get(istek.boyut, WAN_BOYUTLARI["yatay"])
                if resim:
                    kimlik = baslat("wan_fotodan", prompt, genislik, yukseklik, istek.tohum, resim)
                else:
                    kimlik = baslat("wan", prompt, genislik, yukseklik, istek.tohum)
            else:
                genislik, yukseklik = VIDEO_BOYUTLARI.get(istek.boyut, VIDEO_BOYUTLARI["yatay"])
                kimlik = baslat("video", prompt, genislik, yukseklik, VIDEO_KARELERI[istek.sure], istek.tohum,
                                resim)
        else:
            prompt = son_prompt(istek.prompt, istek.mod)
            if sum(len(r) for r in istek.referanslar) > 40_000_000:
                raise HTTPException(status_code=413, detail="Referans görseller çok büyük")
            try:
                referanslar = [base64.b64decode(r, validate=True) for r in istek.referanslar]
            except binascii.Error:
                raise HTTPException(status_code=400, detail="Referans görsel okunamadı")
            if istek.kalite == "flux":
                genislik, yukseklik = FLUX_BOYUTLARI.get(istek.boyut, FLUX_BOYUTLARI["kare"])
                if referanslar:
                    genislik = yukseklik = None  # referanstan türetilir
                kimlik = baslat("flux", prompt, genislik, yukseklik, istek.adet, istek.tohum, referanslar)
            elif qwen_mu(istek.kalite, bool(referanslar)):
                kalite = istek.kalite if istek.kalite in QWEN_BOYUTLARI else "eniyi"
                genislik, yukseklik = boyut_hesapla(istek.boyut, kalite)
                if referanslar:
                    genislik = yukseklik = None  # referanstan türetilir
                kimlik = baslat("qwen", prompt, genislik, yukseklik, istek.adet, istek.tohum, referanslar,
                                QWEN_COZUNURLUK[kalite])
            else:
                genislik, yukseklik = boyut_hesapla(istek.boyut, istek.kalite)
                kimlik = baslat("foto", prompt, genislik, yukseklik, istek.adet, istek.tohum)
        return {"is": kimlik, "genislik": genislik, "yukseklik": yukseklik}

    @api.post("/api/is/{kimlik}/alindi", dependencies=[Depends(oturum_kontrol)])
    def is_alindi(kimlik: str):
        """Telefon sonucu aldı: sunucudaki geçici kopya hemen silinir."""
        if teslim_onayla:
            teslim_onayla(kimlik)
        return {"silindi": True}

    @api.get("/api/is/{kimlik}", dependencies=[Depends(oturum_kontrol)])
    def is_durumu(kimlik: str):
        try:
            sonuc = sonuc_al(kimlik)
        except TimeoutError:
            adimlar = ilerleme_al(kimlik) if ilerleme_al else None
            return {"bitti": False, "ilerleme": adimlar or []}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Üretim başarısız: {type(e).__name__}: {e}")
        if isinstance(sonuc, dict) and "tur" in sonuc:  # araştırma
            return {"bitti": True, **sonuc}
        if isinstance(sonuc, dict):  # video
            return {"bitti": True, "video": base64.b64encode(sonuc["mp4"]).decode(), "tohum": sonuc["tohum"],
                    "gelismis_prompt": sonuc.get("gelismis_prompt")}
        if not isinstance(sonuc, list):  # isit işi
            return {"bitti": True}
        return {
            "bitti": True,
            "gorseller": [
                {"veri": base64.b64encode(g["jpeg"]).decode(), "tohum": g["tohum"],
                 "genislik": g.get("genislik"), "yukseklik": g.get("yukseklik")} for g in sonuc
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
    """İşi başlatır. İş kimliği "<FunctionCall>.<teslim anahtarı>" biçimindedir (ısıtmada yalnızca ilki)."""
    sinif, metot = {
        "foto": (Uretici, "uret"),
        "isit_foto": (Uretici, "isit"),
        "qwen": (QwenUretici, "uret"),
        "isit_qwen": (QwenUretici, "isit"),
        "flux": (FluxUretici, "uret"),
        "isit_flux": (FluxUretici, "isit"),
        "video": (VideoUretici, "uret"),
        "isit_video": (VideoUretici, "isit"),
        "wan": (WanUretici, "uret"),
        "isit_wan": (WanUretici, "isit"),
        "wan_fotodan": (WanFotodanUretici, "uret"),
        "isit_wan_fotodan": (WanFotodanUretici, "isit"),
        "arastir": (None, None),
    }[is_turu]
    fn = arastir if is_turu == "arastir" else getattr(sinif(), metot)
    if not args:  # ısıtma: taşınacak veri yok
        return fn.spawn().object_id
    anahtar = uuid.uuid4().hex
    teslim[f"girdi:{anahtar}"] = args
    return f"{fn.spawn(anahtar).object_id}.{anahtar}"


def _modal_sonuc(kimlik: str):
    fc_id, _, anahtar = kimlik.partition(".")
    sonuc = modal.FunctionCall.from_id(fc_id).get(timeout=0)  # hazır değilse TimeoutError
    if not anahtar:
        return sonuc
    veri = teslim.get(f"sonuc:{anahtar}")
    if veri is None:
        raise RuntimeError("Sonuç bulunamadı; daha önce teslim alınmış olabilir.")
    return veri


def _modal_teslim_onayla(kimlik: str):
    _, _, anahtar = kimlik.partition(".")
    if anahtar:
        teslim.pop(f"sonuc:{anahtar}", None)


@app.function(image=web_image, secrets=[sifre_secret, kota_secret], timeout=900)
@modal.concurrent(max_inputs=20)
@modal.asgi_app(label="foto-studyo")
def web():
    return uygulama_olustur(
        sifre=os.environ["SIFRE"],
        baslat=_modal_baslat,
        sonuc_al=_modal_sonuc,
        statik_dir=STATIK_DIR,
        kota_al=kota_bilgisi,
        ilerleme_al=lambda kimlik: arastirma_durum.get(kimlik.partition(".")[0]),
        istek_sayaci=lambda: arastirma_durum.get(_sayac_anahtari(), 0),
        teslim_onayla=_modal_teslim_onayla,
    )
