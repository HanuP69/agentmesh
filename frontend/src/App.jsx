import React, { useCallback, useEffect, useState } from "react";
import BambooScene from "./components/BambooScene.jsx";
import ChatHistory from "./components/ChatHistory.jsx";
import GoogleSignInButton from "./components/GoogleSignInButton.jsx";
import QueryPanel from "./components/QueryPanel.jsx";
import StatusDashboard from "./components/StatusDashboard.jsx";
import UploadPanel from "./components/UploadPanel.jsx";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export default function App() {
  const [status, setStatus] = useState(null);
  const [connError, setConnError] = useState(false);
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [showStats, setShowStats] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState([]);

  useEffect(() => {
    fetch(`${API_URL}/auth/me`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setUser)
      .finally(() => setAuthChecked(true));
  }, []);

  useEffect(() => {
    let es;
    try {
      es = new EventSource(`${API_URL}/stream/status`);
      es.onmessage = (e) => {
        setConnError(false);
        setStatus(JSON.parse(e.data));
      };
      es.onerror = () => setConnError(true);
    } catch {
      setConnError(true);
    }
    return () => es && es.close();
  }, []);

  const handleSignedIn = useCallback((u) => setUser(u), []);
  
  const handleDevLogin = async () => {
    try {
      const res = await fetch(`${API_URL}/auth/mock`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" }
      });
      if (res.ok) {
        setUser(await res.json());
      } else {
        alert("Dev bypass login failed!");
      }
    } catch (err) {
      console.error(err);
      alert("Dev bypass connection error!");
    }
  };

  const handleLogout = async () => {
    await fetch(`${API_URL}/auth/logout`, { method: "POST", credentials: "include" });
    setUser(null);
    setActiveConversationId(null);
  };

  const handleConversationCreated = useCallback((id) => {
    setActiveConversationId(id);
    setHistoryRefreshKey((k) => k + 1);
  }, []);

  const handleNewChat = () => setActiveConversationId(null);

  const handleSelectChat = (id) => {
    setActiveConversationId(id);
    setSidebarOpen(false);
  };

  return (
    <>
      <BambooScene />
      <div className={`app ${sidebarOpen ? "sidebar-active" : ""}`}>
        <header className="torii-header">
          <div className="header-left">
            <button
              className="hamburger-btn"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              title="Chat History"
              aria-label="Toggle chat history"
            >
              <svg viewBox="0 0 24 24">
                <path d="M3 6h18v2H3V6zm0 5h18v2H3v-2zm0 5h18v2H3v-2z" />
              </svg>
            </button>
          </div>
          <div className="header-center">
            <svg className="torii-mark" viewBox="0 0 64 40">
              <rect x="4" y="8" width="56" height="4" fill="var(--gold)" />
              <rect x="0" y="14" width="64" height="3" fill="var(--gold)" />
              <rect x="10" y="17" width="4" height="21" fill="var(--gold)" />
              <rect x="50" y="17" width="4" height="21" fill="var(--gold)" />
            </svg>
            <h1 className="brush-title">AgentMesh</h1>
            <p className="subtitle">
              distributed multi-agent multimodal RAG ·{" "}
              {connError ? "backend unreachable — start docker compose" : "live via SSE"}
            </p>
          </div>
          <div className="auth-corner" style={{ display: "flex", gap: "10px", alignItems: "center" }}>
            <button 
              className="stats-btn" 
              onClick={() => setShowStats(true)} 
              title="Stats for Nerds"
            >
              <svg viewBox="0 0 24 24">
                <path d="M19.43 12.98c.04-.32.07-.64.07-.98s-.03-.66-.07-.98l2.11-1.65c.19-.15.24-.42.12-.64l-2-3.46c-.12-.22-.39-.3-.61-.22l-2.49 1c-.52-.4-1.08-.73-1.69-.98l-.38-2.65C14.46 2.18 14.25 2 14 2h-4c-.25 0-.46.18-.49.42l-.38 2.65c-.61.25-1.17.59-1.69.98l-2.49-1c-.23-.09-.49 0-.61.22l-2 3.46c-.13.22-.07.49.12.64l2.11 1.65c-.04.32-.07.65-.07.98s.03.66.07.98l-2.11 1.65c-.19.15-.24.42-.12.64l2 3.46c.12.22.39.3.61.22l2.49-1c.52.4 1.08.73 1.69.98l.38 2.65c.03.24.24.42.49.42h4c.25 0 .46-.18.49-.42l.38-2.65c.61-.25 1.17-.59 1.69-.98l2.49 1c.23.09.49 0 .61-.22l2-3.46c.12-.22.07-.49-.12-.64l-2.11-1.65zM12 15.5c-1.93 0-3.5-1.57-3.5-3.5s1.57-3.5 3.5-3.5 3.5 1.57 3.5 3.5-1.57 3.5-3.5 3.5z" />
              </svg>
            </button>
            {authChecked && (user ? (
              <div className="user-chip">
                {user.picture && <img src={user.picture} alt="" className="user-avatar" />}
                <span>{user.name || user.email}</span>
                <button className="logout-btn" onClick={handleLogout}>Sign out</button>
              </div>
            ) : (
              authChecked && (
                <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                  <GoogleSignInButton apiUrl={API_URL} onSignedIn={handleSignedIn} />
                  <button className="logout-btn" onClick={handleDevLogin} style={{ background: "var(--torii-red)", color: "#fff", border: "none" }}>
                    Dev Bypass
                  </button>
                </div>
              )
            ))}
          </div>
        </header>

        {/* Sidebar overlay + drawer */}
        {sidebarOpen && (
          <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />
        )}
        <aside className={`sidebar-drawer ${sidebarOpen ? "open" : ""}`}>
          <div className="sidebar-drawer-header">
            <h2>Conversations</h2>
            <button className="sidebar-close-btn" onClick={() => setSidebarOpen(false)}>&times;</button>
          </div>
          <ChatHistory
            apiUrl={API_URL}
            user={user}
            activeId={activeConversationId}
            onSelect={handleSelectChat}
            onNewChat={() => { handleNewChat(); setSidebarOpen(false); }}
            refreshKey={historyRefreshKey}
          />
        </aside>

        <main className="main-content-full">
          <UploadPanel
            apiUrl={API_URL}
            user={user}
            conversationId={activeConversationId}
            onConversationCreated={handleConversationCreated}
            onSelectionChange={setSelectedFiles}
          />
          <QueryPanel
            apiUrl={API_URL}
            user={user}
            conversationId={activeConversationId}
            onConversationCreated={handleConversationCreated}
            selectedFiles={selectedFiles}
          />
        </main>
      </div>

      {showStats && (
        <div className="stats-modal-overlay" onClick={() => setShowStats(false)}>
          <div className="stats-modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="stats-modal-header">
              <h2>Stats for Nerds</h2>
              <button className="stats-close-btn" onClick={() => setShowStats(false)}>&times;</button>
            </div>
            <StatusDashboard status={status} />
          </div>
        </div>
      )}
    </>
  );
}
