const express = require("express");
const cors = require("cors");
const cookieParser = require("cookie-parser");
const axios = require("axios");
const http = require("http");
const https = require("https");
const multer = require("multer");
const FormData = require("form-data");
const Redis = require("ioredis");

const httpAgent = new http.Agent({ keepAlive: true, keepAliveMsecs: 60000, maxSockets: 1000 });
const httpsAgent = new https.Agent({ keepAlive: true, keepAliveMsecs: 60000, maxSockets: 1000, rejectUnauthorized: false });
axios.defaults.httpAgent = httpAgent;
axios.defaults.httpsAgent = httpsAgent;

const config = require("./lib/config");
const { TokenBucketRateLimiter } = require("./lib/rateLimiter");
const { createAccessToken, makeGoogleVerifier, getCurrentUser } = require("./lib/auth");

const app = express();
app.set("trust proxy", true);
app.use(express.json());
app.use(cookieParser());
app.use(
  cors({
    origin: config.FRONTEND_ORIGIN,
    credentials: true,
  })
);

const upload = multer({ storage: multer.memoryStorage() });

let redisClient = null;
if (config.USE_REDIS) {
  redisClient = new Redis(config.REDIS_URL, { lazyConnect: true, maxRetriesPerRequest: 1 });
  redisClient.on("error", () => {});
  redisClient.connect().catch(() => {
    redisClient = null;
  });
}

const edgeLimiter = new TokenBucketRateLimiter(redisClient, { capacity: 1000, refillRate: 200 });
const verifyGoogleIdToken = makeGoogleVerifier(config);

const INTERNAL_BASE = process.env.INTERNAL_BASE_URL || "http://nginx-internal:8080";
const SUPERVISOR_URL = process.env.SUPERVISOR_URL || `${INTERNAL_BASE}/supervisor`;
const CHAT_URL = process.env.CHAT_URL || `${INTERNAL_BASE}/chat`;
const INGESTION_URL = process.env.INGESTION_URL || `${INTERNAL_BASE}/ingestion`;

const requireUser = getCurrentUser(config, true);
const optionalUser = getCurrentUser(config, false);

app.post("/auth/google", async (req, res) => {
  const info = await verifyGoogleIdToken(req.body.credential);
  if (!info) return res.status(401).json({ detail: "invalid Google token" });
  try {
    await axios.post(
      `${CHAT_URL}/users`,
      { sub: info.sub, email: info.email, name: info.name, picture: info.picture },
      { timeout: 5000 }
    );
  } catch {}
  const token = createAccessToken(info.sub, info.email, config);
  const cookieOptions = {
    httpOnly: true,
    secure: config.COOKIE_SECURE,
    sameSite: config.COOKIE_SECURE ? "none" : "lax",
    maxAge: config.JWT_EXPIRE_MINUTES * 60 * 1000,
  };

  res.cookie(config.COOKIE_NAME, token, cookieOptions);
  res.json({ email: info.email, name: info.name, picture: info.picture });
});

app.post("/auth/mock", async (req, res) => {
  const info = {
    sub: "dev-user-id-999",
    email: "dev@agentmesh.local",
    name: "Developer Admin",
    picture: "https://www.gravatar.com/avatar/00000000000000000000000000000000?d=mp&f=y"
  };
  try {
    await axios.post(
      `${CHAT_URL}/users`,
      { sub: info.sub, email: info.email, name: info.name, picture: info.picture },
      { timeout: 5000 }
    );
  } catch {}
  const token = createAccessToken(info.sub, info.email, config);
  
  const cookieOptions = {
    httpOnly: true,
    secure: config.COOKIE_SECURE,
    sameSite: config.COOKIE_SECURE ? "none" : "lax",
    maxAge: config.JWT_EXPIRE_MINUTES * 60 * 1000,
  };

  res.cookie(config.COOKIE_NAME, token, cookieOptions);
  res.json({ email: info.email, name: info.name, picture: info.picture });
});

app.post("/auth/logout", (req, res) => {
  res.clearCookie(config.COOKIE_NAME);
  res.json({ ok: true });
});

app.get("/auth/me", requireUser, async (req, res) => {
  try {
    const r = await axios.get(`${CHAT_URL}/users/${req.user.user_id}`, { timeout: 5000 });
    if (r.status === 200) return res.json(r.data);
  } catch {}
  res.json(req.user);
});

app.get("/chats", requireUser, async (req, res) => {
  try {
    const r = await axios.get(`${CHAT_URL}/conversations`, { params: { user_id: req.user.user_id }, timeout: 5000 });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Chat service unreachable" });
  }
});

app.post("/chats", requireUser, async (req, res) => {
  try {
    const title = req.body?.title || req.query.title || "New chat";
    const r = await axios.post(`${CHAT_URL}/conversations`, null, {
      params: { user_id: req.user.user_id, title },
      timeout: 5000,
    });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Chat service unreachable" });
  }
});

app.get("/chats/:conversationId/messages", requireUser, async (req, res) => {
  try {
    const r = await axios.get(`${CHAT_URL}/conversations/${req.params.conversationId}`, {
      params: { user_id: req.user.user_id },
      timeout: 5000,
      validateStatus: () => true,
    });
    if (r.status === 404) return res.status(404).json({ detail: "conversation not found" });
    const msgs = await axios.get(`${CHAT_URL}/conversations/${req.params.conversationId}/messages`, { timeout: 5000 });
    res.json(msgs.data);
  } catch (e) {
    res.status(502).json({ detail: "Chat service unreachable" });
  }
});

