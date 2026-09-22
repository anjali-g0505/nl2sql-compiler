import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately (uvicorn on :8000); proxy its routes so the
// browser sees one origin and no CORS setup is needed. In production FastAPI serves
// the built app itself, so the same relative URLs work.
const api = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/query": api,
      "/clarifications": api,
      "/health": api,
      "/admin": api,
    },
  },
});
