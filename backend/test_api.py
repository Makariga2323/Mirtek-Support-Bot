import os

os.environ["API_TOKENS"] = "test-token,other-token"

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

c = TestClient(app)
H = {"Authorization": "Bearer test-token"}


def new_session(token="test-token"):
    r = c.post("/api/v1/auth", json={"token": token})
    assert r.status_code == 200
    return r.json()["session_id"]


def send(sid, text, **extra):
    return c.post("/api/v1/chat/messages", headers=H, json={"session_id": sid, "text": text, **extra})


def test_health():
    assert c.get("/health").status_code == 200


def test_wrong_token_is_401():
    assert c.post("/api/v1/auth", json={"token": "bad"}).status_code == 401


def test_login_via_bearer_header():
    assert c.post("/api/v1/auth", headers=H, json={}).status_code == 200


def test_messages_require_bearer():
    r = c.post("/api/v1/chat/messages", json={"session_id": "x", "text": "цена"})
    assert r.status_code == 401


def test_unknown_session_is_401():
    assert send("nope", "цена").status_code == 401


def test_session_of_other_token_is_403():
    assert send(new_session("other-token"), "цена").status_code == 403


def test_bad_text_is_422():
    sid = new_session()
    assert send(sid, "").status_code == 422
    assert send(sid, "   ").status_code == 422
    assert send(sid, "привет\x00мир").status_code == 422
    assert send(sid, 12345).status_code == 422


def test_answered_and_too_complex():
    sid = new_session()
    assert send(sid, "Какой режим работы?").json()["status"] == "answered"
    assert send(sid, "Расскажи анекдот").json()["status"] == "too_complex"


def test_same_client_message_id_gives_one_message():
    sid = new_session()
    a = send(sid, "Какой режим работы?", client_message_id="c1").json()
    b = send(sid, "Какой режим работы?", client_message_id="c1").json()
    assert a["message_id"] == b["message_id"]
    assert len(c.get("/api/v1/chat/status", params={"session_id": sid}).json()["messages"]) == 1


def test_escalate_is_idempotent_and_listed_in_status():
    sid = new_session()
    m = send(sid, "Расскажи анекдот").json()
    body = {"session_id": sid, "message_id": m["message_id"], "idempotency_key": "k1"}
    t1 = c.post("/api/v1/chat/escalate", headers=H, json=body).json()
    t2 = c.post("/api/v1/chat/escalate", headers=H, json=body).json()
    assert t1["ticket_id"] == t2["ticket_id"]
    st = c.get("/api/v1/chat/status", params={"session_id": sid}).json()
    assert st["status"] == "queued" and len(st["tickets"]) == 1


def test_status_unknown_ticket_is_404():
    r = c.get("/api/v1/chat/status", params={"session_id": new_session(), "ticket_id": "ticket-000000000000"})
    assert r.status_code == 404


def test_bruteforce_gets_429():
    codes = {c.post("/api/v1/auth", json={"token": f"x{i}"}).status_code for i in range(20)}
    assert 429 in codes
