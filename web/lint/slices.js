// How the features may reach each other. Rules of our own rather than no-restricted-imports, since they judge where a
// path lands, however many ../ it takes to get there.
import { dirname, relative, resolve, sep } from "node:path";

const FEATURES = resolve(import.meta.dirname, "../src/features");

// The features, bottom to top. A feature imports only those before it, so no two can import each other, and a new one
// has to be given its place here.
export const ORDER = ["mailbox", "attention", "compose", "curation", "discovery", "assistant", "status", "settings", "reader"];

function inFeature(path) {
  const within = relative(FEATURES, path);
  if (within.startsWith("..")) return null;
  const [name, ...rest] = within.split(sep);
  return { name, file: rest.join("/") };
}

// Every way a file names another, types and re-exports included. `check` gets what it was named by, and where that lands.
function eachImport(context, check) {
  const judge = (source) => {
    const path = source?.value;
    if (typeof path !== "string" || !path.startsWith(".")) return;
    const there = inFeature(resolve(dirname(context.filename), path));
    if (there) check(source, there);
  };
  const fromSource = (node) => judge(node.source);
  return {
    ImportDeclaration: fromSource,
    ExportNamedDeclaration: fromSource,
    ExportAllDeclaration: fromSource,
    ImportExpression: fromSource,
    TSImportType: (node) => judge(node.argument?.literal),
  };
}

// A feature reaches another feature only through that feature's index.ts.
export const onlyThroughIndex = {
  meta: {
    type: "problem",
    schema: [],
    messages: { deep: "`{{from}}` reaches into `{{to}}`: import what it offers from its index.ts ({{door}})." },
  },
  create(context) {
    const mine = inFeature(context.filename);
    if (!mine) return {};
    return eachImport(context, (source, there) => {
      if (there.name === mine.name || ["", "index", "index.ts"].includes(there.file)) return;
      const door = `${"../".repeat(mine.file.split("/").length)}${there.name}`;
      context.report({ node: source, messageId: "deep", data: { from: mine.name, to: there.name, door } });
    });
  },
};

// A feature imports only the features before it in ORDER.
export const featureOrder = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      upward: "`{{from}}` comes before `{{to}}` in ORDER (web/lint/slices.js), so it can't import it: what both need belongs in the lower one, or in lib/.",
      unplaced: "`{{name}}` has no place in ORDER (web/lint/slices.js): add it after the features it imports.",
    },
  },
  create(context) {
    const mine = inFeature(context.filename);
    if (!mine) return {};
    const place = ORDER.indexOf(mine.name);
    if (place < 0) return { Program: (node) => context.report({ node, messageId: "unplaced", data: { name: mine.name } }) };
    return eachImport(context, (source, there) => {
      if (there.name === mine.name) return;
      const theirs = ORDER.indexOf(there.name);
      if (theirs < 0) context.report({ node: source, messageId: "unplaced", data: { name: there.name } });
      else if (theirs > place) context.report({ node: source, messageId: "upward", data: { from: mine.name, to: there.name } });
    });
  },
};
