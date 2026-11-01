"""Ядро без языковой модели (CORE_MODE=rules): программная классификация + ответы по данным.

Конвейер:
  1. Быстрые правила (оператор, иностранный язык, мусор, взлом промпта) -> оператор.
  2. Определение сценариев по шаблонам смысла (не по списку готовых фраз): график, наличие, цена, акции,
     доставка и самовывоз, возврат, FAQ. Товары и магазины ищутся по названию, синонимам и артикулу.
  3. Ответ собирается из данных кодом: остатки с учётом резерва, график с праздниками и особыми днями,
     акции только действующие на дату. Выдумать значение здесь невозможно: каждое число взято из JSON.
  4. Составной вопрос: отвечаем, только если смогли ответить на ВСЕ части, иначе оператор.
     Нет товара или данных, неизвестная тема, неуверенное совпадение -> оператор (TOO_COMPLEX).
Только стандартная библиотека, ответ за миллисекунды.
"""
import re
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from .deadline import Deadline
from .models import CoreResult, MessageStatus
from .real_core import WEEKDAYS, WEEKDAY_RU, Decision, KnowledgeBase, _fmt_price, _norm, _stem, _words, precheck

MAX_INTENTS = 3

RX = {
    "hours": re.compile(r"работ|график|открыт|закрыт|закрыва|откры|скольки|во сколько|выходн|расписан|часы"
                        r"|(?:сегодня|завтра|послезавтра) до\b|\bдо (?:восьми|девяти|десяти|семи|шести|пяти|двадцат\w*|\d{1,2})\b"),
    "promo": re.compile(r"акци|скидк|промокод|распродаж|спецпредложен|предложен"),
    "delivery": re.compile(r"достав|самовывоз|курьер|привез|привоз|отправк|отправит|бронь|бронир"
                           r"|забрать заказ|забрат\w* (?:его |ее )?сам|заберу сам|самостоятельно"),
    "contacts": re.compile(r"связат|телефон|позвонить|позвони|почт|email|e mail|горяч|контакт|куда писать"),
    "returns": re.compile(r"возврат|вернут|вернуть|обмен|обменя"),
    "price": re.compile(r"сколько стоит|стоимост|почем|по чем|стоят|\bцена\b|\bцены\b|\bцену\b|\bценой\b|руб"),
    "stock": re.compile(r"налич|остал|в продаже|продает|продае|забрат|заберу|\bгде\b|в каком|склад|купить"),
}
PICKUP = re.compile(r"самовывоз|забрат|заберу|бронь|бронир")
DEMAND = re.compile(r"(?:сделайте|сделай|дайте|дай|предоставьте)\s+(?:мне\s+)?(?:скидк|бесплатн|подарок|промокод)"
                    r"|обязаны|требую|верните деньги|жалоб|претензи|обман|безобраз|ужасн|дурак|идиот|хамств")
DAYS_OFF = re.compile(r"выходн")
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "мае": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
DATE_WORDS = re.compile(r"\b(\d{1,2})\s+(январ\w*|феврал\w*|март\w*|апрел\w*|мая|мае|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)")
DATE_DOTS = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?\b")
MAX_DAYS_AHEAD = 14          # особые дни в данных известны только на ближайшие 14 дней
MONEY_BACK = re.compile(r"деньг|денег|на карту|возврат средств|вернут средств|вернут деньг")
CITY_MOSCOW = re.compile(r"москв")
CITY_SPB = re.compile(r"петербург|питер|спб")
WEEKDAY_STEMS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресень"]

UNIT_FORMS = {
    "метр": ("метр", "метра", "метров"),
    "штука": ("штука", "штуки", "штук"),
    "упаковка": ("упаковка", "упаковки", "упаковок"),
}


def _qty(n: float, unit: str) -> str:
    value = int(n) if float(n).is_integer() else n
    forms = UNIT_FORMS.get(unit)
    if not forms or not isinstance(value, int):
        return f"{value} {forms[1] if forms else unit}"
    if 11 <= value % 100 <= 14:
        form = forms[2]
    elif value % 10 == 1:
        form = forms[0]
    elif 2 <= value % 10 <= 4:
        form = forms[1]
    else:
        form = forms[2]
    return f"{value} {form}"


