// `just build-web`: the app and its files, named by their content, in mailmind/http/dist. theme.js stays a classic script,
// so it runs before the first paint, and is copied as it is, not bundled.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const CLASSIC = ["theme"];

function classicScripts() {
  return {
    name: "mailmind-classic-scripts",
    apply: "build",
    enforce: "post",
    generateBundle(_options, bundle) {
      const pages = Object.values(bundle).filter((f) => f.type === "asset" && f.fileName.endsWith(".html"));
      for (const name of CLASSIC) {
        const url = `/assets/${name}.js`;
        if (!pages.some((p) => String(p.source).includes(url))) continue;
        const ref = this.emitFile({
          type: "asset",
          name: `${name}.js`,
          source: readFileSync(resolve(import.meta.dirname, `assets/${name}.js`)),
        });
        for (const page of pages) page.source = String(page.source).replace(url, `/${this.getFileName(ref)}`);
      }
    },
  };
}

export default defineConfig({
  root: import.meta.dirname,
  logLevel: "warn",
  build: {
    outDir: "../mailmind/http/dist",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    cssMinify: false,
    rollupOptions: { input: { index: resolve(import.meta.dirname, "index.html") } },
  },
  plugins: [react(), babel({ presets: [reactCompilerPreset()] }), classicScripts()],
  test: { include: ["src/**/*.test.{ts,tsx}"] },
});
