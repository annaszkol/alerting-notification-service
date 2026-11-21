
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev server: the API runs on :8000 (docker compose); /api and /ws are proxied so the browser sees one origin,
// exactly like nginx does in the container (web/nginx.conf).
const api = process.env.API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
