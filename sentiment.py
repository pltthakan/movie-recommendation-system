from typing import Tuple, Dict
import os
from transformers import pipeline, AutoModelForSequenceClassification, AutoTokenizer

_PIPE = None


def _load_pipeline():
    """Load the RoBERTa-Large sentiment model (SiEBERT) for maximum general accuracy."""
    global _PIPE
    if _PIPE is not None:
        return _PIPE

    # SiEBERT'e geri dönülüyor: En iyi genel doğruluk.
    model_name = os.environ.get("SENTIMENT_MODEL", "siebert/sentiment-roberta-large-english")

    # GPU kullanımı için cihaz ayarı
    device = 0 if os.environ.get("HF_USE_CPU") is None and os.cpu_count() is not None else -1

    try:
        model = AutoModelForSequenceClassification.from_pretrained(model_name)
        tokenizer = AutoTokenizer.from_pretrained(model_name)

        _PIPE = pipeline(
            task="sentiment-analysis",
            model=model,
            tokenizer=tokenizer,
            truncation=True,
            top_k=None,
            device=device
        )
        print(
            f"[sentiment] SiEBERT pipeline loaded -> {model_name} (Device: {'GPU' if device != -1 else 'CPU'})")
        return _PIPE

    except Exception as e:
        # SiEBERT silinmişse, tekrar indirilmeye çalışılır.
        print(f"[DEBUG] Could not load SiEBERT model '{model_name}'. Falling back to NEU/0.5: {e}")
        _PIPE = None
        return None


# --- Yardımcı Fonksiyon: _extract_pos_neg (2 Etiketli SiEBERT için Sadeleştirildi) ---
def _extract_pos_neg(scores_list) -> Tuple[float, float]:
    """
    scores_list: pipeline(text)[0] çıktısı (list of {label, score})
    SiEBERT'in 2 etiketli (POS/NEG) çıktısından skorları döndürür.
    """
    pos = neg = None

    # SiEBERT (2 etiketli) veya LABEL_0/1 etiketlerini yakalar.
    for item in scores_list:
        lab = str(item.get("label", "")).lower()
        sc = float(item.get("score", 0.0))

        if "pos" in lab or lab == "label_1":
            pos = sc
        elif "neg" in lab or lab == "label_0":
            neg = sc

    # Eğer bir skor eksikse (nadiren olur, sadece güvenlik için), diğerini 1.0'dan çıkararak tamamla.
    if pos is None and neg is None and len(scores_list) == 2:
        # Etiketler bilinmiyorsa, daha yüksek skoru POS kabul et
        s0 = float(scores_list[0]["score"]);
        s1 = float(scores_list[1]["score"])
        if s1 >= s0:
            pos, neg = s1, s0
        else:
            pos, neg = s0, s1

    # Skorlar None ise 0.0 olarak ayarla (Normalde gelmez)
    pos = float(pos) if pos is not None else 0.0
    neg = float(neg) if neg is not None else 0.0

    # Toplamın 1'e yakın olduğu varsayılır (Modelin doğası gereği).
    return pos, neg


# --- Ana Analiz Fonksiyonu (NEU_MARGIN Geri Getirildi) ---
def analyze_sentiment(text: str) -> Tuple[str, float]:
    """
    Çıktı: ("pos" | "neg" | "neu", confidence)
    """
    text_l = text.strip()
    pipe = _load_pipeline()

    if pipe:
        try:
            if not text_l:
                return "neu", 0.5

            out = pipe(text_l)[0]
            # 2 skor bekleniyor
            pos_score, neg_score = _extract_pos_neg(out)

            # Nötr (NEU) Karar Marjı (SiEBERT için gereklidir)
            neu_margin = float(os.environ.get("NEU_MARGIN", "0.05"))  # Varsayılan marj

            # Nötr Kararı: Skorlar birbirine yeterince yakınsa Nötr'dür.
            if abs(pos_score - neg_score) <= neu_margin:
                print(f"[SENTIMENT DEBUG] '{text_l[:60]}...' -> NEU (pos={pos_score:.3f}, neg={neg_score:.3f})")
                return "neu", max(pos_score, neg_score)

            # Pozitif/Negatif Kararı
            if pos_score > neg_score:
                print(f"[SENTIMENT DEBUG] '{text_l[:60]}...' -> POS (pos={pos_score:.3f}, neg={neg_score:.3f})")
                return "pos", pos_score
            else:
                print(f"[SENTIMENT DEBUG] '{text_l[:60]}...' -> NEG (pos={pos_score:.3f}, neg={neg_score:.3f})")
                return "neg", neg_score

        except Exception as e:
            print(f"[DEBUG] Sentiment analysis error: {e} -> using fallback")

    return "neu", 0.5


# --- Olasılıkları Döndüren Fonksiyon (SiEBERT için Sadeleştirildi) ---
def analyze_sentiment_probs(text: str) -> Tuple[str, float, Dict[str, float]]:
    """
    label, confidence, {"pos": p, "neg": p} döndürür.
    """
    label, conf = analyze_sentiment(text)
    if _PIPE:
        try:
            out = _PIPE(text.strip())[0]
            # Sadece POS ve NEG skorları döndürülür
            pos_score, neg_score = _extract_pos_neg(out)
            return label, conf, {"pos": pos_score, "neg": neg_score}
        except Exception:
            pass

    # Hata durumunda veya nötr durumunda varsayılan olasılıklar
    return label, conf, {"pos": 0.5 if label == "neu" else (conf if label == "pos" else 1.0 - conf),
                         "neg": 0.5 if label == "neu" else (conf if label == "neg" else 1.0 - conf)}


# --- app.py ile uyumlu wrapper ---
def analyze(text: str):
    """
    app.py eski arayüz: ("POS" | "NEG" | "NEU", confidence)
    """
    lab, score = analyze_sentiment(text)
    lab_up = {"pos": "POS", "neg": "NEG", "neu": "NEU"}[lab]
    return lab_up, score