import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    chunkSizeWarningLimit: 800,
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-charts": ["recharts"],
          "vendor-markdown": ["react-markdown", "remark-gfm", "react-syntax-highlighter"],
          "vendor-query": ["@tanstack/react-query"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api/bifrost": {
        target: "http://localhost:8080",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/bifrost/, ""),
      },
    },
  },
})
