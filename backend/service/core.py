from typing import Protocol

from logic import answer_for
from .deadline import Deadline
from .models import CoreResult, MessageStatus
from .settings import Settings

class Core(Protocol):
    def answer(self, text: str, deadline: Deadline) -> CoreResult: ...


class StubCore:
    def answer(self, text: str, deadline: Deadline) -> CoreResult:
        reply = answer_for(text)
        if reply is None:
            return CoreResult(MessageStatus.TOO_COMPLEX)
        return CoreResult(MessageStatus.ANSWERED, reply)


def make_core(settings: Settings) -> Core:
    if settings.core_mode == "stub":
        return StubCore()
    if settings.core_mode == "llm":
        from .real_core import make_llm_core   # ленивый импорт: stub-режиму модель не нужна
        return make_llm_core(settings)
    if settings.core_mode == "rules":
        from .rules_core import make_rules_core   # без модели: ответы по данным
        return make_rules_core(settings)
    raise ValueError(f"неизвестный core_mode: {settings.core_mode!r}")
