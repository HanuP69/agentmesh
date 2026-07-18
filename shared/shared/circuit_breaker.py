"""3-state circuit breaker (CLOSED -> OPEN -> HALF_OPEN) with exponential
backoff + jitter. Wraps agent->agent and agent->LLM/vision calls."""
import random
import time
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        base_delay: float = 0.5,
        cap_delay: float = 30.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.base_delay = base_delay
        self.cap_delay = cap_delay
        self.half_open_max_calls = half_open_max_calls

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.attempt = 0
        self._opened_at = 0.0
        self._half_open_calls = 0

    def _backoff_delay(self) -> float:
        delay = min(self.cap_delay, self.base_delay * (2 ** self.attempt))
        return delay * (0.5 + random.random() * 0.5)  # jitter

    def _can_attempt(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            required = getattr(self, "_required_delay", None)
            if required is None:
                required = self._backoff_delay()
                self._required_delay = required
            if time.time() - self._opened_at >= required:
                self.state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False

    def _on_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.attempt = 0
        if hasattr(self, "_required_delay"):
            delattr(self, "_required_delay")

    def _on_failure(self) -> None:
        self.failure_count += 1
        self.attempt += 1
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self._opened_at = time.time()
            self._required_delay = self._backoff_delay()
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self._opened_at = time.time()
            self._required_delay = self._backoff_delay()

    def call(self, fn: Callable[[], T]) -> T:
        if not self._can_attempt():
            raise CircuitOpenError(f"circuit '{self.name}' is OPEN")
        if self.state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1
        try:
            result = fn()
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "attempt": self.attempt,
        }
