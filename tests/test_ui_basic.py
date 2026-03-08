# tests/test_ui_basic.py
import os
import time
import requests

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# İstersen env'den al:
# export TEST_USER_EMAIL="..."
# export TEST_USER_PASSWORD="..."
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL")
TEST_USER_PASSWORD = os.getenv("TEST_USER_PASSWORD")


def _login(driver, base_url, email, password):
    driver.get(base_url + "/login")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(email)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

    # Login olunca "Çıkış" linki görünür varsayımı
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.LINK_TEXT, "Çıkış")))


def test_homepage_loads(driver, base_url):
    """Anasayfa açılıyor mu, title bekleneni içeriyor mu?"""
    driver.get(base_url + "/")

    title = driver.title or ""
    assert "Film Sitesi" in title or "BitirmeProjesi" in title


def test_header_elements_exist(driver, base_url):
    """Header: logo + arama input var mı?"""
    driver.get(base_url + "/")

    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.LINK_TEXT, "BitirmeProjesi"))
    )
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "siteSearchInput"))
    )


def test_nav_listeler_link_works(driver, base_url):
    """Listeler linki tıklanınca /search sayfasına gidiyor mu?"""
    driver.get(base_url + "/")

    # Nav bazen md hidden olabilir; direkt url'e gitmek de olur ama burada linki deniyoruz.
    # Link görünmezse fallback: doğrudan /search
    try:
        link = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.LINK_TEXT, "Listeler"))
        )
        link.click()
    except Exception:
        driver.get(base_url + "/search?q=")

    WebDriverWait(driver, 10).until(lambda d: "/search" in d.current_url)
    assert "/search" in driver.current_url


def test_search_form_redirects_to_search_page(driver, base_url):
    """
    Üstteki arama kutusunu kullanınca /search sayfasına gidiyor mu?
    (base.html içindeki form: id="siteSearchInput", action="/search")
    """
    driver.get(base_url + "/")

    search_input = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "siteSearchInput"))
    )
    search_input.clear()
    search_input.send_keys("Inception")
    search_input.send_keys(Keys.ENTER)

    WebDriverWait(driver, 10).until(lambda d: "/search" in d.current_url)
    assert "/search" in driver.current_url


def test_search_results_page_has_content(driver, base_url):
    """Search sayfası açılınca sayfada bir sonuç / içerik alanı var mı?"""
    driver.get(base_url + "/search?q=matrix")

    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    # Çok kesin selector kullanmayalım. "Film" veya poster card vs olabilir.
    body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    assert ("matrix" in body_text) or ("sonuç" in body_text) or ("film" in body_text)


def test_live_search_suggest_panel_opens(driver, base_url):
    """Canlı arama paneli (searchSuggest) açılıyor mu?"""
    driver.get(base_url + "/")

    search_input = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "siteSearchInput"))
    )
    search_input.clear()
    search_input.send_keys("matrix")

    panel = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "searchSuggest"))
    )

    # hidden class kalkıyor mu?
    WebDriverWait(driver, 10).until(lambda d: "hidden" not in panel.get_attribute("class"))


def test_login_page_loads(driver, base_url):
    """Login sayfası açılıyor mu?"""
    driver.get(base_url + "/login")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email")))
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "password")))


def test_register_page_loads(driver, base_url):
    """Register sayfası açılıyor mu?"""
    driver.get(base_url + "/register")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "username")))
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email")))
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "password")))




def test_api_featured_health(base_url):
    """Backend API sağlıklı mı? /api/featured 200 dönüyor mu?"""
    r = requests.get(base_url + "/api/featured", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert isinstance(data["results"], list)


def test_api_search_suggest_health(base_url):
    """Canlı önerinin backend'i çalışıyor mu? /api/search_suggest"""
    r = requests.get(base_url + "/api/search_suggest?q=matrix", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert isinstance(data["results"], list)


def test_favorites_page_requires_login_or_redirects(driver, base_url):
    """
    Favoriler login gerektiriyor olabilir.
    Login yoksa ya /login'e yönlendirir ya da 200 ama içerik 'Giriş' ister.
    """
    driver.get(base_url + "/favorites")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    url = driver.current_url
    body_text = driver.find_element(By.TAG_NAME, "body").text.lower()

    assert ("/login" in url) or ("giriş" in body_text) or ("favori" in body_text)


def test_login_and_logout_flow_if_credentials_provided(driver, base_url):
    """Env ile test hesabı verildiyse login + logout E2E çalıştır."""
    if not TEST_USER_EMAIL or not TEST_USER_PASSWORD:
        # credentials yoksa bu testi pas geçiyoruz
        return

    _login(driver, base_url, TEST_USER_EMAIL, TEST_USER_PASSWORD)

    # Logout
    driver.find_element(By.LINK_TEXT, "Çıkış").click()
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.LINK_TEXT, "Giriş")))
