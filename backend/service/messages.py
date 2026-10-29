import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from .deadline import Deadline
from .errors import CoreTimeout
from .models import FailReason, MessageResult, MessageStatus

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.05


class MessageService:
    def __init__(self, storage, core, settings):
        self._storage = storage
        self._core = core
        self._settings = settings
        self._pool = ThreadPoolExecutor(max_workers=4)

    def close(self) -> None:
        self._pool.shutdown(wait = False)

    def submit(self, session_id: str, text: str,
               client_message_id: str | None = None) -> MessageResult:
        deadline = Deadline(self._settings.request_budget_s)
        message_id = self._storage.insert_message(session_id, client_message_id, text)
        if message_id is not None:
            return self._run(message_id, text, deadline)
        return self._handle_duplicate(session_id, client_message_id, deadline)
    def _handle_duplicate(self, session_id, client_message_id, deadline) -> MessageResult:
        while True:
            row = self._storage.get_message(session_id, client_message_id)
            status = row["status"]

            if status in (MessageStatus.ANSWERED.value, MessageStatus.TOO_COMPLEX.value):
                return self._row_to_result(row)

            if status == MessageStatus.FAILED.value:
                if self._storage.retry_failed(row["id"]):
                    return self._run(row["id"], row["text"], deadline)
                continue

            if deadline.expired():
                return MessageResult(MessageStatus.FAILED, "", FailReason.TIMEOUT, None, row["id"])
            time.sleep(POLL_INTERVAL_S)
    def _run(self, message_id: int, text: str, deadline: Deadline) -> MessageResult:
        started = time.monotonic()
        future = self._pool.submit(self._core.answer, text, deadline)
        try: 
            core_result = future.result(timeout=deadline.remaining())
        except (FutureTimeout, CoreTimeout):
            return self._fail(message_id, FailReason.TIMEOUT, started)
        except Exception:
            log.exception("ядро упало на сообщении %s", message_id)
            return self._fail(message_id, FailReason.CORE_ERROR, started)

        latency = self._elapsed_ms(started)
        self._storage.finish_message(
            message_id, core_result.status, core_result.answer, None, latency
        )
        return MessageResult(core_result.status, core_result.answer, None, latency, message_id)

    def _fail(self, message_id: int, reason: FailReason, started: float) -> MessageResult:
        latency = self._elapsed_ms(started)
        self._storage.finish_message(
            message_id, MessageStatus.FAILED, "", reason, latency
        )
        return MessageResult(MessageStatus.FAILED, "", reason, latency, message_id)

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    @staticmethod
    def _row_to_result(row: dict) -> MessageResult:
        reason = row["fail_reason"]
        return MessageResult(
            MessageStatus(row["status"]),
            row["answer"],
            FailReason(reason) if reason else None,
            row["latency_ms"],
            row["id"],
        )