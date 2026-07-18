import React, { useEffect, useRef, useState } from "react";
import MarkdownRenderer from "./MarkdownRenderer.jsx";

export default function QueryPanel({ apiUrl, user, conversationId, onConversationCreated, selectedFiles }) {
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState([]); // [{role, content, citations?, contradictions?, modalities_used?}]
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [provider, setProvider] = useState("GEMINI");
  const bottomRef = useRef(null);

  // Load an existing conversation's messages when it's selected.
  useEffect(() => {
    if (!user || !conversationId) {
      setTurns([]);
      return;
    }
    fetch(`${apiUrl}/chats/${conversationId}/messages`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((msgs) => setTurns(msgs.map((m) => ({
        role: m.role,
        content: m.content,
        modalities_used: m.modalities_used || m.metadata?.modalities_used,
        images: m.images || m.metadata?.images,
        citations: m.citations || m.metadata?.citations,
        contradictions: m.contradictions || m.metadata?.contradictions
      }))))
      .catch(() => setTurns([]));
  }, [apiUrl, user, conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, loading]);

  const runQuery = async () => {
    if (!q.trim()) return;
    const userTurn = { role: "user", content: q };
    setTurns((t) => [...t, userTurn]);
    setQ("");
    setLoading(true);
    setErr(null);
    try {
      const res = await fetch(`${apiUrl}/query`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: userTurn.content,
          top_k: 5,
          urgency: 1.0,
          conversation_id: conversationId,
          provider,
          selected_files: selectedFiles
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTurns((t) => [...t, { role: "assistant", content: data.answer, ...data }]);
      if (user && data.conversation_id && data.conversation_id !== conversationId) {
        onConversationCreated(data.conversation_id);
      }
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="query-scroll">
      {turns.length > 0 && (
        <div className="chat-thread">
          {turns.map((t, i) =>
            t.role === "user" ? (
              <div key={i} className="turn user-turn">{t.content}</div>
            ) : (
              <div key={i} className="turn assistant-turn">
                {t.modalities_used?.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    {t.modalities_used.map((m) => <span key={m} className="tag">{m}</span>)}
                  </div>
                )}
                <MarkdownRenderer content={t.content} />
                
                {t.images?.length > 0 && (
                  <div className="answer-images">
                    {t.images.map((img, j) => (
                      <div key={j} className="answer-image-card">
                        <div className="image-container">
                          <img
                            src={`data:image/png;base64,${img.base64}`}
                            alt={img.caption}
                            className="answer-image"
                          />
                        </div>
                        <div className="answer-image-meta">
                          <span className="image-caption">{img.caption}</span>
                          <span className="image-source">Source: {img.source}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {t.contradictions?.length > 0 &&
                  t.contradictions.map((c, j) => <div key={j} className="contradiction">⚠ {c}</div>)}
                {t.citations?.length > 0 && (
                  <div className="citations-inline">
                    {t.citations.map((c, j) => (
                      <div key={j} className="citation">
                        <span className="tag">{c.modality}</span>
                        {c.source} — score {c.score.toFixed(3)} — {c.snippet}
                        {c.modality === "image" && c.metadata?.image_base64 && (
                          <div style={{ marginTop: 8 }}>
                            <img
                              src={`data:image/png;base64,${c.metadata.image_base64}`}
                              alt={c.source}
                              style={{ maxWidth: "200px", maxHeight: "150px", borderRadius: "4px", border: "1px solid rgba(107, 74, 52, 0.2)" }}
                            />
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          )}
          <div ref={bottomRef} />
        </div>
      )}

      {err && <div className="contradiction">Error: {err}</div>}

      <div className="query-box">
        <input
          placeholder="Ask across text, tables, and images..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runQuery()}
        />
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="GEMINI">GEMINI</option>
          <option value="NIM">NIM</option>
          <option value="OLLAMA">OLLAMA</option>
        </select>
        <button onClick={runQuery} disabled={loading}>
          {loading ? "Seeking..." : "Ask"}
        </button>
      </div>
    </div>
  );
}
