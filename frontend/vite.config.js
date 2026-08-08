import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API base is configurable so the same build works in dev (localhost:8000)
// and in the deployed compose stack (reverse-proxied). CORS on the API already
// allows the dev origin (http://localhost:5173).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
