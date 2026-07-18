const test = require("node:test");
const assert = require("node:assert/strict");
const { CircuitBreaker, CircuitOpenError, CLOSED, OPEN, HALF_OPEN } = require("../lib/circuitBreaker");

function failing() {
  return Promise.reject(new Error("boom"));
}
function succeeding() {
  return Promise.resolve("ok");
}

test("starts CLOSED and allows calls through", async () => {
  const cb = new CircuitBreaker("agent", { failureThreshold: 3 });
  assert.equal(cb.state, CLOSED);
  assert.equal(await cb.call(succeeding), "ok");
  assert.equal(cb.state, CLOSED);
});

test("trips to OPEN after failureThreshold consecutive failures", async () => {
  const cb = new CircuitBreaker("agent", { failureThreshold: 3, baseDelay: 5, capDelay: 30 });
  for (let i = 0; i < 3; i++) {
    await assert.rejects(() => cb.call(failing));
  }
  assert.equal(cb.state, OPEN);
});

test("fails fast with CircuitOpenError while OPEN, without invoking fn", async () => {
  const cb = new CircuitBreaker("agent", { failureThreshold: 1, baseDelay: 30, capDelay: 30 });
  await assert.rejects(() => cb.call(failing));
  assert.equal(cb.state, OPEN);

  let called = false;
  await assert.rejects(
    () => cb.call(() => { called = true; return Promise.resolve("ok"); }),
    CircuitOpenError
  );
  assert.equal(called, false, "fn must not run while breaker is open — that's the whole point of fail-fast");
});

test("a single failure below threshold does not trip the breaker", async () => {
  const cb = new CircuitBreaker("agent", { failureThreshold: 5 });
  await assert.rejects(() => cb.call(failing));
  assert.equal(cb.state, CLOSED);
  assert.equal(cb.failureCount, 1);
});

test("success resets failureCount back to 0 (not a leaky counter)", async () => {
  const cb = new CircuitBreaker("agent", { failureThreshold: 5 });
  await assert.rejects(() => cb.call(failing));
  await assert.rejects(() => cb.call(failing));
  assert.equal(cb.failureCount, 2);
  await cb.call(succeeding);
  assert.equal(cb.failureCount, 0);
  assert.equal(cb.state, CLOSED);
});

test("transitions OPEN -> HALF_OPEN after backoff elapses, then CLOSED on success", async () => {
  const cb = new CircuitBreaker("agent", { failureThreshold: 1, baseDelay: 0.05, capDelay: 0.05 });
  await assert.rejects(() => cb.call(failing));
  assert.equal(cb.state, OPEN);

  // wait past the (jittered, max 0.05s) backoff window
  await new Promise((r) => setTimeout(r, 120));

  const result = await cb.call(succeeding);
  assert.equal(result, "ok");
  assert.equal(cb.state, CLOSED, "a successful half-open trial call must close the breaker");
});

test("a failed HALF_OPEN trial call re-opens the breaker", async () => {
  const cb = new CircuitBreaker("agent", { failureThreshold: 1, baseDelay: 0.05, capDelay: 0.05 });
  await assert.rejects(() => cb.call(failing));
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(cb._canAttempt(), true);
  cb.state = HALF_OPEN;
  cb._halfOpenCalls = 0;
  await assert.rejects(() => cb.call(failing));
  assert.equal(cb.state, OPEN, "failing the half-open trial must re-open, not silently stay closed");
});

test("breakers for different agents are fully independent", async () => {
  const text = new CircuitBreaker("text", { failureThreshold: 1 });
  const table = new CircuitBreaker("table", { failureThreshold: 1 });
  await assert.rejects(() => text.call(failing));
  assert.equal(text.state, OPEN);
  assert.equal(table.state, CLOSED, "tripping one agent's breaker must not affect another agent's breaker");
});
