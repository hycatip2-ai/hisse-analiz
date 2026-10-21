"""Qwen-Image-2.1 denemesi: hız, kalite ve referans görselle kimlik koruma.

Çalıştırma (GPU kredisi harcar, ~0,3 $):  modal run qwen_tani.py
Çıktılar ./tani-cikti/ klasörüne yazılır. Referans olarak gerçek kişi değil, modelin kendi
ürettiği yapay portreler kullanılır.
"""

import io
import time
from pathlib import Path

import modal

MODEL_ID = "Qwen/Qwen-Image-2.1"
MODEL_DIR = "/modeller"
QWEN_YOL = f"{MODEL_DIR}/qwen-image-2.1"

app = modal.App("foto-qwen-tani")
modeller = modal.Volume.from_name("foto-modeller")
gpu_image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch", "torchvision", "diffusers==0.41.0", "transformers>=5.17", "accelerate", "safetensors", "pillow",
    "huggingface_hub",
)
indirme_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub")


@app.function(image=indirme_image, volumes={MODEL_DIR: modeller}, timeout=1800)
def indir():
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL_ID, local_dir=QWEN_YOL, ignore_patterns=["assets/*"])
    modeller.commit()


@app.cls(gpu="L40S", image=gpu_image.env({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}),
         volumes={MODEL_DIR: modeller}, timeout=1800)
class QwenTani:
    @modal.enter()
    def yukle(self):
        import traceback

        self.hata = None
        try:
            import torch
            from diffusers import QwenImage21Pipeline

            bas = time.time()
            self.pipe = QwenImage21Pipeline.from_pretrained(QWEN_YOL, torch_dtype=torch.bfloat16).to("cuda")
            if hasattr(self.pipe.vae, "enable_tiling"):
                self.pipe.vae.enable_tiling()
            self.yukleme = time.time() - bas
        except Exception:
            self.hata = traceback.format_exc()
            print(self.hata)

    def _uret(self, prompt, tohum, **k):
        import torch

        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        bas = time.time()
        img = self.pipe(prompt=prompt, generator=torch.Generator("cuda").manual_seed(tohum), **k).images[0]
        torch.cuda.synchronize()
        return img, round(time.time() - bas, 1)

    @modal.method()
    def calistir(self):
        import torch

        if self.hata:
            raise RuntimeError("Model yüklenemedi:\n" + self.hata)
        sonuc, sureler = {}, {"yukleme": round(self.yukleme, 1)}

        def kaydet(ad, img):
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=92)
            sonuc[ad] = buf.getvalue()

        def dene(ad, prompt, tohum, **k):
            try:
                img, sureler[ad] = self._uret(prompt, tohum, **k)
                kaydet(ad, img)
                return img
            except Exception as e:  # bellek yetmezse vb.
                sureler[ad] = f"HATA {type(e).__name__}: {str(e)[:150]}"
                return None

        # 1) Yazıdan portre, 1 MP (varsayılan) ve ~4 MP
        portre = ("A candid close-up portrait of a woman in her thirties with short curly auburn hair, freckles and "
                  "green eyes, wearing a mustard knit sweater, sitting in a cozy cafe by the window, natural soft "
                  "daylight, realistic skin texture, shot on 50mm")
        referans1 = dene("1_portre_1mp", portre, 7)
        dene("2_portre_2mp", portre, 7, width=1216, height=1664)
        sureler["bellek_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 1)
        if referans1 is None:
            return {"gorseller": sonuc, "sureler": sureler}

        # 2) Tek referans: aynı kişi başka sahnede
        dene("3_referans_sahil",
             "The same woman from the reference image, now standing on a sunny beach wearing a navy business suit, "
             "full body shot, waves behind her. Keep her face, hair and freckles identical.", 11, image=[referans1])

        # 3) İki referans: iki yapay kişi tek sahnede
        adam = dene("4_ikinci_kisi", "A portrait of a bald man in his fifties with a grey beard and round glasses, "
                    "wearing a denim shirt, plain studio background, realistic", 21)
        if adam is not None:
            dene("5_iki_referans",
                 "The woman from the first image and the man from the second image are sitting together at an "
                 "outdoor cafe table, laughing and drinking coffee, natural afternoon light. Keep both of their faces "
                 "identical to the references.", 31, image=[referans1, adam])

        # 4) Düzenleme: yalnızca arka planı değiştir
        dene("6_duzenleme", "Change only the background to a snowy mountain village at dusk. "
             "Keep the woman exactly the same.", 41, image=[referans1])
        sureler["bellek_gb_son"] = round(torch.cuda.max_memory_allocated() / 1e9, 1)
        return {"gorseller": sonuc, "sureler": sureler}


@app.local_entrypoint()
def main():
    cikti = Path("tani-cikti")
    cikti.mkdir(exist_ok=True)
    bas = time.time()
    indir.remote()
    print(f"İndirme: {time.time() - bas:.0f} sn")
    bas = time.time()
    s = QwenTani().calistir.remote()
    print(f"GPU toplam: {time.time() - bas:.0f} sn")
    print("Süreler (sn) ve bellek:", s["sureler"])
    for ad, veri in s["gorseller"].items():
        (cikti / f"{ad}.jpg").write_bytes(veri)
