import sqlite3
import time

import pytest

from service.models import MessageStatus
from service.storage import Storage

@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.sqlite3"))
    s.create_session("S1", "hash", time.time() + 3600)
    return s

def test_second_insert_is_duplicate(storage):
    first = storage.insert_message("S1", "m1", "привет")
    second = storage.insert_message("S1", "m1", "привет")
    assert first is not None
    assert second is None

def test_finish_only_once(storage):
    mid = storage.insert_message("S1", "m1", "привет")
    assert storage.finish_message(mid, MessageStatus.ANSWERED, "ответ") is True
    assert storage.finish_message(mid, MessageStatus.TOO_COMPLEX) is False
    assert storage.get_message("S1", "m1")["status"] == "answered"


def test_survive_restart(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    a = Storage(path)
    a.create_session("S1", "h", time.time() + 3600)
    a.insert_message("S1", "m1", "привет")
    b = Storage(path) #restart: new object to the same file
    assert b.get_message("S1", "m1")["text"] == "привет"

def test_delete_session_cascades(storage):
    storage.insert_message("S1", "m1", "привет")
    with storage._connect() as conn:
        conn.execute("DELETE FROM sessions WHERE ID = 'S1'")
    assert storage.get_message("S1", "m1") is None

def test_unknown_session_rejected(storage):
    with pytest.raises(sqlite3.IntegrityError):
        storage.insert_message("NOPE", "m1", "привет")

def test_null_client_id_conflicts(storage):
    assert storage.insert_message("S1", None, "a") is not None
    assert storage.insert_message("S1", None, "a") is not None
def test_get_session_and_delete(storage):
    assert storage.get_session("S1")["token_hash"] == "hash"
    assert storage.get_session("NOPE") is None
    assert storage.delete_session("S1") is True
    assert storage.delete_session("S1") is False
    assert storage.get_session("S1") is None


def test_expired_session_is_gone_with_messages(storage):
    storage.create_session("OLD", "h", time.time() - 10)
    storage.insert_message("OLD", "m1", "привет")
    assert storage.get_session("OLD") is None
    assert storage.get_message("OLD", "m1") is None


def test_list_messages_in_order_with_ticket(storage):
    a = storage.insert_message("S1", "m1", "один")
    b = storage.insert_message("S1", "m2", "два")
    storage.create_ticket("S1", b, None)
    rows = storage.list_messages("S1")
    assert [r["id"] for r in rows] == [a, b]
    assert rows[0]["ticket_id"] is None
    assert rows[1]["ticket_id"] is not None


def test_ticket_is_idempotent(storage):
    m = storage.insert_message("S1", "m1", "вопрос")
    o1, t1 = storage.create_ticket("S1", m, "k1")
    o2, t2 = storage.create_ticket("S1", m, "k1")
    o3, t3 = storage.create_ticket("S1", m, None)
    o4, t4 = storage.create_ticket("S1", m, "другой-ключ")
    assert o1 == "created" and o2 == o3 == o4 == "existing"
    assert t1["id"] == t2["id"] == t3["id"] == t4["id"]
    assert len(storage.list_tickets("S1")) == 1


def test_ticket_key_of_other_session_conflicts(storage):
    storage.create_session("S2", "h2", time.time() + 3600)
    m1 = storage.insert_message("S1", "m1", "вопрос")
    m2 = storage.insert_message("S2", "m1", "вопрос")
    storage.create_ticket("S1", m1, "k1")
    outcome, ticket = storage.create_ticket("S2", m2, "k1")
    assert outcome == "idempotency_conflict" and ticket is None


def test_ticket_for_foreign_or_missing_message(storage):
    storage.create_session("S2", "h2", time.time() + 3600)
    m2 = storage.insert_message("S2", "m1", "вопрос")
    assert storage.create_ticket("S1", m2, None)[0] == "message_not_found"
    assert storage.create_ticket("S1", 9999, None)[0] == "message_not_found"


def test_get_ticket(storage):
    m = storage.insert_message("S1", "m1", "вопрос")
    _, t = storage.create_ticket("S1", m, None)
    assert storage.get_ticket(t["id"])["status"] == "queued"
    assert storage.get_ticket(0) is None


def test_delete_session_removes_tickets_too(storage):
    m = storage.insert_message("S1", "m1", "вопрос")
    _, t = storage.create_ticket("S1", m, "k1")
    storage.delete_session("S1")
    assert storage.get_ticket(t["id"]) is None


def test_two_tickets_for_different_messages(storage):
    a = storage.insert_message("S1", "m1", "один")
    b = storage.insert_message("S1", "m2", "два")
    _, t1 = storage.create_ticket("S1", a, None)
    _, t2 = storage.create_ticket("S1", b, None)
    assert t1["id"] != t2["id"]