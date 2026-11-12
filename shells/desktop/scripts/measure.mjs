// `just desktop measure` (macOS): the desktop shell's numbers, on the release bundle that `just desktop bundle`
// makes. Each run launches the app through LaunchServices (`open`), as a person does, and reads:
// - launch to first rows: from `open` to the page's title change once its first rows are in the DOM (the app
//   prints it on standard error with MAILMIND_MEASURE set), and the app's own count from the start of main;
// - memory, idle with the list open: the physical footprint and resident size of the app's process and
//   of the WebKit processes that started with it (WebContent, Networking, GPU), from `top` and `ps`.
// The first launch makes the 100,000-message demo mailbox if the app's data folder has none; it is not counted.
// While the screen is locked a window paints nothing, so rows reach the DOM but no frame shows them.
// `node scripts/measure.mjs [runs]`; prints JSON.
import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const RUNS = Number(process.argv[2] ?? 5);
const IDLE_MS = 5000;
const app = resolve(import.meta.dirname, "../../../core/target/release/bundle/macos/MailMind.app");
if (!existsSync(app)) throw new Error(`no app at ${app}: run just desktop bundle`);
const binary = join(app, "Contents/MacOS/mailmind-desktop");
const logs = mkdtempSync(join(tmpdir(), "mailmind-desktop-measure-"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function processes() {
  const out = execFileSync("ps", ["-axo", "pid=,rss=,command="], { encoding: "utf8" });
  return out
    .split("\n")
    .map((line) => line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/))
    .filter(Boolean)
    .map(([, pid, rss, command]) => ({ pid: Number(pid), rssKb: Number(rss), command }));
}

// The memory Activity Monitor shows for a process (its physical footprint), as `top` reports it.
function footprintMb(pid) {
  try {
    const out = execFileSync("top", ["-l", "1", "-pid", String(pid), "-stats", "pid,mem"], { encoding: "utf8" });
    const m = out.trim().split("\n").at(-1).match(/^\d+\s+([\d.]+)([KMG])/);
    if (!m) return undefined;
    return Number(m[1]) * { K: 1 / 1024, M: 1, G: 1024 }[m[2]];
  } catch {
    return undefined;
  }
}

const webkit = (p) => /com\.apple\.WebKit\.(WebContent|Networking|GPU)/.exec(p.command)?.[1];

async function launch(run) {
  const before = new Set(processes().map((p) => p.pid));
  const log = join(logs, `run-${run}.log`);
  const started = performance.now();
  const open = spawn("open", ["-n", "-W", app, "--env", "MAILMIND_MEASURE=1", "--stderr", log, "--stdout", "/dev/null"]);
  let line;
  for (let waited = 0; waited < 600_000 && !line; waited += 10) {
    await sleep(10);
    line = existsSync(log) ? /mailmind-measure rows-committed (\d+) ms/.exec(readFileSync(log, "utf8")) : null;
  }
  const launchMs = performance.now() - started;
  await sleep(IDLE_MS);
  const now = processes().filter((p) => !before.has(p.pid));
  const mine = now.filter((p) => p.command.startsWith(binary) || webkit(p));
  const parts = mine.map((p) => ({
    process: p.command.startsWith(binary) ? "app" : webkit(p),
    footprintMb: footprintMb(p.pid),
    rssMb: Math.round(p.rssKb / 102.4) / 10,
  }));
  for (const p of now.filter((p) => p.command.startsWith(binary))) process.kill(p.pid, "SIGTERM");
  await new Promise((r) => open.on("exit", r));
  await sleep(1500);
  const sum = (key, names) => Math.round(parts.filter((p) => names.includes(p.process)).reduce((n, p) => n + (p[key] ?? 0), 0) * 10) / 10;
  return {
    launchToRowsMs: line ? Math.round(launchMs) : undefined,
    mainToRowsMs: line ? Number(line[1]) : undefined,
    footprintMb: sum("footprintMb", ["app", "WebContent", "Networking"]),
    footprintWithGpuMb: sum("footprintMb", ["app", "WebContent", "Networking", "GPU"]),
    rssMb: sum("rssMb", ["app", "WebContent", "Networking"]),
    parts,
  };
}

try {
  const first = await launch(0);
  const runs = [];
  for (let i = 1; i <= RUNS; i++) runs.push(await launch(i));
  const median = (key) => [...runs.map((r) => r[key])].sort((a, b) => a - b)[Math.floor(runs.length / 2)];
  console.log(
    JSON.stringify(
      {
        app,
        firstLaunch: first,
        median: {
          launchToRowsMs: median("launchToRowsMs"),
          mainToRowsMs: median("mainToRowsMs"),
          footprintMb: median("footprintMb"),
          footprintWithGpuMb: median("footprintWithGpuMb"),
          rssMb: median("rssMb"),
        },
        runs,
      },
      null,
      2,
    ),
  );
} finally {
  rmSync(logs, { recursive: true, force: true });
}
