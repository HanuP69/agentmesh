import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from shared.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


def test_opens_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=3, base_delay=0.01, cap_delay=0.05)

    def fail():
        raise ValueError("boom")

    for _ in range(3):
        with pytest.raises(ValueError):
            cb.call(fail)
    assert cb.state == CircuitState.OPEN


def test_half_open_then_close_on_success():
    cb = CircuitBreaker("test", failure_threshold=1, base_delay=0.01, cap_delay=0.02)
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError()))
    assert cb.state == CircuitState.OPEN
    time.sleep(0.05)
    result = cb.call(lambda: "ok")
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED


def test_rejects_calls_while_open():
    cb = CircuitBreaker("test", failure_threshold=1, base_delay=10, cap_delay=10)
    with pytest.raises(ValueError):
        cb.call(lambda: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: "should not run")
