import os
import secrets
import threading
import time
import uuid
from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from collections import defaultdict, deque
from typing import Optional

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from logic import MAX_LEN, answer_for, validate_text

load_dotenv()
VALID_TOKENS = [t.strip() for t in os.getenv("API_TOKENS", "").split(",") if t.strip()]
ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(",")
    if o.strip()
]
SESSION_TTL = 3600       # секунд
MAX_FAILS = 15           # неудачных входов с одного IP ...
FAIL_WINDOW = 60         # ... за столько секунд, потом 429

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

app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"loc": [str(x) for x in e.get("loc", ())], "type": e.get("type", ""), "msg": e.get("msg", "")}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})

# Хранилище в памяти (для прототипа; при перезапуске сервера всё сбрасывается)
LOCK = threading.RLock()
SESSIONS: dict[str, dict] = {}      # session_id -> {token, expires, client_ids}
MESSAGES: dict[str, dict] = {}      # message_id -> {session_id, status, answer, ticket}
TICKETS: dict[str, dict] = {}       # ticket_id -> {session_id, message_id, status}
IDEMPOTENCY: dict[str, str] = {}    # idempotency_key -> ticket_id
FAILED: dict[str, deque] = defaultdict(deque)   # ip -> времена неудачных входов


def is_valid(token: str) -> bool:
    candidate = token.encode("utf-8")
    ok = False
    for t in VALID_TOKENS:
        ok |= secrets.compare_digest(candidate, t.encode("utf-8"))
    return ok


def bearer(authorization: str) -> str:
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def require_token(authorization: str = Header(default="")) -> str:
    token = bearer(authorization)
    if not token or not is_valid(token):
        raise HTTPException(status_code=401, detail="invalid_token")
    return token


def find_session(session_id: str) -> Optional[dict]:
    s = SESSIONS.get(session_id)
    if s is not None and s["expires"] < time.time():
        SESSIONS.pop(session_id, None)
        return None
    return s


def get_session(session_id: str, token: str) -> dict:
    s = find_session(session_id)
    if s is None:
        raise HTTPException(status_code=401, detail="unknown_session")
    if not secrets.compare_digest(s["token"].encode("utf-8"), token.encode("utf-8")):
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
    session_id = "sess-" + uuid.uuid4().hex[:16]
    with LOCK:
        SESSIONS[session_id] = {"token": token, "expires": time.time() + SESSION_TTL, "client_ids": {}}
    return {"session_id": session_id, "expires_in": SESSION_TTL}


@app.post("/api/v1/auth/logout")
def logout(body: LogoutRequest):
    with LOCK:
        if SESSIONS.pop(body.session_id, None) is None:
            raise HTTPException(status_code=401, detail="unknown_session")
    return {"status": "logged_out"}


def message_payload(message_id: str, msg: dict) -> dict:
    if msg["status"] == "answered":
        return {"message_id": message_id, "status": "answered", "answer": msg["answer"], "latency_ms": 2000}
    return {
        "message_id": message_id,
        "status": "too_complex",
        "error": "Не удалось однозначно понять вопрос",
        "actions": ["escalate"],
    }


@app.post("/api/v1/chat/messages")
def create_message(body: MessageRequest, token: str = Depends(require_token)):
    session = get_session(body.session_id, token)
    err = validate_text(body.text)
    if err:
        raise HTTPException(status_code=422, detail=err)

    with LOCK:
        # Повторная отправка с тем же client_message_id возвращает то же сообщение
        cid = body.client_message_id
        if cid and cid in session["client_ids"]:
            mid = session["client_ids"][cid]
            return message_payload(mid, MESSAGES[mid])
        answer = answer_for(body.text)
        mid = "msg-" + uuid.uuid4().hex[:16]
        MESSAGES[mid] = {
            "session_id": body.session_id,
            "status": "answered" if answer else "too_complex",
            "answer": answer or "",
            "ticket": None,
        }
        if cid:
            session["client_ids"][cid] = mid
        return message_payload(mid, MESSAGES[mid])


@app.post("/api/v1/chat/escalate")
def escalate(body: EscalateRequest, token: str = Depends(require_token)):
    get_session(body.session_id, token)
    with LOCK:   # блокировка нужна: 20 одновременных запросов не должны создать 20 заявок
        key = body.idempotency_key
        if key and key in IDEMPOTENCY:
            t = TICKETS[IDEMPOTENCY[key]]
            if t["session_id"] != body.session_id:
                raise HTTPException(status_code=409, detail="idempotency_conflict")
            return {"ticket_id": IDEMPOTENCY[key], "status": "queued"}
        msg = MESSAGES.get(body.message_id)
        if msg is None or msg["session_id"] != body.session_id:
            raise HTTPException(status_code=404, detail="message_not_found")
        if msg["ticket"]:
            return {"ticket_id": msg["ticket"], "status": "queued"}
        ticket_id = "ticket-" + uuid.uuid4().hex[:12]
        TICKETS[ticket_id] = {"session_id": body.session_id, "message_id": body.message_id, "status": "queued"}
        msg["ticket"] = ticket_id
        if key:
            IDEMPOTENCY[key] = ticket_id
        return {"ticket_id": ticket_id, "status": "queued"}


@app.get("/api/v1/chat/status")
def status(
    session_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    authorization: str = Header(default=""),
):
    # Токен в заголовке необязателен (session_id и так секретный), но если он есть, он должен подходить
    s = find_session(session_id) if session_id else None
    if s is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    token = bearer(authorization)
    if token and not secrets.compare_digest(s["token"].encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(status_code=403, detail="session_token_mismatch")

    def row(tid: str, t: dict) -> dict:
        return {"ticket_id": tid, "status": t["status"], "message_id": t["message_id"]}

    with LOCK:
        if ticket_id:
            t = TICKETS.get(ticket_id)
            if t is None or t["session_id"] != session_id:
                raise HTTPException(status_code=404, detail="ticket_not_found")
            return {"status": t["status"], "tickets": [row(ticket_id, t)]}
        tickets = [row(tid, t) for tid, t in TICKETS.items() if t["session_id"] == session_id]
        msgs = [
            {"message_id": mid, "status": m["status"], "ticket_id": m["ticket"]}
            for mid, m in MESSAGES.items()
            if m["session_id"] == session_id
        ]
        if tickets:
            value = "queued"
        elif msgs:
            value = msgs[-1]["status"]
        else:
            value = "new"
        return {"status": value, "tickets": tickets, "messages": msgs}
