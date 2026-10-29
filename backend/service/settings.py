import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = str(BACKEND_DIR/"data"/"app.sqlite3")

@dataclass(frozen=True)
class Settings:
    core_mode: str
    request_budget_s: float
    session_ttl_s: int
    db_path: str
    message_retention_s: int
    stale_processing_s: int

def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        core_mode = os.getenv("CORE_MODE", "stub"),
        request_budget_s = float(os.getenv("REQUEST_BUDGET_S", "5")), 
        session_ttl_s = int(os.getenv("SESSION_TTL_S", "3600")),
        db_path=os.getenv("DB_PATH", DEFAULT_DB_PATH),
        message_retention_s = int(os.getenv("MESSAGE_RETENTION_S", "604800")), 
        stale_processing_s = int(os.getenv("STALE_PROCESSING", "60")),
    )