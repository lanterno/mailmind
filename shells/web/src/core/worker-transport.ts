// The browser's carrier: lines to and from the core's dedicated worker, by postMessage.
import type { CoreStatus, Transport } from "./transport";
import type { Boot, FromWorker, ToWorker } from "./worker";

export class WorkerTransport implements Transport {
  readonly #worker: Worker;
  #onLine: (line: string) => void = () => {};

  constructor(boot: Boot, onStatus: (status: CoreStatus) => void) {
    this.#worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module", name: "mailmind-core" });
    this.#worker.onmessage = (event: MessageEvent<FromWorker>) => {
      if (typeof event.data === "string") this.#onLine(event.data);
      else onStatus(event.data.status);
    };
    this.#worker.onerror = (event) => onStatus({ state: "failed", detail: event.message || "the core's worker stopped" });
    this.#post({ boot });
  }

  send(line: string): void {
    this.#post(line);
  }

  listen(onLine: (line: string) => void): void {
    this.#onLine = onLine;
  }

  #post(message: ToWorker): void {
    this.#worker.postMessage(message);
  }
}
