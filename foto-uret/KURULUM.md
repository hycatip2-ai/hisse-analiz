# Foto Stüdyo: Kurulum

Telefona kurulabilen yapay zeka görsel üretme uygulaması (PWA). Modal (bulut GPU) üzerinde
**Z-Image-Turbo** modeli çalışır; model ücretsizdir (Apache 2.0), GPU süresi
Modal'ın aylık ücretsiz kredisinden düşer.

## 0. Bilgisayarsız kurulum (sadece telefon)

Deploy işini GitHub Actions yapar (`.github/workflows/foto-deploy.yml`).

1. https://modal.com adresinde hesap aç.
2. Modal'da **Settings → API Tokens → New Token**. Çıkan `Token ID` (ak-...) ve
   `Token Secret` (as-...) değerlerini kopyala.
3. GitHub'da bu repoda **Settings → Secrets and variables → Actions → New repository secret**
   ile üç secret ekle:
   - `MODAL_TOKEN_ID`: Token ID
   - `MODAL_TOKEN_SECRET`: Token Secret
   - `FOTO_SIFRE`: fotoğraf sayfası için kendi belirleyeceğin şifre
4. **Actions** sekmesinde "Foto Üret - Modal'a yükle" işini çalıştır (veya son çalışmayı
   "Re-run" et). Bitince "Deploy" adımının çıktısındaki `https://...modal.run` adresi senin adresin.
5. Modal panelinden harcama limiti koy (aşağıdaki 1.5 maddesi).

Kodda değişiklik yapılıp `foto-uret/` klasörüne push edildiğinde deploy kendiliğinden tekrarlanır.

## 1. Bir kerelik kurulum (bilgisayardan, ~10 dakika)

1. https://modal.com adresinden hesap aç (GitHub ile giriş yapabilirsin).
2. Bilgisayarında Python 3.10+ olsun. Terminal / Komut İstemi aç:
   ```
   pip install modal
   modal setup
   ```
   Tarayıcı açılır, onay ver.
3. Kendine bir şifre belirle ve Modal'a kaydet (`BENIM-SIFREM` yerine kendi şifreni yaz):
   ```
   modal secret create foto-sifre SIFRE=BENIM-SIFREM
   ```
4. Bu klasöre gel ve yayına al:
   ```
   cd foto-uret
   modal deploy foto.py
   ```
   Çıktıda `https://...--foto-studyo....modal.run` gibi bir adres çıkar. Bu senin adresin.

5. Modal panelinde (Settings → Usage / Billing) **harcama limiti** koy, böylece
   ücretsiz kredi bitince sistem durur ve sürpriz fatura gelmez.

## 2. Telefondan kullanım

1. Deploy çıktısındaki `https://...foto-studyo....modal.run` adresini Chrome'da aç.
2. Sağ üstteki indirme simgesine veya Chrome menüsündeki **Uygulamayı yükle**'ye bas.
   Ana ekrana "Foto Stüdyo" ikonu gelir; tam ekran uygulama olarak açılır.
3. Şifrenle giriş yap (90 gün hatırlanır).
4. Açıklamayı yaz, mod / boyut / adet / kalite seç, **Üret**'e bas.
5. Görseller **Galeri** sekmesinde telefonda saklanır; oradan kaydet, paylaş,
   sil veya "Tekrar kullan" ile ayarlarını forma geri yükle.

- Sağ üstteki nokta GPU durumunu gösterir: gri = uykuda, sarı = hazırlanıyor, yeşil = hazır.
  Açıklama kutusuna dokununca GPU arka planda uyanmaya başlar.
- Galeri sadece o telefonda durur; uygulama verisini silersen galeri de silinir.
- İlk üretim veya bir süre kullanmadıktan sonraki ilk üretim **1-3 dakika** sürer
  (GPU açılıp model yükleniyor; en ilk seferde model ~20 GB indirilir).
  Sonrakiler birkaç saniye sürer.
- İngilizce açıklamalar en iyi sonucu verir.
- Aynı "tohum" sayısı ile aynı açıklama aynı resmi verir; beğendiğin bir sonucu
  küçük değişikliklerle tekrar üretmek için işe yarar.

## Maliyet

- 2 dakika kullanılmazsa GPU kendiliğinden kapanır; kapalıyken ücret yok.
- Aynı anda en fazla 1 GPU açılır (`max_containers=1`).
- Kabaca resim başına birkaç kuruş; aylık ücretsiz kredi kişisel kullanım için
  genelde yeterli. Güncel fiyatlar: https://modal.com/pricing

## Değiştirmek istersen

- Kodu değiştirdikten sonra tekrar `modal deploy foto.py` çalıştır.
- Şifreyi değiştirmek: `modal secret create foto-sifre SIFRE=YENI --force`, ardından tekrar deploy.
- Kayıtlar / hatalar: https://modal.com/apps adresinde `foto-uret` uygulaması.
