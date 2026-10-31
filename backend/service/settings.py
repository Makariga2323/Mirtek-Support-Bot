import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = str(BACKEND_DIR/"data"/"app.sqlite3")
DEFAULT_DATA_PATH = str(BACKEND_DIR.parent/"data"/"MOCK_DATA.json")

@dataclass(frozen=True)
class Settings:
    core_mode: str
    request_budget_s: float
    session_ttl_s: int
    db_path: str
    message_retention_s: int
    stale_processing_s: int
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    data_path: str = DEFAULT_DATA_PATH

def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        core_mode = os.getenv("CORE_MODE", "stub"),
        request_budget_s = float(os.getenv("REQUEST_BUDGET_S", "5")), 
        session_ttl_s = int(os.getenv("SESSION_TTL_S", "3600")),
        db_path=os.getenv("DB_PATH", DEFAULT_DB_PATH),
        message_retention_s = int(os.getenv("MESSAGE_RETENTION_S", "604800")), 
        stale_processing_s = int(os.getenv("STALE_PROCESSING_S", "60")),
        llm_base_url = os.getenv("LLM_BASE_URL", ""),
        llm_api_key = os.getenv("LLM_API_KEY", ""),
        llm_model = os.getenv("LLM_MODEL", ""),
        data_path = os.getenv("DATA_PATH", DEFAULT_DATA_PATH),
    )