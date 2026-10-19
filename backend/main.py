import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

load_dotenv()
VALID_TOKENS = [t.strip() for t in os.getenv("API_TOKENS", "").split(",") if t.strip()]

app = FastAPI()
#allowing the frontend to operate with the backend (Live server is usually o 5500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем абсолютно любые входящие адреса
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



def is_valid(token: str) -> bool:
    if not token or not VALID_TOKENS:
        return False
    # Защита от атак по времени (Time-based side-channel attacks)
    for valid_token in VALID_TOKENS:
        if len(token) == len(valid_token):
            if secrets.compare_digest(token.encode("utf-8"), valid_token.encode("utf-8")):
                return True
    return False


def require_token(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not is_valid(token):
        raise HTTPException(status_code=401, detail="Неверный токен")


class LoginRequest(BaseModel):
    token: str


class Inquiry(BaseModel):
    text: str


@app.post("/api/login")
def login(body: LoginRequest):
    try:
        if not is_valid(body.token):
            raise HTTPException(status_code=401, detail="Неверный токен")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/inquiries", dependencies=[Depends(require_token)])
def create_inquiry(body: Inquiry):

    words = ["режим", "работ", "цена", "стоит", "доставк" "наличи", "возврат", "акци"]
    lower = body.text.lower()

    is_clear = any(w in lower for w in words)

    if is_clear:
        return {"answer": "Готовый ответ на ваш вопрос (ответ от сервера).", "handoff_to_operator":False}
    else:
        return {"answer": "Не удалось разобрать вопрос. Можно передать оператору.", "handoff_to_operator":True}