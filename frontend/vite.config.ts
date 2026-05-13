import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Deployed at https://jy1529098645-gif.github.io/xhsAccountRise/ — keep base
// in sync. For local dev (vite) base resolves to "/".
const base = process.env.VITE_BASE ?? "/xhsAccountRise/";

export default defineConfig({
  plugins: [react()],
  base,
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
});
