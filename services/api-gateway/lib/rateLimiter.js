const TOKEN_BUCKET_SCRIPT = `
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local tokens = tonumber(redis.call("HGET", key, "tokens"))
local ts = tonumber(redis.call("HGET", key, "ts"))
if tokens == nil then tokens = capacity end
if ts == nil then ts = now end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill_rate)
local allowed = 0
if tokens >= requested then
  allowed = 1
  tokens = tokens - requested
end
redis.call("HSET", key, "tokens", tokens, "ts", now)
redis.call("EXPIRE", key, 3600)
return allowed
`;

class TokenBucketRateLimiter {
  constructor(redisClient, config) {
    this.redis = redisClient;
    this.config = config;
    this.localState = new Map();
    this.rejections = new Map();
  }

  refill(tokens, ts, now) {
    const elapsed = Math.max(0, now - ts);
    return Math.min(this.config.capacity, tokens + elapsed * this.config.refillRate);
  }

  async allowRedis(identifier, tokensRequested) {
    const key = `ratelimit:edge:${identifier}`;
    const now = Date.now() / 1000;
    const result = await this.redis.eval(
      TOKEN_BUCKET_SCRIPT,
      1,
      key,
      this.config.capacity,
      this.config.refillRate,
      now,
      tokensRequested
    );
    return result === 1;
  }

  async allow(identifier, tokensRequested = 1) {
    let allowed;
    if (this.redis) {
      allowed = await this.allowRedis(identifier, tokensRequested);
    } else {
      const now = Date.now() / 1000;
      const state = this.localState.get(identifier) || { tokens: this.config.capacity, ts: now };
      state.tokens = this.refill(state.tokens, state.ts, now);
      state.ts = now;
      if (state.tokens >= tokensRequested) {
        state.tokens -= tokensRequested;
        allowed = true;
      } else {
        allowed = false;
      }
      this.localState.set(identifier, state);
    }
    if (!allowed) {
      this.rejections.set(identifier, (this.rejections.get(identifier) || 0) + 1);
    }
    return allowed;
  }

  rejectionCounts() {
    return Object.fromEntries(this.rejections);
  }
}

module.exports = { TokenBucketRateLimiter };
