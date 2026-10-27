import pytest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from app.data_loader import DataManager
from app.schedule import ScheduleResolver
from app.promotions import PromotionChecker

@pytest.fixture
def dm():
    return DataManager()

def test_find_product(dm):
    results = dm.search_products("синий атлас")
    assert len(results) > 0
    assert results[0]["article"] == "12345"

def test_stock_reserved(dm):
    stock = dm.get_available_stock("12345", "STORE-001")
    assert len(stock) > 0
    assert stock[0]["available"] == 40

def test_schedule_holiday(dm):
    resolver = ScheduleResolver()
    store = dm.data.stores["stores"][0]
    status = resolver.get_store_status(store, date(2026, 1, 1))
    assert status["status"] == "holiday"
    assert status["hours"] == "closed"

def test_schedule_special(dm):
    resolver = ScheduleResolver()
    store = dm.data.stores["stores"][0]
    status = resolver.get_store_status(store, date(2026, 10, 20))
    assert status["status"] == "special"
    assert status["reason"] == "Санитарный день"

def test_active_promotions(dm):
    checker = PromotionChecker(dm.data.promotions["promotions"])
    active = checker.get_active_promotions(date(2026, 10, 20))
    assert any(p.promotion_id == "PROMO-001" for p in active)
    assert not any(p.promotion_id == "PROMO-002" for p in active)

def test_timezone_is_krasnoyarsk(dm):
    resolver = ScheduleResolver()
    now_krsk = datetime.now(ZoneInfo("Asia/Krasnoyarsk"))
    now_utc = datetime.now(ZoneInfo("UTC"))
    
    assert resolver.tz.key == "Asia/Krasnoyarsk"
    
    delta = now_krsk.utcoffset() - now_utc.utcoffset()
    assert delta.total_seconds() == 7 * 3600