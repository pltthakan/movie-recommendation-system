# app/services/utils.py
import hashlib
import datetime

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", "ignore")).hexdigest()
