# app/logging_config.py
import os
import logging
from logging.handlers import RotatingFileHandler


def setup_logging(app):
    """
    Uygulama loglamasını kurar.
    - Console'a log yazar (Docker için önemli)
    - Ayrıca logs/app.log dosyasına döner (isteğe bağlı)
    """

    # Aynı anda iki kere kurulmaması için koruma
    root_logger = logging.getLogger()
    if root_logger.handlers:
        # Zaten konfigüre edilmiş tekrar elleme
        return

    log_level = app.config.get("LOG_LEVEL", "INFO").upper()

    root_logger.setLevel(log_level)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ---- 1) Console Handler (stdout) ----
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # ---- 2) Dosya Handler (logs/app.log) ----
    log_dir = app.config.get("LOG_DIR", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "app.log")

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        # Dosya oluşturulamazsa en azından console loglamaya devam etsin
        root_logger.error("Log dosyası oluşturulamadı: %s", e)
