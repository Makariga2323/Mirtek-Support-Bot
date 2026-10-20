"""Чистая логика без FastAPI: проверка текста и выбор демо-ответа."""
from typing import Optional

MAX_LEN = 2000

# Демо-ответы. Это заглушки, не реальные данные.
STUBS = [
    (("заказ", "ord-", "достав"), "[демо] Статус заказа: в пути."),
    (("работ", "график", "скольки", "открыва", "расписани"), "[демо] Магазин работает ежедневно с 09:00 до 21:00."),
    (("товар", "артикул", "налич", "art-", "забрать"), "[демо] Товар есть в наличии."),
    (("цена", "стоит"), "[демо] Цена по каталогу: 100 ₽."),
    (("возврат",), "[демо] Возврат возможен в течение 14 дней."),
    (("акци",), "[демо] Сейчас действует скидка 10%."),
]
# Признаки сложного вопроса: такие вопросы уходят оператору
COMPLEX_MARKS = ("одновременно", "не уверен", "почему", "сделайте", "не сформулировал")


def validate_text(text: str) -> Optional[str]:
    """Возвращает код ошибки (для ответа 422) или None, если текст допустим."""
    if len(text) > MAX_LEN:
        return "text_too_long"
    norm = text.replace("\ufeff", "").strip()
    if not norm:
        return "empty_text"
    if any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in norm):
        return "control_characters"
    if any(0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069 for c in norm):
        return "bidi_override"
    return None


def answer_for(text: str) -> Optional[str]:
    """Демо-ответ или None, если вопрос непонятен или слишком сложный."""
    norm = text.replace("\ufeff", "").strip()
    low = norm.lower()
    letters = sum(1 for c in norm if c.isalpha())
    special = sum(1 for c in norm if not c.isalnum() and not c.isspace())
    if (
        letters < 2
        or len(norm) > 280
        or special / max(len(norm), 1) > 0.4
        or "http://" in low
        or "https://" in low
        or (" " not in norm and len(norm) > 40)
        or any(m in low for m in COMPLEX_MARKS)
    ):
        return None
    for words, stub in STUBS:
        if any(w in low for w in words):
            return stub
    return None
