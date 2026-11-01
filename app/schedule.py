from datetime import datetime, date, time
from typing import Optional, Dict
from zoneinfo import ZoneInfo
from .models import Store

class ScheduleResolver:
    def __init__(self, timezone_str: str = "Asia/Krasnoyarsk"):
        self.tz = ZoneInfo(timezone_str)

    def get_store_status(self, store: Store, target_date: Optional[date] = None) -> Dict[str, str]:
        if target_date is None:
            target_date = datetime.now(self.tz).date()

        date_str = target_date.isoformat()
        weekday_str = target_date.strftime("%A").lower()

        weekday_map = {
            "monday": "monday",
            "tuesday": "tuesday",
            "wednesday": "wednesday",
            "thursday": "thursday",
            "friday": "friday",
            "saturday": "saturday",
            "sunday": "sunday"
        }
        day_key = weekday_map.get(weekday_str)

        for special in store.special_schedule:
            if special.get("date") == date_str:
                return {"status": "special", "hours": special.get("hours"), "reason": special.get("reason")}

        for holiday in store.holidays:
            if holiday.get("date") == date_str:
                return {"status": "holiday", "hours": holiday.get("hours"), "note": holiday.get("note")}

        hours = store.working_hours.get(day_key, "closed")
        
        if hours == "closed":
            return {"status": "closed", "hours": None, "reason": "Выходной день"}
        
        return {"status": "normal", "hours": hours, "reason": None}

    def is_open_now(self, store: Store) -> bool:
        now = datetime.now(self.tz)
        status = self.get_store_status(store, now.date())
        
        if status["status"] == "closed" or status["hours"] is None:
            return False
            
        try:
            open_str, close_str = status["hours"].split("-")
            open_time = datetime.strptime(open_str, "%H:%M").time()
            close_time = datetime.strptime(close_str, "%H:%M").time()
            
            return open_time <= now.time() <= close_time
        except ValueError:
            return False