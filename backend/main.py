import hashlib
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Optional

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from logic import MAX_LEN, validate_text
from service.core import make_core
from service.messages import MessageService
from service.models import MessageResult, MessageStatus
from service.public_ids import format_id, parse_id
from service.settings import load_settings
from service.storage import Storage

load_dotenv()
VALID_TOKENS = [t.strip() for t in os.getenv("API_TOKENS", "").split(",") if t.strip()]
ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(",")
    if o.strip()
]
MAX_FAILS = 15           # неудачных входов с одного IP ...
FAIL_WINDOW = 60         # ... за столько секунд, потом 429

log = logging.getLogger("api")

settings = load_settings()
storage = Storage(settings.db_path)
core = make_core(settings)
service = MessageService(storage, core, settings)
storage.recover_interrupted(0)
storage.purge_expired()
storage.recover_interrupted(settings.stale_processing_s)
app = FastAPI(title="Mirtek Support Bot (prototype)")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Если падение произошло из-за переполнения стека (глубокий JSON) или парсинга
    if isinstance(exc, RecursionError) or "JSON" in str(exc) or "parsing" in str(exc).lower():
        return JSONResponse(
            status_code=422,
            content={"error": "bad_json", "detail": "JSON structure is too deep or malformed"}
        )

    # Для всех остальных непредвиденных системных ошибок возвращаем 500
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error"}
    )


@app.exception_handler(sqlite3.Error)
async def storage_error_handler(request: Request, exc: sqlite3.Error):
    log.error("ошибка хранилища: %r", exc)
    return JSONResponse(status_code=503, content={"error": "storage_unavailable"})


app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"loc": [str(x) for x in e.get("loc", ())], "type": e.get("type", ""), "msg": e.get("msg", "")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


# Ограничитель неудачных входов остаётся в памяти (это не данные чата)
LOCK = threading.RLock()
FAILED: dict[str, deque] = defaultdict(deque)   # ip -> времена неудачных входов


def is_valid(token: str) -> bool:
    candidate = token.encode("utf-8")
    ok = False
    for t in VALID_TOKENS:
        ok |= secrets.compare_digest(candidate, t.encode("utf-8"))
    return ok


def bearer(authorization: str) -> str:
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(session: dict, token: str) -> bool:
    return secrets.compare_digest(session["token_hash"], hash_token(token))


def require_token(authorization: str = Header(default="")) -> str:
    token = bearer(authorization)
    if not token or not is_valid(token):
        raise HTTPException(status_code=401, detail="invalid_token")
    return token


def get_session(session_id: str, token: str) -> dict:
    s = storage.get_session(session_id)
    if s is None:
        raise HTTPException(status_code=401, detail="unknown_session")
    if not token_matches(s, token):
        raise HTTPException(status_code=403, detail="session_token_mismatch")
    return s


def too_many_failures(ip: str) -> bool:
    now = time.time()
    with LOCK:
        q = FAILED[ip]
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        return len(q) >= MAX_FAILS


class AuthRequest(BaseModel):
    token: Optional[str] = None


class LogoutRequest(BaseModel):
    session_id: str


class MessageRequest(BaseModel):
    session_id: str
    text: str = Field(min_length=1, max_length=MAX_LEN)
    client_message_id: Optional[str] = None


class EscalateRequest(BaseModel):
    session_id: str
    message_id: str
    idempotency_key: Optional[str] = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/v1/auth")
def auth(
    request: Request,
    body: AuthRequest = Body(default=AuthRequest()),
    authorization: str = Header(default=""),
):
    token = body.token or bearer(authorization)   # токен: в теле или в заголовке Bearer
    if not token:
        raise HTTPException(status_code=401, detail="invalid_token")
    if not is_valid(token):
        ip = request.client.host if request.client else "?"
        if too_many_failures(ip):
            raise HTTPException(status_code=429, detail="rate_limited")
        with LOCK:
            FAILED[ip].append(time.time())
        raise HTTPException(status_code=401, detail="invalid_token")
    storage.purge_expired()
    session_id = "sess-" + uuid.uuid4().hex[:16]
    storage.create_session(session_id, hash_token(token), time.time() + settings.session_ttl_s)
    return {"session_id": session_id, "expires_in": settings.session_ttl_s}


