// What `just check-web` holds web/src to.
//
// package.json's override lets eslint-plugin-jsx-a11y sit on ESLint 10: its peer range stops at 9, which is out of
// support, and it calls nothing 10 removed. @babel/core is pinned there because eslint-plugin-react-hooks asks for
// Babel 7, and npm would otherwise move the build's compiler onto that.
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "mailmind-typescript-eslint";
import { onlyThroughIndex } from "./lint/slices.js";

// HTML put straight into the DOM. A mail body goes into MailFrame's sandboxed frame and nowhere else, so that frame is
// the one module that may do this.
const HTML_SINK = "Writes HTML straight into the DOM, which only MailFrame.tsx may do: its sandboxed frame is the wall around a message's HTML.";
const HTML_SINKS = [
  "JSXAttribute[name.name=/^(dangerouslySetInnerHTML|srcDoc|srcdoc)$/]",
  "Property[key.name=/^(dangerouslySetInnerHTML|srcDoc|srcdoc)$/]",
  "MemberExpression[property.name=/^(innerHTML|outerHTML|srcdoc)$/]",
  "MemberExpression[property.value=/^(innerHTML|outerHTML|srcdoc)$/]",
  "CallExpression[callee.property.name=/^(insertAdjacentHTML|setHTMLUnsafe|createContextualFragment)$/]",
  "CallExpression[callee.property.name='setAttribute'][arguments.0.value=/^srcdoc$/i]",
].map((selector) => ({ selector, message: HTML_SINK }));

// The modules with no DOM, no stores and no fetch, which run anywhere and are tested without a page. model.ts is here
// because the composer's machine stands on it.
const PURE = ["src/**/*.machine.ts", "src/features/compose/machine.ts", "src/features/compose/model.ts", "src/features/compose/sendGuard.ts", "src/features/compose/draftResume.ts", "src/features/compose/linkClean.ts", "src/features/mailbox/linkRisk.ts"];
const IMPURE_GLOBALS = [
  "window", "self", "globalThis", "document", "navigator", "location", "history", "screen", "alert", "confirm", "prompt", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame", "requestIdleCallback", "ResizeObserver", "MutationObserver", "IntersectionObserver", "DOMParser", "Element", "HTMLElement", "Node",
  "localStorage", "sessionStorage", "indexedDB", "caches",
  "fetch", "XMLHttpRequest", "EventSource", "WebSocket", "BroadcastChannel",
].map((name) => ({ name, message: "A pure module has no DOM, no stores and no fetch: have its caller pass in what it needs." }));

export default defineConfig([
  { ignores: ["*", "!src/"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, tseslint.configs.recommended, reactHooks.configs.flat.recommended, jsxA11y.flatConfigs.recommended],
    plugins: { slices: { rules: { "only-through-index": onlyThroughIndex } } },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: {
      // The plugin's recommended set only warns on these, and a warning here would pass the check.
      "react-hooks/exhaustive-deps": "error",
      "react-hooks/incompatible-library": "error",
      "react-hooks/unsupported-syntax": "error",
      // The compiler quietly skips a component that disables exhaustive-deps or rules-of-hooks: useEffectEvent is the way
      // for what an effect reads but doesn't run for.
      "react-hooks/rule-suppression": "error",
      // React Aria's autoFocus is its own prop, moving focus within a dialog or menu it manages, not the DOM's attribute.
      "jsx-a11y/no-autofocus": ["error", { ignoreNonDOM: true }],
      "no-restricted-syntax": ["error", ...HTML_SINKS],
      "slices/only-through-index": "error",
    },
  },
  { files: ["src/features/mailbox/MailFrame.tsx"], rules: { "no-restricted-syntax": "off" } },
  {
    files: PURE,
    rules: {
      "no-restricted-globals": ["error", ...IMPURE_GLOBALS],
      "@typescript-eslint/no-restricted-imports": ["error", {
        patterns: [{
          regex: "^(?!\\./(machine|model|sendGuard|draftResume|linkClean|linkRisk)$)",
          allowTypeImports: true,
          message: "A pure module imports only other pure modules, and types: this could bring in the DOM, a store or a fetch.",
        }],
      }],
    },
  },
]);
