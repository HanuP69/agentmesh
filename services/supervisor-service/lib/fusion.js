const DEFAULT_MODALITY_WEIGHTS = { text: 1.0, table: 1.1, image: 0.85 };

function fuse(results, weights = DEFAULT_MODALITY_WEIGHTS) {
  const fused = results.map((r) => {
    const w = weights[r.modality] ?? 1.0;
    const combined = w * Math.sqrt(Math.max(0.0, r.raw_score)) * Math.sqrt(Math.max(0.0, r.confidence));
    return { ...r, fused_score: combined };
  });
  fused.sort((a, b) => b.fused_score - a.fused_score);
  return fused;
}

function crossModalRerank(results, weights) {
  return fuse(results, weights);
}

module.exports = { fuse, crossModalRerank, DEFAULT_MODALITY_WEIGHTS };
