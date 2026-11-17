# Hey Tank 2.2

Windows için portable, ücretsiz sesli asistan. **Google Gemini Live** ile çalışır: doğal Türkçe konuşursun,
Tank bilgisayarında işi yapar (site/uygulama/oyun açma, müzik, ses, dosya bulma, hava, sistem bilgisi, ekran analizi,
hafıza, hatırlatıcı...) ve sesli cevap verir.

## Kurulum
1. GitHub → **Actions → Build HeyTank → Run workflow**. Çıkan **HeyTank-portable** dosyasını indirip klasöre çıkar.
2. `HeyTank.exe`'yi çalıştır → kurulum sihirbazı ücretsiz Gemini anahtarını almana ve girmene yardım eder.
3. Dinleme modu: **"Hey Tank"**, **Sürekli** (Jarvis gibi) veya **Dokun** (daire / Ctrl+Alt+T).

## Güncelleme
Yeni sürümü indirince HeyTank klasörünü değiştir ama **data** klasörünü koru. Ayarlar, kısayollar, hafıza ve
hatırlatıcılar `data/` içinde durur. Eski sürümlerin `config.json` / `shortcuts.json` dosyaları ilk açılışta otomatik taşınır.

## Notlar
- Her kullanıcı **kendi** Gemini anahtarını girer. Klasörü paylaşırken `data/` klasörünü verme.
- Ses, oturum açıkken Google'a gönderilir (ücretsiz kullanımda Google ürünlerini geliştirmek için kullanabilir).
- Gereksinim: Microsoft Edge WebView2 Runtime (Windows 11'de hazır; sorun olursa uygulama Edge penceresine geçer).
