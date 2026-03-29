## Film İzleme & Kişiselleştirilmiş Film Öneri Sistemi (Web Uygulaması)

**Hazırlayan:** Hakan Polat
**Proje Adı:** BitirmeProjesi
**Teknoloji:** Flask + PostgreSQL + TMDB API + (Embedding tabanlı öneri)
**Çalıştırma:** Docker Compose (web + db)

---

## İçindekiler

1. Proje Özeti
2. Problem Tanımı ve Motivasyon
3. Hedefler ve Başarı Kriterleri
4. Kapsam ve Varsayımlar
5. Kullanılan Teknolojiler
6. Genel Mimari
7. Modüller ve Katmanlar
8. Veritabanı Tasarımı
9. API (REST) Tasarımı ve Kullanımı
10. Öneri Sistemi Nasıl Çalışır?
11. Cache ve Performans Yaklaşımı
12. Loglama ve İzlenebilirlik
13. Güvenlik ve Kimlik Doğrulama
14. Test Süreci (Selenium + Pytest)
15. Kurulum ve Çalıştırma
16. Sonuç

---

# 1) Proje Özeti

Bu proje, kullanıcıların film keşfetmesini kolaylaştıran ve kullanıcının etkileşimlerine göre **kişiselleştirilmiş film önerileri** sunabilen bir web uygulamasıdır. Uygulama; TMDB (The Movie Database) üzerinden film verilerini çeker, kullanıcı kayıt/giriş işlemlerini yönetir, favoriler/puanlama/fragman izleme etkileşimlerini toplar ve bu sinyalleri kullanarak embedding tabanlı öneriler üretir.

Projenin temel çıktıları:

* Film arama & listeleme
* Film detay sayfası
* Favorilere ekleme
* Puanlama (beğen / beğenme vb.)
* Fragman izleme olayı kaydı
* **Kişiselleştirilmiş öneri** (benzerlik/embedding yaklaşımı)
* Docker ile kolay kurulum
* Loglama + UI test altyapısı

---

# 2) Problem Tanımı ve Motivasyon

Günümüzde içerik platformlarında film seçmek zorlaşmıştır. Kullanıcılar çok fazla seçenek arasında kaybolur. Bu proje, şu problemleri hedefler:

* **Keşif zorluğu:** Kullanıcının ilgisine göre film bulmanın zor olması
* **Kişiselleştirme eksikliği:** Her kullanıcıya aynı listelerin sunulması
* **Kullanıcı sinyallerini değerlendirememe:** Favori/izleme/puan gibi veriler varken öneriye dönüşmemesi

Motivasyon: Kullanıcının küçük etkileşimleri bile (favori, puan, fragman) öneri kalitesini artırmak için değerlendirilebilir.

---

# 3) Hedefler ve Başarı Kriterleri

## Hedefler

* Kullanıcıların film arayabilmesi ve detay görebilmesi
* Kullanıcıların etkileşimlerini (favori, puan, fragman) kaydedebilmesi
* Bu sinyallerden kullanıcı profili çıkarıp öneri üretebilmek
* Uygulamayı Docker ile tek komutla ayağa kaldırabilmek
* Loglama ile davranışların izlenebilmesi
* UI testleri ile doğrulama yapılabilmesi

## Başarı Kriterleri

* Sistem çalışır durumda: `/` ana sayfa, `/search`, `/detail/<id>` düzgün açılmalı
* Kayıt/giriş akışı çalışmalı
* `/api/personalized` endpoint’i kullanıcı sinyali varsa öneri döndürmeli
* Loglar istekleri ve kritik olayları kaydetmeli
* UI testleri “passed” olmalı

---

# 4) Kapsam ve Varsayımlar

## Kapsam İçinde

* Web tabanlı arayüz (Flask template + JS)
* PostgreSQL veritabanı
* TMDB API üzerinden film verisi
* Embedding tabanlı öneri ve aday havuzu
* Cache (in-memory + tasarımsal Redis)
* Loglama (request ve auth/api olayları)
* Selenium ile temel UI testleri

---

# 5) Kullanılan Teknolojiler

## Backend

* **Python 3.11+**
* **Flask** (web framework)
* **Gunicorn** (prod-ready WSGI server)
* **psycopg / PostgreSQL** (veritabanı)
* **requests** (TMDB API çağrıları)

## Öneri Sistemi

* **sentence-transformers / transformers** (embedding üretimi için)
* **NumPy** (vektör benzerliği ve matris işlemleri)

## Frontend

* **Jinja2 template**
* **Vanilla JavaScript**
* **TailwindCSS (CDN)**

## DevOps

* **Docker / Docker Compose**
* (Opsiyonel) **Redis** servisi (cache için tasarımsal altyapı)

