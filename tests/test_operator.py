import os
import tempfile

os.environ.setdefault("API_TOKENS", "test-token,other-token")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test_operator.sqlite3"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

c = TestClient(main.app)
OP = {"Authorization": "Bearer op-secret"}


@pytest.fixture(autouse=True)
def operator_env(monkeypatch):
    monkeypatch.setattr(main, "OPERATOR_TOKENS", ["op-secret"])
    with main.storage._connect() as conn:
        conn.execute("DELETE FROM sessions")


def start_session():
    token = main.VALID_TOKENS[0]
    sid = c.post("/api/v1/auth", json={"token": token}).json()["session_id"]
    return sid, {"Authorization": f"Bearer {token}"}


def ask(sid, headers, text, cmid=None):
    body = {"session_id": sid, "text": text}
    if cmid:
        body["client_message_id"] = cmid
    return c.post("/api/v1/chat/messages", json=body, headers=headers).json()


def test_operator_requires_operator_token():
    assert c.get("/api/v1/operator/tickets").status_code == 401
    # токен клиента не открывает очередь оператора
    client_headers = {"Authorization": f"Bearer {main.VALID_TOKENS[0]}"}
    assert c.get("/api/v1/operator/tickets", headers=client_headers).status_code == 401
    assert c.get("/api/v1/operator/tickets", headers=OP).status_code == 200


def test_answered_also_offers_escalation():
    sid, h = start_session()
    r = ask(sid, h, "Во сколько вы сегодня закрываетесь? график работы")
    assert r["status"] == "answered"
    assert "escalate" in r["actions"]


def test_full_operator_flow_no_duplicates():
    sid, h = start_session()
    r = ask(sid, h, "Сделайте мне хорошо", cmid="c-1")
    assert r["status"] == "too_complex" and "escalate" in r["actions"]

    body = {"session_id": sid, "message_id": r["message_id"], "idempotency_key": "idem-1"}
    t1 = c.post("/api/v1/chat/escalate", json=body, headers=h).json()
    t2 = c.post("/api/v1/chat/escalate", json=body, headers=h).json()   # повторное нажатие
    assert t1["ticket_id"] == t2["ticket_id"]

    tickets = c.get("/api/v1/operator/tickets", headers=OP).json()["tickets"]
    assert len(tickets) == 1
    assert tickets[0]["text"] == "Сделайте мне хорошо"      # оператор видит вопрос
    assert tickets[0]["status"] == "queued"

    patch = c.patch(f"/api/v1/operator/tickets/{t1['ticket_id']}", json={"status": "in_progress"}, headers=OP)
    assert patch.status_code == 200
    seen_by_client = c.get(f"/api/v1/chat/status?session_id={sid}&ticket_id={t1['ticket_id']}", headers=h).json()
    assert seen_by_client["status"] == "in_progress"
    only_closed = c.get("/api/v1/operator/tickets?status=closed", headers=OP).json()["tickets"]
    assert only_closed == []


def test_bad_status_and_unknown_ticket():
    sid, h = start_session()
    r = ask(sid, h, "Сделайте мне хорошо")
    t = c.post("/api/v1/chat/escalate", json={"session_id": sid, "message_id": r["message_id"]}, headers=h).json()
    assert c.patch(f"/api/v1/operator/tickets/{t['ticket_id']}", json={"status": "bogus"}, headers=OP).status_code == 422
    assert c.patch("/api/v1/operator/tickets/ticket-99999", json={"status": "closed"}, headers=OP).status_code == 404
    assert c.get("/api/v1/operator/tickets?status=bogus", headers=OP).status_code == 422
