import os
import tempfile

os.environ["API_TOKENS"] = "test-token,other-token"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_api.sqlite3")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from main import app  # noqa: E402
from service.public_ids import parse_id  # noqa: E402

import pytest  # noqa: E402

c = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    """Каждый тест начинается с пустого состояния сервера (в том числе счётчика неудачных входов)."""
    with main.storage._connect() as conn:
        conn.execute("DELETE FROM sessions")   # каскадом удаляются сообщения и заявки
    with main.LOCK:
        main.FAILED.clear()


def count(table, where="", args=()):
    with main.storage._connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0]


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


def escalate(sid, mid, key):
    return c.post("/api/v1/chat/escalate", headers=H, json={"session_id": sid, "message_id": mid, "idempotency_key": key})


def test_same_key_for_different_messages_is_409():
    sid = new_session()
    m1 = send(sid, "Расскажи анекдот").json()["message_id"]
    m2 = send(sid, "Почему небо синее?").json()["message_id"]
    assert escalate(sid, m1, "k").status_code == 200
    assert escalate(sid, m2, "k").status_code == 409


def test_same_key_same_message_returns_same_ticket():
    sid = new_session()
    m = send(sid, "Расскажи анекдот").json()["message_id"]
    a, b = escalate(sid, m, "k"), escalate(sid, m, "k")
    assert a.status_code == b.status_code == 200
    assert a.json()["ticket_id"] == b.json()["ticket_id"]


def test_key_of_other_session_is_409():
    s1, s2 = new_session(), new_session()
    m1 = send(s1, "Расскажи анекдот").json()["message_id"]
    m2 = send(s2, "Расскажи анекдот").json()["message_id"]
    assert escalate(s1, m1, "k").status_code == 200
    assert escalate(s2, m2, "k").status_code == 409


def test_question_text_is_saved():
    sid = new_session()
    mid = send(sid, "Расскажи анекдот").json()["message_id"]
    row = main.storage.get_message_by_id(parse_id("msg", mid))
    assert row["text"] == "Расскажи анекдот"


def test_latency_is_measured_and_stable_on_replay():
    sid = new_session()
    a = send(sid, "Какой режим работы?", client_message_id="c1").json()
    b = send(sid, "Какой режим работы?", client_message_id="c1").json()
    assert isinstance(a["latency_ms"], int) and a["latency_ms"] != 2000
    assert a["latency_ms"] == b["latency_ms"]


def test_logout_purges_session_data():
    sid = new_session()
    mid = send(sid, "Расскажи анекдот").json()["message_id"]
    escalate(sid, mid, "k")
    assert c.post("/api/v1/auth/logout", json={"session_id": sid}).status_code == 200
    assert count("sessions") == 0 and count("messages") == 0 and count("tickets") == 0


def test_expired_session_is_purged():
    sid = new_session()
    mid = send(sid, "Расскажи анекдот").json()["message_id"]
    escalate(sid, mid, "k")
    with main.storage._connect() as conn:
        conn.execute("UPDATE sessions SET expires_at = 0 WHERE id = ?", (sid,))
    assert send(sid, "цена").status_code == 401
    assert count("messages") == 0 and count("tickets") == 0


def test_purge_keeps_other_sessions():
    s1, s2 = new_session(), new_session()
    m2 = send(s2, "Расскажи анекдот").json()["message_id"]
    escalate(s2, m2, "k2")
    main.storage.delete_session(s1)
    assert count("messages") == 1
    assert count("tickets", "WHERE idempotency_key = ?", ("k2",)) == 1
    assert main.storage.get_session(s2) is not None


def test_login_purges_expired_sessions():
    old = new_session()
    with main.storage._connect() as conn:
        conn.execute("UPDATE sessions SET expires_at = 0 WHERE id = ?", (old,))
    new_session()
    assert main.storage.get_session(old) is None
    assert count("sessions") == 1
