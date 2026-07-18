import React, { useEffect, useRef } from "react";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

export default function GoogleSignInButton({ apiUrl, onSignedIn }) {
  const btnRef = useRef(null);

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    const handleCredential = async (response) => {
      const res = await fetch(`${apiUrl}/auth/google`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential: response.credential }),
      });
      if (res.ok) onSignedIn(await res.json());
    };

    const init = () => {
      if (!window.google || !btnRef.current) return;
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleCredential,
      });
      window.google.accounts.id.renderButton(btnRef.current, {
        theme: "filled_black",
        size: "medium",
        shape: "pill",
        text: "signin",
      });
    };

    if (window.google) {
      init();
    } else {
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.onload = init;
      document.body.appendChild(script);
    }
  }, [apiUrl, onSignedIn]);

  if (!GOOGLE_CLIENT_ID) {
    return (
      <span style={{ fontSize: 11, color: "var(--bamboo-light)", fontFamily: "JetBrains Mono, monospace" }}>
        set VITE_GOOGLE_CLIENT_ID to enable sign-in
      </span>
    );
  }

  return <div ref={btnRef} />;
}
