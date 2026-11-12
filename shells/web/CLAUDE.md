# shells/web: the browser's MailMind

A bare React page that shows the inbox, and the Rust core running under it in the browser. The core is
`core/crates/mm-wasm`, compiled to WebAssembly and run in a dedicated worker, with the encrypted database in the
origin's private file system (OPFS, through SQLite's "sahpool" VFS). The page talks to the core only in the JSON
lines of `mm_app::wire`, through `src/core/client.ts` over a `Transport`:

- `WorkerTransport`: postMessage to and from the worker (`src/core/worker.ts`).
- `TauriTransport`: the desktop shell's channel and commands (`shells/desktop`).

The page picks Tauri when it runs inside it, and the worker otherwise. `vite build --mode desktop` makes the
desktop's page, which has neither the worker nor the WebAssembly (`#transport` points at `connect-desktop.ts`).

Step 0 opens only the demo mailbox: 100,000 fictional messages, made in OPFS on first load (`?messages=N` makes
and opens another size).

## Commands

Run from the repo root. They need Node, and for the WebAssembly a clang that targets wasm32 (Apple's does not:
unpack a wasi-sdk release under `~/.local/share/wasi-sdk`, or set `WASI_SDK_PATH`; on Ubuntu, apt's `clang` and
`llvm` work) and `wasm-bindgen-cli` at the version `core/Cargo.lock` holds (`just web wasm` says which).

```bash
just web build          # mm-wasm, then the page, into dist/
just web dev            # the page on http://localhost:5173
just web test           # tsc, and the client's handling of the core's lines (vitest)
just web check          # clippy on mm-wasm for wasm32, then test
just web measure        # first rows, memory and the WebAssembly's size, in headless Chromium
just web gen            # the TypeScript types of the contract, from the core (ts-rs)
```

## Rules

- **The page decides nothing about mail.** What a list holds, its order and what each row says come from the
  core. The page keeps only the rows the core says to hold, draws them, and says which rows are visible.
- **`src/core/generated/` is never edited by hand.** `just web gen` writes it from `mm-app` and `mm-kernel`;
  commit its diff on its own. A contract change starts in `mm-app` (see `core/CLAUDE.md`).
- **The content security policy stays script files only:** no inline script, no event-handler attribute, no
  `eval`, no `data:` script. The page's policy is in `vite.config.ts` (the browser's adds `'wasm-unsafe-eval'` for
  the worker's WebAssembly; the desktop's adds Tauri's IPC and matches `tauri.conf.json`). Styles come from
  `src/styles.css` through a `<link>`, never a `<style>` block.
- **No row numbers, no keys in the page's code.** Rows carry the message key. The demo's database key is the
  fixture key, which the worker asks the core for and which guards only fictional mail.
- **The list is virtualized.** One fixed row height, a node only for the rows on screen, the visible range sent
  once a frame at most. Keep memory flat from row 10 to row 90,000.
