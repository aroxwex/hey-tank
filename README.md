# Hey Tank 2.0

Windows için portable, ücretsiz sesli asistan. **Google Gemini Live** ile çalışır: doğal Türkçe konuşursun,
Tank bilgisayarında işi yapar (site/uygulama açma, müzik, ses, kısayollar...) ve sesli cevap verir.

## Kurulum
1. GitHub → **Actions → Build HeyTank → Run workflow**. Çıkan **HeyTank-portable** dosyasını indirip klasöre çıkar.
2. `HeyTank.exe`'yi çalıştır → **Ayarlar > Gemini**: ücretsiz anahtarını (aistudio.google.com/apikey) yapıştır, "Kaydet ve dene".
3. "Hey Tank" de, daireye dokun veya **Ctrl+Alt+T** bas.

## Notlar
- Her şey exe'nin klasöründe kalır (portable). Tek istisna: "Windows ile başlat" açılırsa HKCU\...\Run'a bir kayıt yazılır.
- Her kullanıcı **kendi** Gemini anahtarını girer. Klasörü paylaşırken `config.json`'u verme.
- Ses, konuşma sırasında Google'a gönderilir (yalnızca "Hey Tank" veya daire/kısayol ile başlattığında).
- Gereksinim: Microsoft Edge WebView2 Runtime (Windows 11'de hazır; sorun olursa uygulama Edge penceresine geçer).