## Test

* **pytest**
* **selenium**
* **webdriver-manager** (ChromeDriver yönetimi)

---

# 6) Genel Mimari

Proje, modüler bir monolith olarak tasarlanmıştır:

* `app/__init__.py` → Flask uygulaması oluşturma, blueprint kayıtları, loglama kurulumu
* `app/blueprints/` → Sayfalar, auth ve API endpoint’leri
* `app/services/` → TMDB erişimi, öneri sistemi, embedding işlemleri, yardımcı fonksiyonlar
* `app/db.py` → DB bağlantı ve init işlemleri
* `docker-compose.yml` → web + db (+ opsiyonel redis)
* `wsgi.py` → gunicorn giriş noktası

Mimari akış (yüksek seviye):

1. Kullanıcı arayüzden istek yapar
2. Flask blueprint route karşılar
3. Servis katmanı (services) TMDB/DB/embedding işlemlerini yapar
4. Sonuç template veya JSON (API) olarak döndürülür
5. Loglama katmanı request ve olayları kaydeder

---

# 7) Modüller ve Katmanlar

## 7.1 Blueprints

* **pages blueprint:** Ana sayfa, arama sayfası, detay sayfası, favoriler, listeleme gibi UI sayfaları
* **auth blueprint:** Register / Login / Logout
* **api blueprint:** `/api/*` uçları (featured, discover, personalized vb.)

## 7.2 Services

* `tmdb.py`: TMDB API çağrıları, tür listeleri, caching
* `recommender.py`: aday havuzu, kullanıcı profili, skor hesaplama, öneri üretimi
* `embeddings.py`: film embedding’lerini üretme/ensure etme
* `events.py`: olay loglama (watch_trailer, login_success vb.)
* `auth.py`: login_required ve kullanıcı yardımcıları
* `utils.py`: zaman, hash, yardımcı fonksiyonlar

---

# 8) Veritabanı Tasarımı (Özet)

Proje, kullanıcı etkileşimlerini saklayarak öneri üretir. Öne çıkan tablolar:

* `users`: kullanıcı bilgileri
* `favorites`: kullanıcı favorileri
* `ratings`: kullanıcı puan/geri bildirimleri
* `trailer_events`: fragman izleme olayları
* `candidate_movies`: öneri için aday havuzu (film meta)
* `user_profiles`: kullanıcı embedding profili
* `user_recommendations`: üretilen önerilerin cache’lenmiş hali

Bu tasarım, öneri sistemi için gerekli sinyalleri kalıcı hale getirir ve tekrar hesaplama maliyetini düşürür.

---

# 9) REST API Tasarımı ve Nerede Kullanıldı?

projede **REST API** kullanıldı. “Frontend sayfalar” dışında, arayüzün dinamik kısımlarında JSON dönen API endpoint’leri kullanılır.

Örnek endpointler:

* `GET /api/featured`
  Ana sayfadaki “öne çıkanlar / popülerler” gibi film listelerini JSON döndürür.

* `GET /api/discover`
  Filtreli keşif (genre/year/sort) için JSON döndürür.

* `GET /api/search_suggest`
  Canlı arama önerileri (autocomplete) için JSON döndürür.

* `POST /api/trailer_event`
  Kullanıcı fragman izleyince olayı DB’ye kaydeder.

* `GET /api/personalized`
  Kullanıcı profili varsa kişiselleştirilmiş öneri listesini döndürür.

Bu endpointler arayüz tarafında JS ile çağrılarak sayfa yenilemeden içerik günceller.

---

# 10) Öneri Sistemi Nasıl Çalışır?

Öneri sistemi “sinyal toplama → kullanıcı profili → aday film havuzu → benzerlik skoru” adımlarını izler.

## 10.1 Kullanıcı Sinyalleri

Sistem aşağıdaki etkileşimleri “kullanıcı tercihi” olarak yorumlar:

* Favoriye ekleme (pozitif sinyal, yüksek ağırlık)
* Puanlama (beğeni/eleştiri gibi pozitif/negatif sinyal)
* Fragman izleme (ilgi göstergesi, orta ağırlık)

## 10.2 Kullanıcı Profili (Embedding)

* Kullanıcının etkileşimde bulunduğu filmlerin embedding’leri alınır.
* Bu embedding’ler ağırlıklı ortalama ile birleştirilir.
* Sonuç normalize edilerek **kullanıcı vektörü** oluşturulur.
* Bu vektör `user_profiles` tablosunda saklanır.

## 10.3 Aday Havuzu (Candidate Pool)

* TMDB üzerinden popüler/top-rated/trending/now-playing listelerinden aday filmler çekilir.
* Aday film listesi DB’de `candidate_movies` tablosuna yazılır.
* Bu adayların embedding matrisi hazırlanır.

