const express = require("express");
const axios = require("axios");
const http = require("http");
const https = require("https");
const Redis = require("ioredis");

const httpAgent = new http.Agent({ keepAlive: true, keepAliveMsecs: 60000, maxSockets: 1000 });
const httpsAgent = new https.Agent({ keepAlive: true, keepAliveMsecs: 60000, maxSockets: 1000, rejectUnauthorized: false });
axios.defaults.httpAgent = httpAgent;
axios.defaults.httpsAgent = httpsAgent;

const config = require("./lib/config");
const { crossModalRerank } = require("./lib/fusion");
const { ModalityAwareCache } = require("./lib/cache");
const { CircuitBreaker, CircuitOpenError } = require("./lib/circuitBreaker");

const app = express();
app.use(express.json());

let redisClient = null;
if (config.USE_REDIS) {
  redisClient = new Redis(config.REDIS_URL, { lazyConnect: true, maxRetriesPerRequest: 1 });
  redisClient.on("error", () => {});
  redisClient.connect().catch(() => {
    redisClient = null;
  });
}

const cache = new ModalityAwareCache(redisClient);

const INTERNAL_BASE = process.env.INTERNAL_BASE_URL || "http://nginx-internal:8080";
const AGENT_URLS = {
  text: process.env.TEXT_AGENT_URL || `${INTERNAL_BASE}/text-agent`,
  table: process.env.TABLE_AGENT_URL || `${INTERNAL_BASE}/table-agent`,
  image: process.env.IMAGE_AGENT_URL || `${INTERNAL_BASE}/image-agent`,
};
const SYNTHESIZER_URL = process.env.SYNTHESIZER_URL || `${INTERNAL_BASE}/synthesizer`;
const CHAT_URL = process.env.CHAT_URL || `${INTERNAL_BASE}/chat`;

// Real per-agent circuit breakers (was previously just a bare try/catch with
// no tripping/backoff at all in this service — see lib/circuitBreaker.js).
const breakers = Object.fromEntries(
  Object.keys(AGENT_URLS).map((m) => [m, new CircuitBreaker(m, { failureThreshold: 5, baseDelay: 0.5, capDelay: 30 })])
);

// Real in-flight request counter — was previously a hardcoded `queue_depth: 0`
// literal everywhere. This tracks actual concurrent /query handling.
let inFlightQueries = 0;

// Real shard map, populated by polling each agent's /health (which now
// reports its live hash-ring node list). Was previously absent entirely;
// the dashboard's shard_map field was undefined against this service.
const shardMap = { text: [], table: [], image: [] };

async function refreshShardMap() {
  await Promise.all(
    Object.entries(AGENT_URLS).map(async ([modality, url]) => {
      try {
        const r = await axios.get(`${url}/health`, { timeout: 3000 });
        if (Array.isArray(r.data?.shard_nodes)) shardMap[modality] = r.data.shard_nodes;
      } catch {
        // leave last-known shard map in place if an agent is briefly unreachable
      }
    })
  );
}
refreshShardMap();
setInterval(refreshShardMap, 15000);

const TABLE_HINTS = /\b(table|row|column|compare|numbers|stat|metric)\b/i;
const IMAGE_HINTS = /\b(diagram|screenshot|image|picture|chart|figure)\b/i;
const HYDE_INTENT = /\bwhy\b|\bexplain\b|\breason\b|\bcause\b|\beffect\b|\bimpact\b|\brelationship\b|\bhow does\b/i;
const USE_HYDE = (process.env.USE_HYDE || "false").toLowerCase() === "true";

async function hydeExpand(query, provider) {
  if (!USE_HYDE || !HYDE_INTENT.test(query)) return null;
  const prompt =
    "Write a short factual passage (2-3 sentences) that would directly answer " +
    "the following question. Write it as if extracted from a document. " +
    "Do not say 'the answer is' — just write the passage.\n\n" +
    `Question: ${query}\n\nPassage:`;
  try {
    const resp = await axios.post(`${SYNTHESIZER_URL}/chat`, { agent_type: "synthesizer", prompt, provider }, { timeout: 30000 });
    const hyp = resp.data.response;
    return hyp ? `${query} ${hyp}` : null;
  } catch {
    return null;
  }
}

