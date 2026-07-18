function boolEnv(name, fallback) {
  const v = process.env[name];
  if (v === undefined) return fallback;
  return v.toLowerCase() === "true";
}

module.exports = {
  REDIS_URL: process.env.REDIS_URL || "redis://redis:6379/0",
  USE_REDIS: boolEnv("USE_REDIS", true),
};