## 10.4 Skorlama

* Her aday film için skor: **cosine benzerlik mantığında dot product**
  `score = candidate_matrix @ user_vector`
* Kullanıcının zaten gördüğü/etkileşim yaptığı filmler filtrelenir.
* En yüksek skorlu N film öneri olarak döndürülür.
* Sonuçlar `user_recommendations` tablosuna kaydedilerek tekrar hesaplama azaltılır.

---

# 11) Cache ve Performans Yaklaşımı

Projede hem **in-memory cache** hem de (tasarımsal olarak) **Redis cache katmanı** düşünülmüştür:

**@lru_cache:**
Tür listeleri gibi küçük ve sık kullanılan statik veriler için.

**Bellek içi aday havuzu (_mem_cand):**
`get_candidate_cache()` ile `candidate_movies` tablosundan çekilen embedding matrisi RAM’de tutulur.
Böylece her istekte veritabanından komple set çekilmez.

**Redis (tasarım):**
TMDB sonuçlarının ve embeddinglerin kısa süreli cache’i için kullanılmak üzere `docker-compose.yml` içinde `redis` servisi tanımlanmıştır.
İleride yüksek trafik altında network tabanlı merkezi cache olarak kullanılabilir.


---

# 12) Loglama ve İzlenebilirlik (Projeye Eklenen Kısım)

Bu projede uygulama davranışını izlemek için **merkezi loglama** eklenmiştir. Loglar, hem hata ayıklamayı kolaylaştırır hem de kullanıcı davranış analizi sağlar.

## 12.1 Loglama Mimarisinin Amacı

* API çağrıları başarılı mı? Kaç sonuç dönüyor?
* Login/Register gibi kritik adımlar ne sıklıkla başarısız oluyor?
* Öneri endpoint’i cache’ten mi geliyor yoksa yeni mi üretiliyor?
* Sistem canlıda sorun çıkarırsa “nerede koptuğu” hızlı bulunur.

## 12.2 Loglanan Örnek Olaylar

* `/api/featured called` ve `success returned=20`
* `Login FAILED` / `Login SUCCESS`
* `Register FAILED (duplicate)`
* `/api/personalized` için `from_cache` veya `fresh` bilgisi

Bu loglar `docker compose logs -f web` komutuyla canlı izlenebilir.

## 12.3 Logların Sunum Değeri

Rapor/sunumda log çıktısı göstermek:

* Projenin “gerçek dünyaya daha yakın” olduğunu,
* İzleme/analiz altyapısı bulunduğunu,
* Debug kolaylığını
  kanıtlar.

---

# 13) Güvenlik ve Kimlik Doğrulama

* Kullanıcı şifreleri **hash**’lenerek saklanır.
* Oturum yönetimi `session` üzerinden yapılır.
* `login_required` ile korunan endpoint’lere girişsiz erişim engellenir.
* Hassas loglarda e-posta gibi bilgiler direkt değil, gerekirse **hash** ile kaydedilebilir.

---

# 14) Test Süreci (Selenium + Pytest)

Projede UI doğrulaması için **Selenium tabanlı uçtan uca testler** eklenmiştir.

Testlerin amacı:

* Ana sayfa açılıyor mu?
* Arama formu yönlendirme yapıyor mu?

## Test Çalıştırma Mantığı

* Uygulama Docker’da çalışır: `http://localhost:5002`
* Testler local `.venv` ortamında çalıştırılır.
* `APP_BASE_URL` ile testlerin hangi adrese gideceği ayarlanır.

Örnek:

```bash
export APP_BASE_URL=http://localhost:5002
pytest -q
```

Başarılı koşum çıktısı:

* `2 passed`

---

# 15) Kurulum ve Çalıştırma

## 15.1 Docker ile Çalıştırma

```bash
docker compose up --build
```

Portlar (örnek):

* Web: `http://localhost:5002`
* DB: `localhost:5433`

## 15.2 Logları İzleme

```bash
docker compose logs -f web
```

## 15.3 Testleri Çalıştırma (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install selenium webdriver-manager pytest

export APP_BASE_URL=http://localhost:5002
pytest -q
```

---

# 16) Sonuç

Bu projede, film keşfi ve kişiselleştirme problemine yönelik çalışan bir web uygulaması geliştirilmiştir. Uygulama, kullanıcı davranışlarını kaydedip embedding tabanlı öneri üretebilen bir altyapıya sahiptir. Docker ile kolay çalıştırılabilmesi, loglama ve test eklenmesi projenin “bitirme projesi” seviyesinin üzerine çıkmasını sağlamıştır.

---



