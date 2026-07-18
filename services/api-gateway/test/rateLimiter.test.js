const test = require("node:test");
const assert = require("node:assert/strict");
const { TokenBucketRateLimiter } = require("../lib/rateLimiter");

// No Redis client passed in => exercises the in-memory fallback path directly.
// This is the exact code path this project's REALITY_CHECK.md says runs when
// Redis is unreachable, so it deserves its own coverage rather than only
// being tested implicitly via the Redis-backed Lua script.

test("allows requests up to capacity, then rejects", async () => {
  const rl = new TokenBucketRateLimiter(null, { capacity: 3, refillRate: 0 });
  assert.equal(await rl.allow("user-1"), true);
  assert.equal(await rl.allow("user-1"), true);
  assert.equal(await rl.allow("user-1"), true);
  assert.equal(await rl.allow("user-1"), false, "4th request within capacity window must be rejected");
});

test("different identifiers get fully independent buckets", async () => {
  const rl = new TokenBucketRateLimiter(null, { capacity: 1, refillRate: 0 });
  assert.equal(await rl.allow("user-a"), true);
  assert.equal(await rl.allow("user-a"), false);
  assert.equal(await rl.allow("user-b"), true, "user-b must not be throttled by user-a's usage");
});

test("tokens refill over time up to capacity, not unbounded", async () => {
  const rl = new TokenBucketRateLimiter(null, { capacity: 2, refillRate: 100 }); // 100 tokens/sec
  assert.equal(await rl.allow("user-1", 2), true); // drain to 0
  assert.equal(await rl.allow("user-1"), false);
  await new Promise((r) => setTimeout(r, 30)); // ~3 tokens worth of refill, capped at capacity=2
  assert.equal(await rl.allow("user-1", 2), true, "should have refilled back up to capacity within 30ms at 100/sec");
  assert.equal(await rl.allow("user-1"), false, "must not refill past capacity");
});

test("rejectionCounts tracks rejections per identifier, not globally", async () => {
  const rl = new TokenBucketRateLimiter(null, { capacity: 1, refillRate: 0 });
  await rl.allow("user-a");
  await rl.allow("user-a"); // rejected
  await rl.allow("user-a"); // rejected
  await rl.allow("user-b"); // allowed, no rejection
  const counts = rl.rejectionCounts();
  assert.equal(counts["user-a"], 2);
  assert.equal(counts["user-b"], undefined);
});

test("a request larger than capacity is always rejected, never partially allowed", async () => {
  const rl = new TokenBucketRateLimiter(null, { capacity: 5, refillRate: 0 });
  assert.equal(await rl.allow("user-1", 10), false);
});
