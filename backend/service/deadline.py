import time

class Deadline:
    def __init__ (self, seconds:float):
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - time.monotonic())
    def expired(self) -> bool:
        return self.remaining() <= 0