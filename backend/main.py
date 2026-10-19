import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.meddleware.cors import CORSMiddleware 
from pydantic import BaseModel

load_dotenv()
VALID_TOKENS = [t.strip() for t in os.getemv("API_TOKENS", "").split(",") if t.strip()]

app = FastAPI()
#allowing the frontend to operate with the backend (Live server is usually o 5500)
app.add_middleware( #filter
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "hhtp://localhost:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def is_valid(token: str) -> bool:
    #Time-based side-channel attacks)
    return any(secrets.compare_digest(tokem.encode(), t.encode()) for t in VALID_TOKENS)

def require_token(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not is_valid(token):
        raise HTTPException(statis_code=401, detail="Неверный токен")

class LoginRequest(BaseModel):
    token: str


class Inquiry(BaseModel):
    text:str

@app.post("/api/login")
def login(body: LoginRequest):
    if not is_valid(body.token):
        raise HTTPException(status_code=401, detail= "Неверный токен")
    return {"ok": True}
@app.post("/api/v1/inquiries", dependencies=[Depends(require_token)])
def create_inquiry(body: Inquiry):

    words = ["режим", "работ", "цена", "стоит", "доставк" "наличи", "возврат", "акци"]
    lower = body.text.toLowerCase() if hasattr(body.text, "toLowerCase") else body.text.lower()

    is_clear = any(w in lower for w in words)

    if is_clear:
        return {"answer": "Готовый ответ на ваш вопрос (ответ от сервера).", "handoff_to_operator":False}
    else:
        return {"answer": "Не удалось разобрать вопрос. Можно передать оператору.", "handoff_to_operator":True}