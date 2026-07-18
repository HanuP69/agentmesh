import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/auth": "http://nginx-edge:80",
      "/query": "http://nginx-edge:80",
      "/ingest": "http://nginx-edge:80",
      "/chats": "http://nginx-edge:80",
      "/status": "http://nginx-edge:80",
      "/stream": "http://nginx-edge:80",
    },
  },
});
