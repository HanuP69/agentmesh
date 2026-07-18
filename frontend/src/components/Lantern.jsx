import React from "react";

const GLOW = {
  CLOSED: "var(--lantern-green)",
  OPEN: "var(--lantern-red)",
  HALF_OPEN: "var(--lantern-amber)",
};

const LABEL = {
  CLOSED: "flowing",
  OPEN: "tripped",
  HALF_OPEN: "testing",
};

export default function Lantern({ name, state }) {
  const glow = GLOW[state] || "var(--lantern-amber)";
  return (
    <div className="lantern-unit">
      <svg width="46" height="72" viewBox="0 0 46 72" className={`lantern-svg lantern-${state}`}>
        <line x1="23" y1="0" x2="23" y2="10" stroke="var(--wood)" strokeWidth="2" />
        <ellipse cx="23" cy="14" rx="6" ry="4" fill="var(--wood)" />
        <path
          d="M8 18 Q23 8 38 18 Q46 40 38 58 Q23 68 8 58 Q0 40 8 18 Z"
          fill={glow}
          opacity="0.9"
          className="lantern-glow"
          style={{ filter: `drop-shadow(0 0 8px ${glow})` }}
        />
        <path d="M8 18 Q23 8 38 18" fill="none" stroke="var(--wood)" strokeWidth="1.5" />
        <path d="M8 58 Q23 68 38 58" fill="none" stroke="var(--wood)" strokeWidth="1.5" />
        <line x1="4" y1="38" x2="42" y2="38" stroke="var(--wood)" strokeWidth="1" opacity="0.5" />
        <ellipse cx="23" cy="63" rx="5" ry="3" fill="var(--wood)" />
      </svg>
      <div className="lantern-name">{name.replace("_agent", "")}</div>
      <div className="lantern-label" style={{ color: glow }}>{LABEL[state] || state}</div>
    </div>
  );
}
