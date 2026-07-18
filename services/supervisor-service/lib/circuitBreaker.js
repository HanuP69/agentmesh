// 3-state circuit breaker (CLOSED -> OPEN -> HALF_OPEN) with exponential
// backoff + jitter. Same semantics as shared/shared/circuit_breaker.py,
// ported so the Node.js supervisor-service can wrap its own agent calls
// instead of only existing in the Python monolith/synthesizer path.

const CLOSED = "CLOSED";
const OPEN = "OPEN";
const HALF_OPEN = "HALF_OPEN";

class CircuitOpenError extends Error {
  constructor(name) {
    super(`circuit '${name}' is OPEN`);
    this.name = "CircuitOpenError";
  }
}

class CircuitBreaker {
  constructor(name, { failureThreshold = 5, baseDelay = 0.5, capDelay = 30.0, halfOpenMaxCalls = 1 } = {}) {
    this.name = name;
    this.failureThreshold = failureThreshold;
    this.baseDelay = baseDelay;
    this.capDelay = capDelay;
    this.halfOpenMaxCalls = halfOpenMaxCalls;

    this.state = CLOSED;
    this.failureCount = 0;
    this.attempt = 0;
    this._openedAt = 0;
    this._halfOpenCalls = 0;
    this._requiredDelay = null;
  }

  _backoffDelay() {
    const delay = Math.min(this.capDelay, this.baseDelay * 2 ** this.attempt);
    return delay * (0.5 + Math.random() * 0.5); // jitter
  }

  _canAttempt() {
    const now = Date.now() / 1000;
    if (this.state === CLOSED) return true;
    if (this.state === OPEN) {
      if (this._requiredDelay == null) this._requiredDelay = this._backoffDelay();
      if (now - this._openedAt >= this._requiredDelay) {
        this.state = HALF_OPEN;
        this._halfOpenCalls = 0;
        return true;
      }
      return false;
    }
    if (this.state === HALF_OPEN) {
      return this._halfOpenCalls < this.halfOpenMaxCalls;
    }
    return false;
  }

  _onSuccess() {
    this.state = CLOSED;
    this.failureCount = 0;
    this.attempt = 0;
    this._requiredDelay = null;
  }

  _onFailure() {
    this.failureCount += 1;
    this.attempt += 1;
    const now = Date.now() / 1000;
    if (this.state === HALF_OPEN) {
      this.state = OPEN;
      this._openedAt = now;
      this._requiredDelay = this._backoffDelay();
    } else if (this.failureCount >= this.failureThreshold) {
      this.state = OPEN;
      this._openedAt = now;
      this._requiredDelay = this._backoffDelay();
    }
  }

  async call(fn) {
    if (!this._canAttempt()) throw new CircuitOpenError(this.name);
    if (this.state === HALF_OPEN) this._halfOpenCalls += 1;
    try {
      const result = await fn();
      this._onSuccess();
      return result;
    } catch (e) {
      this._onFailure();
      throw e;
    }
  }

  snapshot() {
    return { name: this.name, state: this.state, failure_count: this.failureCount, attempt: this.attempt };
  }
}

module.exports = { CircuitBreaker, CircuitOpenError, CLOSED, OPEN, HALF_OPEN };
