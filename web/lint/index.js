// typescript-eslint parses with TypeScript's JavaScript API, which TypeScript 7 doesn't have, and wants a TypeScript
// below 6.1. So this folder is a workspace holding it and a TypeScript 6 of its own, and `tsc` stays on the 7 in
// web/package.json. Its ts-api-utils takes any TypeScript, so npm hoists that up beside the 7, and it is pointed back
// here. All of this goes when typescript-eslint supports TypeScript 7.
import { createRequire, registerHooks } from "node:module";

const six = createRequire(import.meta.url).resolve("typescript");
registerHooks({
  resolve(specifier, context, next) {
    return next(specifier === "typescript" && context.parentURL?.includes("/ts-api-utils/") ? six : specifier, context);
  },
});

export default (await import("typescript-eslint")).default;
