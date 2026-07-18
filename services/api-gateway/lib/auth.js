const jwt = require("jsonwebtoken");
const { OAuth2Client } = require("google-auth-library");

function createAccessToken(userId, email, config) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { sub: userId, email, iat: now, exp: now + config.JWT_EXPIRE_MINUTES * 60 };
  return jwt.sign(payload, config.JWT_SECRET, { algorithm: config.JWT_ALGORITHM, noTimestamp: true });
}

function decodeAccessToken(token, config) {
  try {
    return jwt.verify(token, config.JWT_SECRET, { algorithms: [config.JWT_ALGORITHM] });
  } catch {
    return null;
  }
}

function makeGoogleVerifier(config) {
  const client = new OAuth2Client();
  return async function verifyGoogleIdToken(token) {
    if (!config.GOOGLE_CLIENT_ID) return null;
    try {
      const ticket = await client.verifyIdToken({ idToken: token, audience: config.GOOGLE_CLIENT_ID });
      const info = ticket.getPayload();
      if (!["accounts.google.com", "https://accounts.google.com"].includes(info.iss)) return null;
      return { sub: info.sub, email: info.email || "", name: info.name || "", picture: info.picture || "" };
    } catch {
      return null;
    }
  };
}

function getCurrentUser(config, required) {
  return (req, res, next) => {
    const authHeader = req.headers["authorization"];
    const token = req.cookies[config.COOKIE_NAME] || (authHeader && authHeader.startsWith("Bearer ") ? authHeader.substring(7) : null);
    if (!token) {
      if (required) return res.status(401).json({ detail: "not authenticated" });
      req.user = null;
      return next();
    }
    const payload = decodeAccessToken(token, config);
    if (!payload) {
      if (required) return res.status(401).json({ detail: "invalid or expired session" });
      req.user = null;
      return next();
    }
    req.user = { user_id: payload.sub, email: payload.email };
    next();
  };
}

module.exports = { createAccessToken, decodeAccessToken, makeGoogleVerifier, getCurrentUser };
