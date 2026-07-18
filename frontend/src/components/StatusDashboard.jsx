import React from "react";
import Lantern from "./Lantern.jsx";

export default function StatusDashboard({ status }) {
  if (!status) {
    return (
      <div className="card">
        <h3>System Status</h3>
        <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 13 }}>
          waiting for the mesh to wake...
        </span>
      </div>
    );
  }

  const {
    queue_depth = 0,
    cache_stats = {},
    circuit_states = {},
    shard_map = {},
    rate_limiter_rejections = {},
  } = status;
  const rejectionEntries = Object.entries(rate_limiter_rejections);

  return (
    <div className="grid">
      <div className="card">
        <h3>Queue Depth</h3>
        <div className="metric">{queue_depth}</div>
      </div>

      <div className="card">
        <h3>Circuit Breakers</h3>
        <div className="lantern-row">
          {Object.values(circuit_states).map((c) => (
            <Lantern key={c.name} name={c.name} state={c.state} />
          ))}
        </div>
      </div>

      <div className="card">
        <h3>Cache Hit Rate</h3>
        {Object.entries(cache_stats).map(([modality, s]) => (
          <div key={modality} style={{ marginBottom: 10 }}>
            <div className="shard-row" style={{ border: "none", padding: "2px 0" }}>
              <span>{modality}</span>
              <span>{(s.hit_ratio * 100).toFixed(1)}%</span>
            </div>
            <div className="bamboo-meter">
              <div className="bamboo-meter-fill" style={{ width: `${s.hit_ratio * 100}%` }} />
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Shard Map</h3>
        {Object.entries(shard_map).map(([modality, nodes]) => (
          <div key={modality} className="shard-row">
            <span>{modality}</span>
            <div className="stone-cluster">
              {nodes.map((n) => (
                <span key={n} className="stone" title={n} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Rate Limiter Rejections</h3>
        {rejectionEntries.length === 0 ? (
          <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#7a7160" }}>
            none yet this session
          </span>
        ) : (
          rejectionEntries.map(([agentType, count]) => (
            <div key={agentType} className="shard-row">
              <span>{agentType}</span>
              <span>{count}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
