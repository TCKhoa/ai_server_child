import re
import unicodedata
from config import BAD_WORDS, SYSTEM_WORDS

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None


def normalize_text(text: str):
    text = text.lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join([c for c in text if not unicodedata.combining(c)])

    replace_map = {
        "1": "i", "!": "i", "3": "e", "4": "a",
        "@": "a", "0": "o", "$": "s", "5": "s"
    }

    for k, v in replace_map.items():
        text = text.replace(k, v)

    text = re.sub(r'[^\w\s]', ' ', text)
    return text.strip()


def is_valid_text(text: str):
    if not text.strip():
        return False
    digit_ratio = sum(c.isdigit() for c in text) / max(len(text), 1)
    return digit_ratio <= 0.6


def is_system_text(text: str):
    for w in SYSTEM_WORDS:
        if w in text:
            return True
    return False


def build_fuzzy_pattern(word):
    if len(word) < 2:
        return None
    return r"\b" + r"[\W_]*".join(list(word)) + r"\b"


def rule_check(text: str):
    # Match hoàn chỉnh (word boundary) để tránh false positive ví dụ từ "search"
    for word in BAD_WORDS:
        pattern = rf"\b{re.escape(word)}\b"
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True, word

    # Enhance đánh chặn chữ viết tắt lách vòng như "s-e-x"
    for word in BAD_WORDS:
        pattern = build_fuzzy_pattern(word)
        if pattern and re.search(pattern, text, flags=re.IGNORECASE):
            return True, word

    return False, None


def fuzzy_check(text: str):
    if fuzz is None:
        return False, None, 0

    for word in BAD_WORDS:
        score = fuzz.partial_ratio(word, text)
        if score > 95:
            return True, word, score
    return False, None, 0