function decomposeModalitiesHeuristic(query) {
  const modalities = ["text"];
  if (TABLE_HINTS.test(query)) modalities.push("table");
  if (IMAGE_HINTS.test(query)) modalities.push("image");
  return modalities;
}

async function decomposeModalitiesLLM(query, provider) {
  const prompt =
    "Given a user query, determine which modalities are required. " +
    "Available: 'text' (always included), 'table' (structured data), 'image' (visual content).\n" +
    "Respond with ONLY a comma-separated list.\n\n" +
    `Query: ${query}\nRequired Modalities:`;
  try {
    const resp = await axios.post(
      `${SYNTHESIZER_URL}/chat`,
      { agent_type: "synthesizer", prompt, provider },
      { timeout: 30000 }
    );
    const raw = resp.data.response.toLowerCase();
    const parsed = [];
    if (raw.includes("text")) parsed.push("text");
    if (raw.includes("table")) parsed.push("table");
    if (raw.includes("image")) parsed.push("image");
    return parsed.length ? parsed : ["text"];
  } catch (e) {
    console.warn(`LLM decomposition failed: ${e.message}. Falling back to heuristic.`);
    return decomposeModalitiesHeuristic(query);
  }
}

function detectContradictions(fused) {
  const numRe = /-?\d+(?:\.\d+)?/g;
  const numbersByModality = {};
  for (const item of fused) {
    const nums = new Set((item.content || "").match(numRe) || []);
    if (nums.size) {
      if (!numbersByModality[item.modality]) numbersByModality[item.modality] = new Set();
      for (const n of nums) numbersByModality[item.modality].add(n);
    }
  }
  const flags = [];
  if (numbersByModality.table && numbersByModality.text) {
    const tableNums = numbersByModality.table;
    const textNums = numbersByModality.text;
    let disjoint = tableNums.size > 0 && textNums.size > 0;
    for (const n of tableNums) {
      if (textNums.has(n)) {
        disjoint = false;
        break;
      }
    }
    if (disjoint) {
      flags.push(
        `Possible contradiction: table numbers ${[...tableNums]} differ from text numbers ${[...textNums]}`
      );
    }
  }
  return flags;
}

async function fanOut(modalities, query, topK, embedQuery) {
  const results = [];
  const calls = modalities
    .filter((m) => AGENT_URLS[m])
    .map(async (m) => {
      try {
        const data = await breakers[m].call(async () => {
          const resp = await axios.post(`${AGENT_URLS[m]}/retrieve`, { query, top_k: topK, embed_query: embedQuery }, { timeout: 60000 });
          return resp.data.results;
        });
        return { ok: true, data };
      } catch (e) {
        if (e instanceof CircuitOpenError) {
          console.warn(`Agent ${m} skipped: circuit open`);
        } else {
          console.warn(`Agent ${m} call failed: ${e.message}`);
        }
        return { ok: false, modality: m, error: e.message };
      }
    });
  const settled = await Promise.all(calls);
  for (const s of settled) {
    if (s.ok) results.push(...s.data);
  }
  return results;
}

async function ensureConversation(conversationId, userId, title) {
  if (conversationId) {
    try {
      const r = await axios.get(`${CHAT_URL}/conversations/${conversationId}`, {
        params: { user_id: userId },
        timeout: 5000,
        validateStatus: () => true,
      });
      if (r.status === 200) return conversationId;
    } catch {}
  }
  const resp = await axios.post(`${CHAT_URL}/conversations`, null, {
    params: { user_id: userId, title },
    timeout: 5000,
  });
  return resp.data.conversation_id;
}

async function writeChatHistory(conversationId, userId, title, userContent, assistantContent, modalitiesUsed, images = null, citations = null, contradictions = null) {
  try {
    const cid = await ensureConversation(conversationId, userId, title);
    await axios.post(
      `${CHAT_URL}/messages`,
      { conversation_id: cid, role: "user", content: userContent },
      { timeout: 5000 }
    );
    await axios.post(
      `${CHAT_URL}/messages`,
      {
        conversation_id: cid,
        role: "assistant",
        content: assistantContent,
        metadata: {
          modalities_used: modalitiesUsed,
          images: images,
          citations: citations,
          contradictions: contradictions
        },
      },
      { timeout: 5000 }
    );
    return cid;
  } catch (e) {
    console.warn(`Chat history write failed: ${e.message}`);
    return conversationId;
  }
}