app.post("/chats/:conversationId/files", requireUser, async (req, res) => {
  try {
    const r = await axios.post(`${CHAT_URL}/conversations/${req.params.conversationId}/files`, req.body, {
      params: { user_id: req.user.user_id },
      timeout: 5000,
    });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Chat service unreachable" });
  }
});

app.get("/chats/:conversationId/files", requireUser, async (req, res) => {
  try {
    const r = await axios.get(`${CHAT_URL}/conversations/${req.params.conversationId}/files`, {
      params: { user_id: req.user.user_id },
      timeout: 5000,
    });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Chat service unreachable" });
  }
});

app.delete("/chats/:conversationId", requireUser, async (req, res) => {
  try {
    await axios.delete(`${CHAT_URL}/conversations/${req.params.conversationId}`, {
      params: { user_id: req.user.user_id },
      timeout: 5000,
    });
    res.json({ status: "ok" });
  } catch (e) {
    res.status(502).json({ detail: "Chat service unreachable" });
  }
});

app.delete("/chats/:conversationId/files", requireUser, async (req, res) => {
  try {
    const filename = req.query.filename;
    if (!filename) return res.status(400).json({ detail: "filename query param is required" });
    const r = await axios.delete(`${CHAT_URL}/conversations/${req.params.conversationId}/files`, {
      params: { user_id: req.user.user_id, filename },
      timeout: 5000,
    });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Chat service unreachable" });
  }
});

app.post("/query", optionalUser, async (req, res) => {
  const identifier = req.user ? req.user.user_id : `ip:${req.ip}`;
  const allowed = await edgeLimiter.allow(identifier);
  if (!allowed) return res.status(429).json({ detail: "rate limit exceeded" });

  const payload = {
    query: req.body.query,
    top_k: req.body.top_k ?? 5,
    urgency: req.body.urgency ?? 1.0,
    conversation_id: req.body.conversation_id ?? null,
    provider: req.body.provider ?? null,
    selected_files: req.body.selected_files ?? null,
  };
  if (req.user) {
    payload.user_id = req.user.user_id;
    payload.user_email = req.user.email;
  }
  try {
    const r = await axios.post(`${SUPERVISOR_URL}/query`, payload, { timeout: 60000 });
    res.json(r.data);
  } catch {
    res.status(500).json({ detail: "Internal Server Error" });
  }
});

app.post("/ingest/text", requireUser, async (req, res) => {
  try {
    const { doc_id, source, text } = { ...req.query, ...req.body };
    const r = await axios.post(`${INGESTION_URL}/ingest/text`, null, {
      params: { doc_id, source, text },
      timeout: 180000,
    });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Ingestion service timed out or returned an error" });
  }
});

app.post("/ingest/table", requireUser, async (req, res) => {
  try {
    const { doc_id, source, table_text } = { ...req.query, ...req.body };
    const r = await axios.post(`${INGESTION_URL}/ingest/table`, null, {
      params: { doc_id, source, table_text },
      timeout: 180000,
    });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Ingestion service timed out or returned an error" });
  }
});

app.post("/ingest/upload", requireUser, upload.single("file"), async (req, res) => {
  try {
    const form = new FormData();
    form.append("file", req.file.buffer, { filename: req.file.originalname, contentType: req.file.mimetype });
    form.append("caption", req.body.caption || "");
    const r = await axios.post(`${INGESTION_URL}/ingest/upload`, form, {
      headers: form.getHeaders(),
      timeout: 180000,
    });
    res.json(r.data);
  } catch (e) {
    res.status(502).json({ detail: "Ingestion service timed out or returned an error" });
  }
});

app.get("/health", (req, res) => {
  res.json({ status: "ok", service: "api-gateway" });
});

app.get("/status", async (req, res) => {
  try {
    const r = await axios.get(`${SUPERVISOR_URL}/status`, { timeout: 5000 });
    // rate_limiter_rejections lives here (edge limiter), not in the supervisor —
    // merge it in rather than leaving the field missing on the proxied response.
    res.json({ ...r.data, rate_limiter_rejections: edgeLimiter.rejectionCounts() });
  } catch {
    res.json({ error: "supervisor unreachable", rate_limiter_rejections: edgeLimiter.rejectionCounts() });
  }
});

app.get("/stream/status", async (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  try {
    const upstream = await axios.get(`${SUPERVISOR_URL}/stream/status`, { responseType: "stream", timeout: 120000 });
    let buffer = "";
    // Parse each "data: {...}\n\n" SSE frame and merge in the gateway's own
    // rate_limiter_rejections before re-emitting, instead of blind-piping the
    // supervisor's stream (which has no visibility into edge rejections at all).
    upstream.data.on("data", (chunk) => {
      buffer += chunk.toString();
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const match = frame.match(/^data: (.*)$/m);
        if (match) {
          try {
            const payload = JSON.parse(match[1]);
            payload.rate_limiter_rejections = edgeLimiter.rejectionCounts();
            res.write(`data: ${JSON.stringify(payload)}\n\n`);
            continue;
          } catch {
            // fall through to re-emit the raw frame unmodified
          }
        }
        res.write(`${frame}\n\n`);
      }
    });
    upstream.data.on("end", () => res.end());
    upstream.data.on("error", () => {
      res.write('data: {"error": "stream disconnected"}\n\n');
      res.end();
    });
    req.on("close", () => upstream.data.destroy());
  } catch {
    res.write('data: {"error": "stream disconnected"}\n\n');
    res.end();
  }
});

const port = process.env.PORT || 8000;
const server = app.listen(port, "0.0.0.0");
server.timeout = 180000;
server.keepAliveTimeout = 180000;
server.headersTimeout = 180000;
server.requestTimeout = 180000;
