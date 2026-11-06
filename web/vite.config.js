// `just build-web`: the page's files, named by their content, in mailmind/http/dist. The scripts stay classic
// scripts, with their top-level names shared and run in order, so they are copied as they are, not bundled.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite";

const CLASSIC = ["theme", "app"];

function classicScripts() {
  return {
    name: "mailmind-classic-scripts",
    apply: "build",
    enforce: "post",
    generateBundle(_options, bundle) {
      const page = bundle["index.html"];
      for (const name of CLASSIC) {
        const ref = this.emitFile({
          type: "asset",
          name: `${name}.js`,
          source: readFileSync(resolve(import.meta.dirname, `assets/legacy/${name}.js`)),
        });
        page.source = page.source.replace(`/assets/legacy/${name}.js`, `/${this.getFileName(ref)}`);
      }
    },
  };
}

export default defineConfig({
  root: import.meta.dirname,
  logLevel: "warn",
  build: { outDir: "../mailmind/http/dist", emptyOutDir: true, assetsInlineLimit: 0, cssMinify: false },
  plugins: [classicScripts()],
});
