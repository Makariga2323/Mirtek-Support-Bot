import threading
import time
from types import SimpleNamespace

import pytest

from service.messages import MessageService
from service.models import CoreResult, FailReason, MessageStatus
from service.storage import Storage


class FakeCore:
    """Ядро-подделка: считает вызовы и делает то, что ему скажут."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    def answer(self, text, deadline):
        self.calls += 1
        return self.behavior(self.calls)


def ok(_):
    return CoreResult(MessageStatus.ANSWERED, "ответ")


@pytest.fixture
def make_service(tmp_path):
    def make(core, budget=2.0):
        storage = Storage(str(tmp_path / "t.sqlite3"))
        storage.create_session("S1", "h", time.time() + 3600)
        service = MessageService(storage, core, SimpleNamespace(request_budget_s=budget))
        return service, storage
    return make


def test_answer_is_saved(make_service):
    core = FakeCore(ok)
    service, storage = make_service(core)
    r = service.submit("S1", "вопрос", "m1")
    assert r.status == MessageStatus.ANSWERED and r.answer == "ответ"
    row = storage.get_message("S1", "m1")
    assert row["status"] == "answered" and row["latency_ms"] is not None


def test_repeat_does_not_run_core_again(make_service):
    core = FakeCore(ok)
    service, _ = make_service(core)
    first = service.submit("S1", "вопрос", "m1")
    second = service.submit("S1", "вопрос", "m1")
    assert core.calls == 1
    assert second == first


def test_too_complex_is_saved(make_service):
    core = FakeCore(lambda n: CoreResult(MessageStatus.TOO_COMPLEX))
    service, storage = make_service(core)
    r = service.submit("S1", "вопрос", "m1")
    assert r.status == MessageStatus.TOO_COMPLEX
    assert storage.get_message("S1", "m1")["status"] == "too_complex"


def test_core_crash_is_predictable(make_service):
    def boom(n):
        raise RuntimeError("сломалось")
    service, storage = make_service(FakeCore(boom))
    r = service.submit("S1", "вопрос", "m1")
    assert r.status == MessageStatus.FAILED and r.fail_reason == FailReason.CORE_ERROR
    assert storage.get_message("S1", "m1")["fail_reason"] == "core_error"


def test_timeout_is_predictable(make_service):
    def slow(n):
        time.sleep(1.0)
        return CoreResult(MessageStatus.ANSWERED, "поздно")
    service, _ = make_service(FakeCore(slow), budget=0.2)
    started = time.monotonic()
    r = service.submit("S1", "вопрос", "m1")
    assert time.monotonic() - started < 0.6
    assert r.status == MessageStatus.FAILED and r.fail_reason == FailReason.TIMEOUT


def test_late_answer_does_not_overwrite(make_service):
    def slow(n):
        time.sleep(0.4)
        return CoreResult(MessageStatus.ANSWERED, "поздно")
    service, storage = make_service(FakeCore(slow), budget=0.1)
    service.submit("S1", "вопрос", "m1")
    time.sleep(0.7)
    assert storage.get_message("S1", "m1")["status"] == "failed"


def test_failed_can_be_retried(make_service):
    def flaky(n):
        if n == 1:
            raise RuntimeError("сбой")
        return CoreResult(MessageStatus.ANSWERED, "ответ")
    core = FakeCore(flaky)
    service, storage = make_service(core)
    assert service.submit("S1", "вопрос", "m1").status == MessageStatus.FAILED
    assert service.submit("S1", "вопрос", "m1").status == MessageStatus.ANSWERED
    assert core.calls == 2
    assert storage.get_message("S1", "m1")["attempts"] == 2


def test_repeat_during_processing_waits(make_service):
    def slow(n):
        time.sleep(0.3)
        return CoreResult(MessageStatus.ANSWERED, "ответ")
    core = FakeCore(slow)
    service, _ = make_service(core, budget=3.0)
    results = []
    t = threading.Thread(target=lambda: results.append(service.submit("S1", "в", "m1")))
    t.start()
    time.sleep(0.05)
    second = service.submit("S1", "в", "m1")
    t.join()
    assert core.calls == 1
    assert second.status == MessageStatus.ANSWERED and second.answer == "ответ"


def test_without_client_id_always_runs(make_service):
    core = FakeCore(ok)
    service, _ = make_service(core)
    service.submit("S1", "в")
    service.submit("S1", "в")
    assert core.calls == 2