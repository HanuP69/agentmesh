function boolEnv(name, fallback) {
  const v = process.env[name];
  if (v === undefined) return fallback;
  return v.toLowerCase() === "true";
}

module.exports = {
  REDIS_URL: process.env.REDIS_URL || "redis://redis:6379/0",
  USE_REDIS: boolEnv("USE_REDIS", true),
  GOOGLE_CLIENT_ID: process.env.GOOGLE_CLIENT_ID || "",
  JWT_SECRET: process.env.JWT_SECRET || "dev-insecure-secret-change-me",
  JWT_ALGORITHM: "HS256",
  JWT_EXPIRE_MINUTES: parseInt(process.env.JWT_EXPIRE_MINUTES || String(60 * 24 * 7), 10),
  COOKIE_NAME: process.env.COOKIE_NAME || "agentmesh_session",
  COOKIE_SECURE: boolEnv("COOKIE_SECURE", false),
  FRONTEND_ORIGIN: process.env.FRONTEND_ORIGIN || "http://localhost:5173",
};
