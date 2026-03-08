# tests/conftest.py
import os
import time
import pytest
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# --- Ayarlar ---
DEFAULT_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5002").rstrip("/")
HEADLESS = os.getenv("HEADLESS", "1") == "1"        # default headless
WAIT_SEC = int(os.getenv("SELENIUM_WAIT", "15"))    # default 15s
SCREENSHOT_DIR = Path(os.getenv("SCREENSHOT_DIR", "test_artifacts/screenshots"))


@pytest.fixture(scope="session")
def base_url():
    """
    Docker'da çalıştırıyorsan: http://localhost:5002
    Lokal çalıştırıyorsan:    http://localhost:5000
    """
    return DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def wait_seconds():
    return WAIT_SEC


@pytest.fixture(scope="session")
def driver():
    """
    Stabil Chrome driver:
    - headless varsayılan açık (CI ve hızlı test için)
    - window-size sabit
    - no-sandbox + dev-shm disable
    """
    chrome_options = Options()

    if HEADLESS:
        chrome_options.add_argument("--headless=new")

    chrome_options.add_argument("--window-size=1400,900")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--lang=tr-TR")

    drv = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
    )

    drv.set_page_load_timeout(30)
    yield drv
    drv.quit()


@pytest.fixture()
def wait(driver, wait_seconds):
    return WebDriverWait(driver, wait_seconds)


@pytest.fixture()
def screenshot_on_fail(request, driver):
    """
    Test FAIL olursa otomatik screenshot alır.
    """
    yield
    # pytest burada test sonucunu request.node.rep_call'a yazar (hook ile)
    rep = getattr(request.node, "rep_call", None)
    if rep and rep.failed:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        name = request.node.name.replace("/", "_").replace(" ", "_")
        path = SCREENSHOT_DIR / f"{name}_{ts}.png"
        driver.save_screenshot(str(path))
        print(f"\n[SCREENSHOT] {path}")


def pytest_runtest_makereport(item, call):
    """
    screenshot_on_fail fixture'ı için raporu attach eder.
    """
    if call.when == "call":
        item.rep_call = call


# -------------------------
# Yardımcı E2E fonksiyonları
# -------------------------

def ui_login(driver, wait, base_url, email, password):
    driver.get(f"{base_url}/login")
    wait.until(EC.presence_of_element_located((By.NAME, "email"))).clear()
    driver.find_element(By.NAME, "email").send_keys(email)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    # Login başarılıysa "Çıkış" linki gelir
    wait.until(EC.presence_of_element_located((By.LINK_TEXT, "Çıkış")))


def ui_logout(driver, wait, base_url):
    driver.get(f"{base_url}/")
    wait.until(EC.presence_of_element_located((By.LINK_TEXT, "Çıkış"))).click()
    wait.until(EC.presence_of_element_located((By.LINK_TEXT, "Giriş")))


@pytest.fixture(scope="session")
def test_user_credentials():
    """
    Eğer env ile kullanıcı vermezsen, bazı testler skip olur.
    Öneri: Mevcut bir hesabın varsa env gir:
      export TEST_USER_EMAIL="..."
      export TEST_USER_PASSWORD="..."
    """
    email = os.getenv("TEST_USER_EMAIL")
    password = os.getenv("TEST_USER_PASSWORD")
    return email, password
