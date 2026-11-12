// The browser's core: a dedicated worker running mm-wasm. Only a dedicated worker has the synchronous file
// handles the database's VFS needs, and here the core's work never holds up the page's frames.
import init, { Core, buildFixture, databaseExists, fixtureKeyHex, installStorage, sqliteMemoryUsed } from "../../wasm/mm_wasm.js";
import wasmUrl from "../../wasm/mm_wasm_bg.wasm?url";
import type { CoreStatus } from "./transport";

// What the page asks for: the database to open, and how big a demo mailbox to make if it is not there yet.
export interface Boot {
  database: string;
  messages: number;
}

// The first message is the boot; every later one is a line for the core.
export type ToWorker = { boot: Boot } | string;
// Lines from the core, and the worker's own status.
export type FromWorker = string | { status: CoreStatus };

interface WorkerScope {
  postMessage(message: FromWorker): void;
  onmessage: ((event: MessageEvent<ToWorker>) => void) | null;
}

const scope = self as unknown as WorkerScope & Record<string, unknown>;
let core: Core | undefined;
const early: string[] = [];
// The first lines are timed, for `just web measure`: the list's open and its first window.
let timed = 0;

scope.onmessage = (event) => {
  const data = event.data;
  if (typeof data !== "string") {
    void start(data.boot);
  } else if (core) {
    send(core, data);
  } else {
    early.push(data);
  }
};

function send(open: Core, line: string): void {
  if (timed >= 2) return open.send(line);
  const start = performance.now();
  open.send(line);
  performance.measure(`mailmind:line-${++timed}`, { start });
}

function status(state: CoreStatus): void {
  scope.postMessage({ status: state });
}

async function start(boot: Boot): Promise<void> {
  try {
    const wasm = await init({ module_or_path: wasmUrl });
    performance.mark("mailmind:wasm-ready");
    await installStorage();
    performance.mark("mailmind:storage-ready");
    if (!databaseExists(boot.database)) {
      status({ state: "building", messages: boot.messages });
      buildFixture(boot.database, boot.messages);
    }
    // Step 0 opens only the demo mailbox, whose key guards only fictional mail.
    core = Core.open(boot.database, fixtureKeyHex(), (line: string) => scope.postMessage(line));
    performance.mark("mailmind:core-open");
    // For `just web measure`, which reads it through Playwright's handle on this worker.
    scope.mailmindMemory = () => ({ wasm: wasm.memory.buffer.byteLength, sqlite: sqliteMemoryUsed() });
    status({ state: "ready" });
    for (const line of early.splice(0)) send(core, line);
  } catch (e) {
    status({ state: "failed", detail: e instanceof Error ? e.message : String(e) });
  }
}
