// A feature reaches another feature only through that feature's index.ts. A rule of our own rather than
// no-restricted-imports, since it judges where a path lands, however many ../ it takes to get there.
import { dirname, relative, resolve, sep } from "node:path";

const FEATURES = resolve(import.meta.dirname, "../src/features");

function inFeature(path) {
  const within = relative(FEATURES, path);
  if (within.startsWith("..")) return null;
  const [name, ...rest] = within.split(sep);
  return { name, file: rest.join("/") };
}

export const onlyThroughIndex = {
  meta: {
    type: "problem",
    schema: [],
    messages: { deep: "`{{from}}` reaches into `{{to}}`: import what it offers from its index.ts ({{door}})." },
  },
  create(context) {
    const mine = inFeature(context.filename);
    if (!mine) return {};
    const check = (source) => {
      const path = source?.value;
      if (typeof path !== "string" || !path.startsWith(".")) return;
      const there = inFeature(resolve(dirname(context.filename), path));
      if (!there || there.name === mine.name || ["", "index", "index.ts"].includes(there.file)) return;
      const door = `${"../".repeat(mine.file.split("/").length)}${there.name}`;
      context.report({ node: source, messageId: "deep", data: { from: mine.name, to: there.name, door } });
    };
    const fromSource = (node) => check(node.source);
    return {
      ImportDeclaration: fromSource,
      ExportNamedDeclaration: fromSource,
      ExportAllDeclaration: fromSource,
      ImportExpression: fromSource,
      TSImportType: (node) => check(node.argument?.literal),
    };
  },
};
