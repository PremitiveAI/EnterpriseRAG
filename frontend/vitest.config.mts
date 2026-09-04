import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

/**
 * No @vitejs/plugin-react: it pulls a bundler that needs Node >= 20.12, and
 * this machine runs 20.9. The plugin only adds fast refresh, which tests do
 * not use — esbuild handles the JSX transform on its own.
 */
export default defineConfig({
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    // Only our own tests. Without this, vitest walks node_modules.
    include: ["src/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
