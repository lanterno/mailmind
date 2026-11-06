// `just gen-web`: the pure TypeScript modules as a classic script the page runs with no build. Types are erased and
// nothing else changes. With --check it says whether assets/legacy/pure.js is what the sources give, and writes nothing.
import { readFileSync, writeFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { resolve } from "node:path";

const WEB = resolve(import.meta.dirname, "..");
const OUT = resolve(WEB, "assets/legacy/pure.js");
const BLOCKS = [["draft-resume", "src/features/compose/draftResume.ts"], ["send-guard", "src/features/compose/sendGuard.ts"]];

function block([name, path]) {
  const js = stripTypeScriptTypes(readFileSync(resolve(WEB, path), "utf-8"), { mode: "strip" })
    .replace(/^export /gm, "")
    .replace(/^\s*\n/gm, "");
  return `/* ${name}:pure:start */\n${js}/* ${name}:pure:end */\n`;
}

const text = `// Written by \`just gen-web\` from web/src/compose: edit those.\n"use strict";\n${BLOCKS.map(block).join("")}`;
if (process.argv.includes("--check")) {
  if (readFileSync(OUT, "utf-8") !== text) {
    console.error("web/assets/legacy/pure.js is out of step with web/src: run `just gen-web`");
    process.exit(1);
  }
} else {
  writeFileSync(OUT, text);
}