class RulesCore:
    def __init__(self, kb: KnowledgeBase, now_fn: Optional[Callable[[], datetime]] = None):
        self._kb = kb
        self._now = now_fn or (lambda: datetime.now(kb.tz))

    # ---------------- публичный интерфейс ----------------
    def answer(self, text: str, deadline: Deadline) -> CoreResult:
        d = self.answer_detailed(text, deadline)
        return CoreResult(d.status, d.answer)

    def answer_detailed(self, text: str, deadline: Deadline | None = None) -> Decision:
        reason = precheck(text)
        if reason:
            return Decision(MessageStatus.TOO_COMPLEX, reason=reason)

        low = _norm(text)
        if DEMAND.search(low):
            return Decision(MessageStatus.TOO_COMPLEX, reason="demand_or_complaint")
        now = self._now()
        products = self._kb.find_products(text)
        stores = self._kb.find_stores(text)

        faq_strong = self._faq(text, strong=True)
        if faq_strong:
            return Decision(MessageStatus.ANSWERED, faq_strong, "faq", "faq_match")

        intents = self._intents(low, products)
        if not intents:
            faq_weak = self._faq(text, strong=False)
            if faq_weak:
                return Decision(MessageStatus.ANSWERED, faq_weak, "faq", "faq_weak_match")
            return Decision(MessageStatus.TOO_COMPLEX, reason="unknown_category")
        if len(intents) > MAX_INTENTS:
            return Decision(MessageStatus.TOO_COMPLEX, reason="too_many_intents")

        parts = []
        for name in intents:
            part = self._handle(name, text, low, now, products, stores)
            if not part:                         # на часть вопроса ответить нельзя -> весь вопрос оператору
                return Decision(MessageStatus.TOO_COMPLEX, scenario=name, reason=f"no_data_for_{name}")
            parts.append(part)
        return Decision(MessageStatus.ANSWERED, "\n".join(parts), "+".join(intents), "answered")

    # ---------------- классификация ----------------
    def _intents(self, low: str, products: list[dict]) -> list[str]:
        found = []
        if RX["hours"].search(low):
            found.append("hours")
        if products:
            wants_price = bool(RX["price"].search(low))
            wants_stock = bool(RX["stock"].search(low)) or (
                bool(re.search(r"\bесть\b", low)) and not self._other_topics(low))
            if wants_price:
                found.append("price")
            # без явного «цена/наличие» товар из вопроса показываем только если других тем нет
            if wants_stock or (not wants_price and not found and not self._other_topics(low)):
                found.append("stock")
        if RX["promo"].search(low):
            found.append("promo")
        if RX["delivery"].search(low):
            found.append("delivery")
        if RX["returns"].search(low):
            found.append("returns")
        if RX["contacts"].search(low):
            found.append("contacts")
        return found

    @staticmethod
    def _other_topics(low: str) -> bool:
        return any(RX[k].search(low) for k in ("promo", "delivery", "returns", "hours"))

    def _handle(self, name, text, low, now, products, stores) -> Optional[str]:
        if name == "hours":
            return self._hours(text, low, now, stores)
        if name == "contacts":
            return self._contacts()
        if name == "price":
            return self._price(products)
        if name == "stock":
            return self._stock(products, stores)
        if name == "promo":
            return self._promo(now, products)
        if name == "delivery":
            return self._delivery(text, low)
        if name == "returns":
            return self._returns(low)
        return None

    # ---------------- график работы ----------------
    def _explicit_date(self, text: str, low: str, today: date) -> Optional[date]:
        m = DATE_WORDS.search(low)
        day = month = None
        if m:
            day = int(m.group(1))
            month = next((v for k, v in MONTHS.items() if m.group(2).startswith(k)), None)
        else:
            m = DATE_DOTS.search(text)
            if m:
                day, month = int(m.group(1)), int(m.group(2))
        if not day or not month:
            return None
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                return None
            if candidate >= today:
                return candidate
        return None

    def _target_day(self, text: str, low: str, now: datetime) -> tuple[Optional[date], str]:
        today = now.date()
        explicit = self._explicit_date(text, low, today)
        if explicit:
            return explicit, "на"
        if "послезавтра" in low:
            return today + timedelta(days=2), "послезавтра"
        if "завтра" in low:
            return today + timedelta(days=1), "завтра"
        for i, stem in enumerate(WEEKDAY_STEMS):
            if stem in low:
                return today + timedelta(days=(i - today.weekday()) % 7), "в " + WEEKDAY_RU[i]
        return today, "сегодня"

    def _days_off(self, stores: list[dict]) -> str:
        lines = []
        for s in stores or self._kb.stores:
            off = [WEEKDAY_RU[i] for i, k in enumerate(WEEKDAYS) if s["working_hours"].get(k, "closed") == "closed"]
            lines.append(f"{s['name']}: " + ("выходной — " + ", ".join(off) + "." if off else "без выходных."))
        return "\n".join(lines)

    def _hours(self, text: str, low: str, now: datetime, stores: list[dict]) -> Optional[str]:
        explicit = self._explicit_date(text, low, now.date())
        if DAYS_OFF.search(low) and not explicit and not re.search(r"сегодня|завтра|послезавтра", low):
            return self._days_off(stores)
        day, label = self._target_day(text, low, now)
        chosen = stores or self._kb.stores
        if explicit and (explicit - now.date()).days > MAX_DAYS_AHEAD:
            covered = any(e.get("date") == explicit.isoformat()
                          for s in chosen for e in list(s.get("holidays", [])) + list(s.get("special_schedule", [])))
            if not covered:
                return None                       # про такую дату в данных ничего нет -> оператор
        lines = []
        for s in chosen:
            hours, note = self._kb.store_day(s, day)
            if hours == "closed":
                h = "закрыт" + (f" ({note})" if note else "")
            else:
                h = f"работает {hours}" + (f" ({note})" if note else "")
            line = f"{s['name']} ({s['address']}), {label} {day.strftime('%d.%m.%Y')}: {h}."
            if day == now.date():
                line += " Сейчас открыт." if self._kb._open_now(s, now) else " Сейчас закрыт."
            lines.append(line)
        return "\n".join(lines)

    def _contacts(self) -> Optional[str]:
        c = self._kb.data.get("contacts")
        if not c:
            return None
        return (f"Телефон поддержки: {c['support_phone']}, почта: {c['support_email']}. "
                f"Время работы поддержки: {c['working_hours']}.")

    # ---------------- товары: цена и наличие ----------------
    def _price(self, products: list[dict]) -> Optional[str]:
        if not products:
            return None
        return "\n".join(f"{p['name']} (арт. {p['article']}): {_fmt_price(p['price_per_unit'])} руб. за {p['unit']}."
                         for p in products)

    def _stock_by_store(self, article: str) -> list[tuple[dict, float, str]]:
        out = []
        for wh in self._kb.warehouses:
            store = self._kb._store_by_id.get(wh.get("store_id"))
            if not store:
                continue                          # виртуальный склад не относится к магазинам
            for st in wh.get("stock", []):
                if st["article"] == article:
                    out.append((store, st["quantity"] - st.get("reserved", 0), st["unit"]))
        return out

    def _stock(self, products: list[dict], stores: list[dict]) -> Optional[str]:
        if not products:
            return None
        asked = {s["store_id"] for s in stores}
        lines = []
        for p in products:
            per = self._stock_by_store(p["article"])
            available = [(s, n, u) for s, n, u in per if n > 0]
            head = f"{p['name']} (арт. {p['article']})"
            if asked:
                here = [(s, n, u) for s, n, u in per if s["store_id"] in asked]
                parts = [f"{s['name']}: {_qty(n, u)} в наличии" if n > 0 else f"{s['name']}: нет в наличии"
                         for s, n, u in here]
                others = [(s, n, u) for s, n, u in available if s["store_id"] not in asked]
                text = f"{head}. " + "; ".join(parts) + "."
                if others and all(n <= 0 for _, n, _ in here):
                    text += " Есть в других магазинах: " + "; ".join(f"{s['name']} ({_qty(n, u)})" for s, n, u in others) + "."
            elif available:
                text = f"{head} в наличии: " + "; ".join(f"{s['name']} — {_qty(n, u)}" for s, n, u in available) + "."
            else:
                text = f"{head}: сейчас нет в наличии ни в одном магазине."
            lines.append(text)
        return "\n".join(lines)

    # ---------------- акции: только действующие на дату ----------------
    def _promo(self, now: datetime, products: list[dict]) -> str:
        articles = [p["article"] for p in products] or None
        promos = self._kb.active_promotions(now.date(), articles)
        if not promos:
            return ("Сейчас нет действующих акций на этот товар." if articles
                    else "Сейчас действующих акций нет.")
        return "Действующие акции:\n" + "\n".join("– " + self._kb.promo_chunk(p) for p in promos)

    # ---------------- доставка и самовывоз ----------------
    def _unknown_destination(self, text: str) -> bool:
        """«Доставка в Новосибирск / на Луну»: город с заглавной буквы, которого нет в данных -> оператор."""
        for word in re.findall(r"\b(?i:в|на)\s+([А-ЯЁ][а-яё\-]{2,})", text):
            stem = _stem(_norm(word).strip())
            if stem in ("москв", "санкт", "петер", "росси") or self._kb.find_stores(word):
                continue
            return True
        return False

    def _delivery(self, text: str, low: str) -> Optional[str]:
        d = self._kb.data.get("delivery")
        if not d or not d.get("available", True):
            return None
        if self._unknown_destination(text):
            return None
        pickup_only = bool(PICKUP.search(low)) and not re.search(r"курьер|доставк|привез|привоз", low)
        parts = []
        for m in d.get("methods", []):
            if m.get("zones"):
                if pickup_only:
                    continue
                zones = m["zones"]
                if CITY_MOSCOW.search(low):
                    zones = [z for z in zones if "Москва" in z["city"]] or zones
                elif CITY_SPB.search(low):
                    zones = [z for z in zones if "Петербург" in z["city"]] or zones
                parts.append(f"{m['name']}: " + "; ".join(
                    f"{z['city']} — {z['cost']} руб., бесплатно от {z['free_from']} руб., срок {z['days']} дн."
                    for z in zones))
            else:
                names = ", ".join(self._kb._store_by_id[s]["name"] for s in m.get("stores", []) if s in self._kb._store_by_id)
                parts.append(f"{m['name']}: бесплатно, {m.get('conditions', '').rstrip('.')}. Магазины: {names}.")
        if not pickup_only:
            r = d.get("restrictions", {})
            parts.append(f"Ограничения: вес до {r.get('max_weight_kg')} кг, длина до {r.get('max_length_m')} м, "
                         f"минимальная сумма заказа {r.get('min_order_amount')} руб.")
        return "\n".join(parts)

    # ---------------- возврат ----------------
    def _returns(self, low: str) -> Optional[str]:
        r = self._kb.data.get("returns")
        if not r:
            return None
        if MONEY_BACK.search(low) and r.get("refund_timeline"):
            timeline = "; ".join(f"{x['method']} — {x['days']} дн." for x in r["refund_timeline"])
            return f"{r.get('policy', '')}\nСроки возврата денег: {timeline}.".strip()
        parts = [r.get("policy", "")]
        if r.get("conditions"):
            parts.append("Условия: " + "; ".join(r["conditions"]) + ".")
        if r.get("non_returnable"):
            parts.append("Не подлежат возврату: " + "; ".join(r["non_returnable"]) + ".")
        if r.get("process"):
            parts.append("Порядок: " + "; ".join(r["process"]) + ".")
        return "\n".join(p for p in parts if p)

    # ---------------- FAQ ----------------
    def _faq(self, text: str, strong: bool) -> Optional[str]:
        q_stems = {_stem(w) for w in _words(text)}
        best, best_key = None, (0, 0.0)
        for item in self._kb.data.get("faq", {}).get("questions", []):
            fs = {_stem(w) for w in _words(item["question"])}
            if not fs:
                continue
            overlap = len(fs & q_stems)
            ratio = overlap / len(fs)
            if (overlap, ratio) > best_key:
                best, best_key = item, (overlap, ratio)
        overlap, ratio = best_key
        need_ratio = 0.75 if strong else 0.5
        if best and (overlap >= 2 or (overlap == 1 and ratio == 1.0)) and ratio >= need_ratio:
            return best["answer"]
        return None


def make_rules_core(settings) -> RulesCore:
    return RulesCore(KnowledgeBase.from_file(settings.data_path))
