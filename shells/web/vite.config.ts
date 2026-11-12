// The web shell's build. `vite build` makes the browser's page (dist/), with the worker and the core's
// WebAssembly; `vite build --mode desktop` makes the page the desktop shell wraps (dist-desktop/), with neither:
// there the core runs in the Tauri process.
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

// Script files from this origin and nothing else. The browser's page also compiles the core's WebAssembly in its
// worker; the desktop's page has neither, and reaches Tauri's IPC instead, which is a scheme of its own. Keep it
// the same as app.security.csp in shells/desktop/src-tauri/tauri.conf.json.
function policy(mode: string): string {
  const desktop = mode === "desktop";
  return [
    "default-src 'self'",
    desktop ? "script-src 'self'" : "script-src 'self' 'wasm-unsafe-eval'",
    desktop ? "worker-src 'none'" : "worker-src 'self'",
    "style-src 'self'",
    desktop ? "connect-src 'self' ipc: http://ipc.localhost" : "connect-src 'self'",
    "img-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

// The policy goes into the page as a meta tag, so a static host and the desktop shell carry it too.
function contentSecurityPolicy(mode: string): Plugin {
  return {
    name: "mailmind-csp",
    transformIndexHtml: () => [
      {
        tag: "meta",
        attrs: { "http-equiv": "Content-Security-Policy", content: policy(mode) },
        injectTo: "head-prepend",
      },
    ],
  };
}

// Cross-origin isolation lets `just web measure` count the worker's heap and the WebAssembly memory with
// performance.measureUserAgentSpecificMemory.
const isolated = { "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp" };

export default defineConfig(({ mode }) => ({
  root: import.meta.dirname,
  logLevel: "warn",
  resolve: {
    alias: {
      "#transport": resolve(import.meta.dirname, `src/core/connect-${mode === "desktop" ? "desktop" : "web"}.ts`),
    },
  },
  oxc: { jsx: { runtime: "automatic" } },
  build: {
    outDir: mode === "desktop" ? "dist-desktop" : "dist",
    emptyOutDir: true,
    target: "es2022",
    // Nothing becomes a data: URL: the policy allows files from this origin only.
    assetsInlineLimit: 0,
  },
  worker: { format: "es" },
  server: { port: 5173, strictPort: true, headers: isolated },
  preview: { port: 4173, strictPort: true, headers: isolated },
  plugins: [contentSecurityPolicy(mode)],
  test: { include: ["src/**/*.test.ts"] },
}));
