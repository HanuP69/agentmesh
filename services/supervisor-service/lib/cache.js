const crypto = require("crypto");

class InProcessLRU {
  constructor(capacity = 512) {
    this.capacity = capacity;
    this.map = new Map();
  }

  get(key) {
    if (!this.map.has(key)) return null;
    const value = this.map.get(key);
    this.map.delete(key);
    this.map.set(key, value);
    return value;
  }

  put(key, value) {
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, value);
    if (this.map.size > this.capacity) {
      const oldestKey = this.map.keys().next().value;
      this.map.delete(oldestKey);
    }
  }

  size() {
    return this.map.size;
  }
}

function cacheKey(query, modality, topK) {
  const raw = topK != null ? `${query}::${topK}` : query;
  const hash = crypto.createHash("sha256").update(raw, "utf8").digest("hex").slice(0, 24);
  return `cache:${modality}:${hash}`;
}

class ModalityAwareCache {
  constructor(redisClient = null, l1Capacity = 512) {
    this.redis = redisClient;
    this.l1 = new InProcessLRU(l1Capacity);
    this.hits = { response: 0 };
    this.misses = { response: 0 };
  }

  async get(query, modality, topK) {
    const key = cacheKey(query, modality, topK);
    let val = this.l1.get(key);
    if (val === null && this.redis) {
      const raw = await this.redis.get(key);
      if (raw != null) {
        val = JSON.parse(raw);
        this.l1.put(key, val);
      }
    }
    if (modality in this.hits) {
      if (val != null) this.hits[modality]++;
      else this.misses[modality]++;
    }
    if (val == null) return null;
    return JSON.parse(JSON.stringify(val));
  }

  async put(query, modality, value, topK, ttl = 3600) {
    const key = cacheKey(query, modality, topK);
    this.l1.put(key, value);
    if (this.redis) await this.redis.set(key, JSON.stringify(value), "EX", ttl);
  }

  hitRatio(modality) {
    const total = this.hits[modality] + this.misses[modality];
    return total ? this.hits[modality] / total : 0.0;
  }

  stats() {
    const out = {};
    for (const m of Object.keys(this.hits)) {
      out[m] = { hits: this.hits[m], misses: this.misses[m], hit_ratio: this.hitRatio(m) };
    }
    return out;
  }
}

module.exports = { ModalityAwareCache, InProcessLRU, cacheKey };
