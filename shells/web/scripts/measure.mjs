// `just web measure`: the web shell's numbers, in headless Chromium, on the built page served by `vite preview`.
//
// It loads the page once (making the demo mailbox in OPFS if this browser profile has none), then reloads it
// RUNS times and reads the "mailmind:first-rows" mark: navigation to the first frame showing rows. It reads
// memory with the list open, and again after jumping far down: the page's and the worker's JS heaps and the
// WebAssembly memory (performance.measureUserAgentSpecificMemory), and the worker's own count of its memory.
// `node scripts/measure.mjs [messages]`; prints JSON.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright-core";

const PORT = 4173;
const RUNS = 7;
const ROW_HEIGHT = 64;
const messages = Number(process.argv[2] ?? 100000);
const url = `http://127.0.0.1:${PORT}/${messages === 100000 ? "" : `?messages=${messages}`}`;

const preview = spawn("npx", ["vite", "preview", "--host", "127.0.0.1", "--port", String(PORT), "--strictPort"], {
  cwd: join(import.meta.dirname, ".."),
  stdio: ["ignore", "ignore", "inherit"],
});
// A profile of its own, so the demo mailbox made here is the only data it holds, and goes with it.
const profile = mkdtempSync(join(tmpdir(), "mailmind-measure-"));
let context;
try {
  await serving(url);
  context = await chromium.launchPersistentContext(profile, {
    headless: true,
    // The full Chromium in its new headless mode: the headless shell has no measureUserAgentSpecificMemory.
    channel: "chromium",
    viewport: { width: 1200, height: 800 },
    args: ["--enable-blink-features=ForceEagerMeasureMemory", "--enable-precise-memory-info"],
  });
  const page = context.pages()[0] ?? (await context.newPage());
  const problems = [];
  page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
  page.on("pageerror", (e) => problems.push(e.message));

  let started = performance.now();
  await page.goto(url);
  await firstRows(page, 900_000);
  const firstLoadMs = performance.now() - started;

  const runs = [];
  const steps = [];
  for (let i = 0; i < RUNS; i++) {
    await page.reload();
    runs.push(await firstRows(page, 60_000));
    steps.push(await breakdown(page));
  }
  await page.waitForTimeout(1500);
  const afterOpen = await memory(page);

  // A scrollbar drag to near the end: the rows there arrive as one jump.
  const far = Math.floor(messages * 0.9);
  started = performance.now();
  await page.evaluate((top) => {
    const list = document.querySelector(".list");
    if (list) list.scrollTop = top;
  }, far * ROW_HEIGHT);
  await page.waitForSelector(`.row:not(.placeholder)[aria-posinset="${far + 1}"]`, { timeout: 30_000 });
  const jumpMs = performance.now() - started;
  await page.waitForTimeout(1500);
  const afterJump = await memory(page);
  if (process.env.MEASURE_SCREENSHOT) await page.screenshot({ path: process.env.MEASURE_SCREENSHOT });

  const sorted = [...runs].sort((a, b) => a - b);
  console.log(
    JSON.stringify(
      {
        messages,
        firstLoadIncludingFixtureMs: Math.round(firstLoadMs),
        firstRowsMs: { median: sorted[Math.floor(sorted.length / 2)], min: sorted[0], max: sorted.at(-1), runs },
        // Each step's time since navigation, in the run with the median first rows.
        stepsMs: steps[runs.indexOf(sorted[Math.floor(sorted.length / 2)])],
        jumpToRowsMs: Math.round(jumpMs),
        afterOpen,
        afterJump,
        crossOriginIsolated: await page.evaluate(() => crossOriginIsolated),
        problems,
      },
      null,
      2,
    ),
  );
  if (problems.length) process.exitCode = 1;
} finally {
  await context?.close();
  preview.kill();
  rmSync(profile, { recursive: true, force: true });
}

async function serving(at) {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(at)).ok) return;
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("vite preview did not start");
}

// Milliseconds from navigation to the first frame with rows, as the page marked it.
async function firstRows(page, timeout) {
  const handle = await page.waitForFunction(
    () => performance.getEntriesByName("mailmind:first-rows")[0]?.startTime,
    null,
    { timeout, polling: 50 },
  );
  return Math.round(await handle.jsonValue());
}

// When each step of a load happened, in milliseconds since navigation: the worker's marks and its first two lines
// (the list's open, then its first window, each with how long the core took), and the page's marks.
async function breakdown(page) {
  const pageOrigin = await page.evaluate(() => performance.timeOrigin);
  const worker = page.workers()[0];
  const entries = worker
    ? await worker.evaluate(() => ({
        origin: performance.timeOrigin,
        marks: performance.getEntries().map((e) => [e.name, e.startTime, e.duration]),
      }))
    : { origin: pageOrigin, marks: [] };
  const shift = entries.origin - pageOrigin;
  const steps = { workerStarted: Math.round(shift) };
  for (const [name, start, duration] of entries.marks) {
    if (!name.startsWith("mailmind:")) continue;
    steps[name.slice(9)] = duration ? { at: Math.round(shift + start), took: Math.round(duration) } : Math.round(shift + start);
  }
  const marks = await page.evaluate(() => performance.getEntriesByType("mark").map((e) => [e.name, e.startTime]));
  for (const [name, start] of marks) if (name.startsWith("mailmind:")) steps[name.slice(9)] = Math.round(start);
  return steps;
}

async function memory(page) {
  const measured = await page.evaluate(() => performance.measureUserAgentSpecificMemory());
  const byScope = {};
  for (const entry of measured.breakdown) {
    for (const where of entry.attribution) byScope[where.scope] = (byScope[where.scope] ?? 0) + entry.bytes;
  }
  const worker = page.workers()[0];
  const core = worker ? await worker.evaluate(() => globalThis.mailmindMemory?.()) : undefined;
  const mb = (n) => Math.round((n / 1048576) * 10) / 10;
  return {
    totalMb: mb(measured.bytes),
    byScopeMb: Object.fromEntries(Object.entries(byScope).map(([k, v]) => [k, mb(v)])),
    wasmMemoryMb: core ? mb(core.wasm) : undefined,
    sqliteMb: core ? mb(core.sqlite) : undefined,
    pageHeapMb: mb(await page.evaluate(() => performance.memory.usedJSHeapSize)),
  };
}
