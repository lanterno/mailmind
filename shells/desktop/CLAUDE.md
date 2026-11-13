# shells/desktop: MailMind for macOS, Windows and Linux

Tauri 2 around the web shell's page (`shells/web`): its dev server in `just desktop dev`, its `dist-desktop/` in a
build. The core runs natively in the app's process (`mm-app`, one thread for views), not in the page: the page's
desktop build carries no worker and no WebAssembly.

The page reaches the core through two commands and nothing else (`src-tauri/src/main.rs`):

- `core_listen(channel)` keeps the page's channel; every line the core sends goes down it.
- `core_send(line)` hands one JSON line of `mm_app::wire` to the core's thread, in order.

Step 0 opens only the demo mailbox: 100,000 fictional messages, made in the app's data folder on first launch
and opened with the fixture key, which guards only fictional mail.

`src-tauri` is not in the core's workspace (it has its own `[workspace]` and `Cargo.lock`) but builds into
`core/target` (`src-tauri/.cargo/config.toml`), with the core's compiler and formatting.

## Commands

Run from the repo root. They need Node and Rust; on Linux, WebKitGTK's development packages.

```bash
just desktop build      # the page, then the app in release, unbundled (core/target/release/mailmind-desktop)
just desktop bundle     # the installers for this system, under core/target/release/bundle
just desktop dev        # the app in debug, on the web shell's dev server
just desktop check      # rustfmt and clippy on the app's Rust
just desktop smoke      # the release app opens its list, scrolls nine tenths down and exits 0 once rows came
just desktop measure    # macOS: launch to first rows and idle memory, on the release .app
```

## Rules

- **Grant only `core_listen` and `core_send`.** No plugin (shell, fs, http, dialog, opener, ...), no `core:`
  permission. `build.rs` names the commands, so each needs a permission, and `capabilities/core.json` grants
  exactly these two to the one window.
- **The page decides nothing about mail.** The same rules as `shells/web/CLAUDE.md`: the page only draws what
  the core sends.
- **The content security policy stays script files only.** `app.security.csp` in `tauri.conf.json` matches the
  desktop policy in `shells/web/vite.config.ts`; change both together.
- **Keys never cross the page.** The core opens the database in this process; a real profile's key will come
  from the platform's key store here, never from the page.
- **The page's title is a signal.** It changes once the first rows are in the DOM; `src-tauri/src/measure.rs`
  reports that with `MAILMIND_MEASURE` set, for `just desktop measure`. With `MAILMIND_MEASURE=scroll` it then
  scrolls the list from the webview itself and exits with what came (`just desktop smoke`, which CI runs on every
  system). A window nobody sees, as on a locked Mac, may never scroll: run it with the screen on.
