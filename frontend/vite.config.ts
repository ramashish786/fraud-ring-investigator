import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Vite server proxies /api to the FastAPI app, so the browser sees one origin
// (no CORS). In Docker, nginx does the same (see nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": process.env.API_URL ?? "http://localhost:8000" } },
  preview: { port: 4173, proxy: { "/api": process.env.API_URL ?? "http://localhost:8000" } },
});
