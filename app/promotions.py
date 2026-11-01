from datetime import date, datetime
from typing import List
from .models import Promotion

class PromotionChecker:
    def __init__(self, promotions: List[Promotion]):
        self.promotions = promotions

    def get_active_promotions(self, target_date: date = None, article: str = None, store_id: str = None) -> List[Promotion]:
        if target_date is None:
            target_date = date.today()

        active = []
        for promo in self.promotions:
            start = datetime.strptime(promo.start_date, "%Y-%m-%d").date()
            end = datetime.strptime(promo.end_date, "%Y-%m-%d").date()

            if not (start <= target_date <= end):
                continue

            if article and "*" not in promo.applicable_articles:
                if article not in promo.applicable_articles:
                    continue

            if store_id and "*" not in promo.applicable_stores:
                if store_id not in promo.applicable_stores:
                    continue

            active.append(promo)

        return active