<p align="center"><img src="assets/icon.png" width="96" alt="Hey Tank"></p>

# Hey Tank

Sesinle Windows bilgisayarını yöneten Türkçe sesli asistan.

"Hey Tank" de, Tank seni dinlesin. Uygulama ve oyun açsın, müziği ve sesi ayarlasın, dosya bulsun, sorularını yanıtlasın, seninle sohbet etsin.

Ücretsizdir. Kurulum gerektirmez (taşınabilir). Türkler için yapıldı.

## Neler yapabilir?

| Örnek cümle | Ne olur? |
|---|---|
| "Hey Tank, nasılsın?" | Seninle sohbet eder |
| "YouTube'da sakin bir müzik aç" | İlk sonucu açıp oynatır |
| "Spotify'ı aç" / "Discord'u aç" | Uygulamayı açar |
| "Counter-Strike 2'yi başlat" | Steam ve Epic oyunlarını bulup başlatır |
| "Sesi yüzde 30 yap" | Bilgisayar sesini ayarlar |
| "Spotify'ın sesini kıs" | Sadece o uygulamanın sesini ayarlar |
| "Masaüstünde rapor adlı dosyayı bul" | Dosya ve klasör arar, açar |
| "Ekranda ne yazıyor?" | Ekran görüntüsüne bakıp anlatır |
| "Hava nasıl?" | Hava durumunu söyler |
| "RAM ne kadar dolu?" | Sistem bilgisini söyler |
| "20 dakika sonra bana su içmeyi hatırlat" | Hatırlatıcı kurar |
| "Benim adım Ali, bunu hatırla" | Hafızaya kaydeder |

Bunların dışında Kısayollar sayfasından kendi işlerini tanımlayabilirsin: bir oyun, bir program, rastgele açılan siteler ya da bir uygulamanın tuş kısayolu.

## Kurulum

1. **Sürümler** sayfasından `HeyTank-portable.zip` dosyasını indir.
2. Zip'i bir klasöre çıkar (örneğin Masaüstü). Bir klasör içinde çalışır, kurulum yoktur.
3. `HeyTank.exe` dosyasını çalıştır.
4. Windows uyarı gösterirse **Daha fazla bilgi**, sonra **Yine de çalıştır** düğmesine bas.
5. Açılan kurulum ekranı ücretsiz anahtarı almana yardım eder.

Gereksinimler: Windows 10 veya 11, mikrofon, internet. Pencere için Microsoft Edge WebView2 gerekir (Windows 11'de hazır gelir, yoksa uygulama Edge penceresinde açılır).

## Ücretsiz Gemini API anahtarı

Tank, Google'ın Gemini API'sini kullanır. Bunun için kendi anahtarına ihtiyacın var:

1. Kurulum ekranındaki **Anahtar sayfasını aç** düğmesine bas (ya da `aistudio.google.com/api-keys` adresine git).
2. Google hesabınla giriş yap.
3. **API anahtarı oluştur** düğmesine bas ve anahtarı kopyala.
4. Anahtarı kurulum ekranına yapıştır, **Kaydet ve bağlantıyı dene** düğmesine bas.

Ücretsiz kullanımın sınırlarını Google belirler ve zamanla değişebilir.

## Kullanım

Üç dinleme modu vardır, ana sayfadan değiştirebilirsin:

- **Hey Tank:** "Hey Tank" dediğinde dinlemeye başlar.
- **Sürekli:** Tank hep açıktır, adını duyunca cevap verir.
- **Dokun:** Daireye dokun ya da `Ctrl+Alt+T` tuşlarına bas.

Pencereyi kapatınca Tank sistem tepsisinde çalışmaya devam eder. Tamamen kapatmak için sol menüdeki **Çıkış** düğmesini ya da tepsi simgesini kullan.

Ayarlar **Basit** ve **Gelişmiş** olarak ikiye ayrılır. Basit ayarlar yeterlidir.

Bir şey çalışmıyorsa sol menüdeki **Sistem testi** sayfasını çalıştır. Mikrofon, hoparlör, internet ve model bağlantısını kontrol eder. Sonucu kopyalayıp bir sorun bildirirken ekleyebilirsin.

## Sık sorunlar

**Hey Tank yanlışlıkla uyanıyor:** Ayarlar, Gelişmiş, Dinleme bölümünden hassasiyeti "Sıkı" yap ya da "Dokun" modunu kullan.

**Bağlanamıyor:** Sistem testini çalıştır. Anahtarını ve internetini kontrol et. Ayarlar, Gelişmiş, Gemini bölümündeki **Modelleri test et** düğmesi hangi modellerin çalıştığını gösterir.

**Kota hatası alıyorum:** Google bu anahtar için kota yok diyor demektir. Ücretsiz kotanın sınırları Google'a bağlıdır ve hesaba ya da bölgeye göre değişebilir. AI Studio'da canlı konuşmayı denemek faydalı olur, orada da çalışmıyorsa sorun Google tarafındadır.

**Arayüz açılmıyor:** İndirdiğin zip'e sağ tıkla, Özellikler'de **Engellemeyi kaldır** kutusunu işaretle ve zip'i yeniden çıkar.

**Discord'da mikrofon kapanmıyor:** Discord mikrofonu sadece tuş atamasıyla yönetilebilir. Discord'da Tuş Atamaları'ndan mikrofon eylemine `Ctrl+Shift+M` ata, sonra Hey Tank'te Kısayollar sayfasındaki "Discord mikrofon" kısayolunu aç.

**Spotify'da şarkı kendiliğinden çalmıyor:** Tank Spotify'da aramayı açar ama şarkıyı başlatamaz. YouTube'da ise ilk sonucu doğrudan oynatır.

## Gizlilik

- "Hey Tank" dinleyicisi bilgisayarında çevrimdışı çalışır.
- Konuşma, oturum açıkken Gemini API'ye gönderilir. Kullanım koşulları için Google'ın belgelerine bak: `ai.google.dev/gemini-api/terms`
- Ekran görüntüsü yalnızca ekranı sorduğunda gönderilir.
- API anahtarı bu bilgisayarda şifrelenerek saklanır.
- Ayarlar, kısayollar, hafıza ve hatırlatıcılar `data` klasöründedir. Klasörü paylaşırken `data` klasörünü verme.

## Güncelleme

Yeni sürümü indir, eski HeyTank klasörünün yerine çıkar ama `data` klasörünü yeni klasöre taşı. Ayarların ve hafızan korunur.

## Geliştiriciler için

Derleme GitHub Actions ile otomatik yapılır: **Actions** sekmesinden **Derleme** iş akışını çalıştır ya da `v*` biçiminde bir etiket gönder:

```
git tag v2.2.0
git push --tags
```

Yerelde çalıştırmak için:

```
pip install -r requirements.txt
python tools/fetch_models.py models
python main.py
```

Dosyalar:

```
main.py              uygulamanın tamamı: motor, araçlar, arayüz köprüsü
ui/index.html        arayüz
icon_draw.py         logo çizimi
tools/               model indirme ve simge üretme
assets/              logo dosyaları
.github/workflows/   otomatik derleme
```

## Lisans

MIT. Ayrıntılar `LICENSE` dosyasında. Uyandırma için Vosk modelleri, arayüz için pywebview ve diğer açık kaynak kütüphaneler kullanılır; her birinin kendi lisansı geçerlidir.
