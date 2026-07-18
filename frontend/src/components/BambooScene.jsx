import React, { useMemo } from "react";

/* Ambient signature scene: swaying bamboo stalks framing the viewport +
   drifting sakura petals. Fixed, pointer-events none, sits behind content. */

function BambooStalk({ x, height, delay, flip }) {
  const segments = Math.round(height / 42);
  return (
    <svg
      className="bamboo-stalk"
      style={{ left: `${x}%`, animationDelay: `${delay}s`, transform: flip ? "scaleX(-1)" : "none" }}
      width="34"
      height={height}
      viewBox={`0 0 34 ${height}`}
    >
      <defs>
        <linearGradient id={`stalk-${x}`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--bamboo-dark)" />
          <stop offset="50%" stopColor="var(--bamboo)" />
          <stop offset="100%" stopColor="var(--bamboo-dark)" />
        </linearGradient>
      </defs>
      <rect x="10" y="0" width="14" height={height} rx="6" fill={`url(#stalk-${x})`} />
      {Array.from({ length: segments }).map((_, i) => (
        <rect key={i} x="8" y={i * 42 + 38} width="18" height="4" rx="2" fill="var(--bamboo-node)" />
      ))}
      {/* a couple of leaves for organic silhouette */}
      <path d="M24 60 Q 46 50 40 30 Q 26 42 24 60 Z" fill="var(--bamboo-light)" opacity="0.85" />
      <path d="M10 140 Q -14 132 -10 110 Q 6 122 10 140 Z" fill="var(--bamboo-light)" opacity="0.7" />
    </svg>
  );
}

function Petal({ left, duration, delay, size, drift }) {
  return (
    <div
      className="sakura-petal"
      style={{
        left: `${left}%`,
        width: size,
        height: size * 0.8,
        animationDuration: `${duration}s`,
        animationDelay: `${delay}s`,
        "--drift": `${drift}px`,
      }}
    />
  );
}

export default function BambooScene() {
  const petals = useMemo(
    () =>
      Array.from({ length: 16 }).map((_, i) => ({
        id: i,
        left: Math.random() * 100,
        duration: 9 + Math.random() * 8,
        delay: Math.random() * 10,
        size: 8 + Math.random() * 8,
        drift: (Math.random() - 0.5) * 160,
      })),
    []
  );

  const stalks = useMemo(
    () => [
      { x: 1, height: 340, delay: 0 },
      { x: 5.5, height: 220, delay: 0.6 },
      { x: 94, height: 300, delay: 0.3, flip: true },
      { x: 98, height: 200, delay: 0.9, flip: true },
    ],
    []
  );

  return (
    <div className="bamboo-scene" aria-hidden="true">
      {stalks.map((s) => (
        <BambooStalk key={s.x} {...s} />
      ))}
      {petals.map((p) => (
        <Petal key={p.id} {...p} />
      ))}
    </div>
  );
}