@app.post("/api/v1/auth/logout")
def logout(body: LogoutRequest):
    if not storage.delete_session(body.session_id):
        raise HTTPException(status_code=401, detail="unknown_session")
    return {"status": "logged_out"}


def message_payload(result: MessageResult) -> dict:
    message_id = format_id("msg", result.message_id)
    if result.status == MessageStatus.ANSWERED:
        return {
            "message_id": message_id,
            "status": "answered",
            "answer": result.answer,
            "latency_ms": result.latency_ms,
        }
    if result.status == MessageStatus.TOO_COMPLEX:
        error = "Не удалось однозначно понять вопрос"
    else:
        error = "Не удалось обработать вопрос, попробуйте позже"
    return {
        "message_id": message_id,
        "status": "too_complex",
        "error": error,
        "actions": ["escalate"],
    }


@app.post("/api/v1/chat/messages")
def create_message(body: MessageRequest, token: str = Depends(require_token)):
    get_session(body.session_id, token)
    err = validate_text(body.text)
    if err:
        raise HTTPException(status_code=422, detail=err)
    result = service.submit(body.session_id, body.text, body.client_message_id or None)
    return message_payload(result)


@app.post("/api/v1/chat/escalate")
def escalate(body: EscalateRequest, token: str = Depends(require_token)):
    get_session(body.session_id, token)
    message_number = parse_id("msg", body.message_id)
    if message_number is None:
        message_number = -1          # такого номера в базе не бывает -> 404 message_not_found
    outcome, ticket = storage.create_ticket(
        body.session_id, message_number, body.idempotency_key or None
    )
    if outcome == "idempotency_conflict":
        raise HTTPException(status_code=409, detail="idempotency_conflict")
    if outcome == "message_not_found":
        raise HTTPException(status_code=404, detail="message_not_found")
    return {"ticket_id": format_id("ticket", ticket["id"]), "status": ticket["status"]}


def public_status(status: str) -> str:
    # Контракт знает только answered и too_complex: внутреннее failed показываем как too_complex
    return "too_complex" if status == MessageStatus.FAILED.value else status


def ticket_row(t: dict) -> dict:
    return {
        "ticket_id": format_id("ticket", t["id"]),
        "status": t["status"],
        "message_id": format_id("msg", t["message_id"]),
    }


@app.get("/api/v1/chat/status")
def status(
    session_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    authorization: str = Header(default=""),
):
    # Токен в заголовке необязателен (session_id и так секретный), но если он есть, он должен подходить
    s = storage.get_session(session_id) if session_id else None
    if s is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    token = bearer(authorization)
    if token and not token_matches(s, token):
        raise HTTPException(status_code=403, detail="session_token_mismatch")

    if ticket_id:
        number = parse_id("ticket", ticket_id)
        t = storage.get_ticket(number) if number is not None else None
        if t is None or t["session_id"] != session_id:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        return {"status": t["status"], "tickets": [ticket_row(t)]}

    tickets = [ticket_row(t) for t in storage.list_tickets(session_id)]
    msgs = [
        {
            "message_id": format_id("msg", m["id"]),
            "status": public_status(m["status"]),
            "ticket_id": format_id("ticket", m["ticket_id"]) if m["ticket_id"] is not None else None,
        }
        for m in storage.list_messages(session_id)
    ]
    if tickets:
        value = "queued"
    elif msgs:
        value = msgs[-1]["status"]
    else:
        value = "new"
    return {"status": value, "tickets": tickets, "messages": msgs}
