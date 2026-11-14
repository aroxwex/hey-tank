# Hey Tank

Windows için portable, çevrimdışı, ücretsiz Türkçe sesli komut asistanı.
"Hey Tank" de, sonra komutunu söyle (örn. "youtube aç").

## Derleme
GitHub → **Actions → Build HeyTank → Run workflow**. Bitince **HeyTank-portable**
artifact'ını indirip klasöre çıkar, `HeyTank.exe`'yi çalıştır.

Her şey (ayarlar, komutlar, modeller, WebView önbelleği) exe'nin yanındaki klasörde kalır.
Tek istisna: "Windows ile başlat" açılırsa HKCU\...\Run altına bir kayıt yazılır (kapatınca silinir).

## Gereksinim
Microsoft Edge WebView2 Runtime (Windows 11'de hazır, güncel Windows 10'da genelde var).