app.get("/health", (req, res) => {
  res.json({ status: "ok", queue_depth: inFlightQueries });
});

app.post("/query", async (req, res) => {
  inFlightQueries += 1;
  try {
  const query = req.body.query;
  const topK = req.body.top_k ?? 5;
  let conversationId = req.body.conversation_id ?? null;
  const userId = req.body.user_id ?? null;
  const provider = req.body.provider ?? null;

  const cached = await cache.get(query, "response", topK);
  if (cached) {
    let cid = conversationId;
    if (userId) {
      cid = await writeChatHistory(
        conversationId,
        userId,
        query.slice(0, 60),
        query,
        cached.answer,
        cached.modalities_used,
        cached.images,
        cached.citations,
        cached.contradictions
      );
    }
    return res.json({ ...cached, conversation_id: cid });
  }

  const selectedFiles = req.body.selected_files ?? null;
  const modalities = await decomposeModalitiesLLM(query, provider);
  const embedQuery = await hydeExpand(query, provider);
  const results = await fanOut(modalities, query, selectedFiles ? Math.max(topK, 25) : topK, embedQuery);

  let filteredResults = results;
  if (selectedFiles && Array.isArray(selectedFiles) && selectedFiles.length > 0) {
    filteredResults = results.filter((r) => selectedFiles.includes(r.source));
  }

  const fused = crossModalRerank(filteredResults);
  const contradictions = detectContradictions(fused);

  let answer;
  try {
    const resp = await axios.post(
      `${SYNTHESIZER_URL}/synthesize`,
      { query, context: fused.slice(0, 8), provider },
      { timeout: 60000 }
    );
    answer = resp.data.answer;
  } catch (e) {
    answer = `(synthesis unavailable: ${e.message}) Top result: ${fused.length ? fused[0].content : "none"}`;
  }

  // Extract images from fused results to show prominently in the frontend response
  const images = fused
    .filter((f) => f.modality === "image" && f.metadata && f.metadata.image_base64)
    .map((f) => ({
      base64: f.metadata.image_base64,
      source: f.source,
      caption: f.content || `Image from ${f.source}`,
      score: f.fused_score,
    }));

  const responseData = {
    answer,
    images,
    citations: fused.map((f) => ({
      modality: f.modality,
      source: f.source,
      snippet: f.content.slice(0, 200),
      score: f.fused_score,
      metadata: f.metadata ?? null,
    })),
    contradictions,
    modalities_used: modalities,
    conversation_id: conversationId,
  };
  await cache.put(query, "response", responseData, topK);

  if (userId) {
    conversationId = await writeChatHistory(
      conversationId,
      userId,
      query.slice(0, 60),
      query,
      answer,
      modalities,
      images,
      responseData.citations,
      contradictions
    );
  }

  res.json({ ...responseData, conversation_id: conversationId });
  } catch (e) {
    res.status(500).json({ detail: `Internal Server Error: ${e.message}` });
  } finally {
    inFlightQueries -= 1;
  }
});

function currentStatus() {
  return {
    queue_depth: inFlightQueries,
    cache_stats: cache.stats(),
    circuit_states: Object.fromEntries(Object.entries(breakers).map(([m, b]) => [m, b.snapshot()])),
    shard_map: shardMap,
  };
}

app.get("/status", (req, res) => {
  res.json(currentStatus());
});

app.get("/stream/status", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  let i = 0;
  const interval = setInterval(() => {
    if (i >= 30) {
      clearInterval(interval);
      return res.end();
    }
    res.write(`data: ${JSON.stringify(currentStatus())}\n\n`);
    i++;
  }, 2000);
  req.on("close", () => clearInterval(interval));
});

const port = process.env.PORT || 8010;
app.listen(port, "0.0.0.0");
