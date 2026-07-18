import React, { useEffect, useState } from "react";

export default function ChatHistory({ apiUrl, user, activeId, onSelect, onNewChat, refreshKey }) {
  const [chats, setChats] = useState([]);
  const [localRefresh, setLocalRefresh] = useState(0);

  useEffect(() => {
    if (!user) {
      setChats([]);
      return;
    }
    fetch(`${apiUrl}/chats`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then(setChats)
      .catch(() => setChats([]));
  }, [apiUrl, user, refreshKey, localRefresh]);

  const handleDeleteChat = async (e, conversationId) => {
    e.stopPropagation(); // Prevent selecting the chat when clicking delete
    if (!window.confirm("Are you sure you want to delete this chat conversation?")) return;
    try {
      const res = await fetch(`${apiUrl}/chats/${conversationId}`, {
        method: "DELETE",
        credentials: "include"
      });
      if (res.ok) {
        if (conversationId === activeId) {
          onNewChat(); // Reset to new chat if currently viewing the deleted one
        }
        setLocalRefresh((k) => k + 1);
      }
    } catch (err) {
      console.error("Failed to delete chat:", err);
    }
  };

  if (!user) {
    return (
      <div className="card history-panel">
        <h3>Chat History</h3>
        <p className="history-empty">sign in to save and revisit conversations</p>
      </div>
    );
  }

  return (
    <div className="card history-panel">
      <h3>Chat History</h3>
      <button className="new-chat-btn" onClick={onNewChat}>+ New chat</button>
      {chats.length === 0 && <p className="history-empty">no conversations yet</p>}
      <div className="history-list">
        {chats.map((c) => (
          <div
            key={c.conversation_id}
            className={`history-item-container ${c.conversation_id === activeId ? "active" : ""}`}
          >
            <button
              className="history-item-btn"
              onClick={() => onSelect(c.conversation_id)}
              title={c.title || "Untitled"}
            >
              {c.title || "Untitled"}
            </button>
            <button
              className="history-delete-btn"
              onClick={(e) => handleDeleteChat(e, c.conversation_id)}
              title="Delete chat"
              aria-label="Delete chat"
            >
              &times;
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
