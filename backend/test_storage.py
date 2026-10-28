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
