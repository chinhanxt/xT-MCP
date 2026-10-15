import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: { proxy: {
    "/ollama": { target: "http://127.0.0.1:11434", rewrite: p => p.replace(/^\/ollama/, "") },
    "/mcp": { target: "http://127.0.0.1:19100" },
  } },
});
