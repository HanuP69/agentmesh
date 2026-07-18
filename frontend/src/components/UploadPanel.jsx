import React, { useCallback, useEffect, useRef, useState } from "react";

const ACCEPT = ".txt,.md,.csv,.tsv,.pdf,.png,.jpg,.jpeg,.gif,.webp";
const IMAGE_EXTS = ["png", "jpg", "jpeg", "gif", "webp"];

export default function UploadPanel({ apiUrl, user, conversationId, onConversationCreated, onSelectionChange }) {
  const [items, setItems] = useState([]); // {id, name, status: uploading|done|error, modality?, chunks?, error?}
  const [selectedIds, setSelectedIds] = useState(new Set()); // set of item names that are selected
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  // Fetch ingested files for the current conversation when it changes
  useEffect(() => {
    if (!user || !conversationId) {
      setItems([]);
      setSelectedIds(new Set());
      if (onSelectionChange) onSelectionChange([]);
      return;
    }
    fetch(`${apiUrl}/chats/${conversationId}/files`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : []))
      .then((files) => {
        const mapped = files.map((f, idx) => ({
          id: `db-${idx}-${f.filename}`,
          name: f.filename,
          status: "done",
          modality: f.modality,
          chunks: f.chunks
        }));
        setItems(mapped);
        
        // Auto-select all by default
        const allNames = mapped.map(it => it.name);
        setSelectedIds(new Set(allNames));
        if (onSelectionChange) onSelectionChange(allNames);
      })
      .catch(() => {
        setItems([]);
        setSelectedIds(new Set());
        if (onSelectionChange) onSelectionChange([]);
      });
  }, [apiUrl, user, conversationId]);

  // Propagate selected files to parent
  const updateParentSelection = (updatedSelectedIds, currentItems) => {
    if (onSelectionChange) {
      const activeNames = currentItems
        .filter(it => it.status === "done" && updatedSelectedIds.has(it.name))
        .map(it => it.name);
      onSelectionChange(activeNames);
    }
  };

  const handleToggleSelect = (name) => {
    const next = new Set(selectedIds);
    if (next.has(name)) {
      next.delete(name);
    } else {
      next.add(name);
    }
    setSelectedIds(next);
    updateParentSelection(next, items);
  };

  const handleDeleteFile = async (e, name, id) => {
    e.stopPropagation();
    if (!window.confirm(`Are you sure you want to remove "${name}" from this conversation?`)) return;

    if (user && conversationId) {
      try {
        const res = await fetch(`${apiUrl}/chats/${conversationId}/files?filename=${encodeURIComponent(name)}`, {
          method: "DELETE",
          credentials: "include"
        });
        if (!res.ok) console.warn("Failed to remove file association from backend.");
      } catch (err) {
        console.error("Failed to remove file:", err);
      }
    }

    // Remove from UI state
    const nextItems = items.filter(it => it.id !== id);
    setItems(nextItems);

    const nextSelected = new Set(selectedIds);
    nextSelected.delete(name);
    setSelectedIds(nextSelected);
    updateParentSelection(nextSelected, nextItems);
  };

  const uploadOne = useCallback(async (file) => {
    const isImage = IMAGE_EXTS.includes((file.name.split(".").pop() || "").toLowerCase());
    const id = `${file.name}-${Date.now()}`;
    
    // Add to items list as uploading
    setItems((prev) => [...prev, { id, name: file.name, status: "uploading" }]);

    const form = new FormData();
    form.append("file", file);
    if (isImage) {
      const caption = window.prompt(`Optional caption for "${file.name}" (leave blank to auto-caption via local vision model):`, "") || "";
      form.append("caption", caption);
    }

    try {
      // 1. Ingest the file physically
      const res = await fetch(`${apiUrl}/ingest/upload`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      let targetConversationId = conversationId;

      // 2. If logged in but no active conversation, create one!
      if (user && !targetConversationId) {
        try {
          const newChatRes = await fetch(`${apiUrl}/chats?title=${encodeURIComponent(file.name)}`, {
            method: "POST",
            credentials: "include"
          });
          if (newChatRes.ok) {
            const newChat = await newChatRes.json();
            targetConversationId = newChat.conversation_id;
          }
        } catch (chatErr) {
          console.error("Failed to auto-create conversation on upload:", chatErr);
        }
      }

      // 3. Associate file with conversation in database if conversation exists and user logged in
      if (user && targetConversationId) {
        try {
          await fetch(`${apiUrl}/chats/${targetConversationId}/files`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              filename: file.name,
              modality: data.modality,
              chunks: data.chunks_ingested
            })
          });
        } catch (assocErr) {
          console.error("Failed to associate file with conversation:", assocErr);
        }
      }

      // 4. Notify parent of conversation creation after file association is successfully saved
      if (user && targetConversationId && targetConversationId !== conversationId) {
        onConversationCreated(targetConversationId);
      }

      // Update upload status UI
      setItems((prev) => {
        const next = prev.map((it) => (it.id === id ? { ...it, status: "done", modality: data.modality, chunks: data.chunks_ingested } : it));
        
        // Auto-select the newly uploaded file
        setSelectedIds((prevSelected) => {
          const nextSelected = new Set(prevSelected);
          nextSelected.add(file.name);
          // Update selection in parent
          updateParentSelection(nextSelected, next);
          return nextSelected;
        });
        
        return next;
      });
    } catch (e) {
      setItems((prev) => prev.map((it) => (it.id === id ? { ...it, status: "error", error: e.message } : it)));
    }
  }, [apiUrl, user, conversationId, onConversationCreated, selectedIds]);

  const handleFiles = (fileList) => {
    Array.from(fileList).forEach(uploadOne);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  };

  return (
    <div className="card upload-panel">
      <h3>Ingest Documents {conversationId ? "in this Chat" : ""}</h3>
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
      >
        <span>Drop files here or click to browse</span>
        <span className="dropzone-hint">.txt .md .csv .pdf .png .jpg</span>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        style={{ display: "none" }}
        onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }}
      />

      {items.length > 0 && (
        <div className="upload-list">
          {items.map((it) => (
            <div
              key={it.id}
              className={`upload-item-row ${it.status === "done" ? "completed" : ""}`}
            >
              <div className="upload-item-left">
                {it.status === "done" && (
                  <input
                    type="checkbox"
                    className="file-select-checkbox"
                    checked={selectedIds.has(it.name)}
                    onChange={() => handleToggleSelect(it.name)}
                    onClick={(e) => e.stopPropagation()}
                    title="Include in query scope"
                  />
                )}
                <span className="upload-name" title={it.name}>{it.name}</span>
              </div>
              <div className="upload-item-right">
                {it.status === "uploading" && <span className="upload-status pending">ingesting...</span>}
                {it.status === "done" && (
                  <span className="upload-status ok">
                    <span className="tag">{it.modality}</span>{it.chunks} chunk{it.chunks === 1 ? "" : "s"}
                  </span>
                )}
                {it.status === "error" && <span className="upload-status err">{it.error}</span>}
                
                {it.status === "done" && (
                  <button
                    className="file-delete-btn"
                    onClick={(e) => handleDeleteFile(e, it.name, it.id)}
                    title="Remove file"
                  >
                    &times;
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
