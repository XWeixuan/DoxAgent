import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: {
      port: 5174,
      strictPort: true,
      proxy: {
        "/api/doxagent/v2": {
          target: env.V2_API_TARGET || "http://127.0.0.1:8002",
          changeOrigin: true,
        },
      },
    },
    build: {
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            "react-runtime": ["react", "react-dom", "react-router-dom"],
            "auth-runtime": ["@supabase/supabase-js"],
            "query-runtime": ["@tanstack/react-query"],
            "wire-validation": ["ajv"],
          },
        },
      },
    },
  };
});
