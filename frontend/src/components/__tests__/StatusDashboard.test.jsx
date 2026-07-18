import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import StatusDashboard from "../StatusDashboard.jsx";

describe("StatusDashboard", () => {
  it("renders the waiting state with no status yet", () => {
    render(<StatusDashboard status={null} />);
    expect(screen.getByText(/waiting for the mesh/i)).toBeInTheDocument();
  });

  it(
    "does not crash when status is missing circuit_states/shard_map/rate_limiter_rejections " +
      "(this was a real bug: supervisor-service's /status only ever returned " +
      "{queue_depth, cache_stats}, so Object.values(circuit_states) and " +
      "Object.entries(shard_map) threw on undefined against the microservices deployment)",
    () => {
      const minimalStatus = { queue_depth: 3, cache_stats: {} };
      expect(() => render(<StatusDashboard status={minimalStatus} />)).not.toThrow();
      expect(screen.getByText("3")).toBeInTheDocument();
    }
  );

  it("renders full status with circuit breakers, shard map, and rejections", () => {
    const status = {
      queue_depth: 2,
      cache_stats: { text: { hit_ratio: 0.75 } },
      circuit_states: { text: { name: "text", state: "CLOSED" }, table: { name: "table", state: "OPEN" } },
      shard_map: { text: ["shard-0", "shard-1"] },
      rate_limiter_rejections: { "user-1": 5 },
    };
    render(<StatusDashboard status={status} />);
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("75.0%")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it('shows "none yet this session" when there are no rate limiter rejections', () => {
    const status = { queue_depth: 0, cache_stats: {}, circuit_states: {}, shard_map: {}, rate_limiter_rejections: {} };
    render(<StatusDashboard status={status} />);
    expect(screen.getByText(/none yet this session/i)).toBeInTheDocument();
  });
});
