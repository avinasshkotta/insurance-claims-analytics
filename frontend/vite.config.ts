import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API target is configurable so the dev server can follow the backend port.
// Default 8010 because 8000 is often occupied on this machine.
const target = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
      },
    },
  },
});
