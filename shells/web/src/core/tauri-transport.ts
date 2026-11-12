// The desktop's carrier: the core runs in the Tauri process. Its lines come down one channel, given once;
// the page's lines go up one command each.
import { Channel, invoke } from "@tauri-apps/api/core";
import type { CoreStatus, Transport } from "./transport";

export class TauriTransport implements Transport {
  readonly #listening: Promise<void>;
  #onLine: (line: string) => void = () => {};

  constructor(onStatus: (status: CoreStatus) => void) {
    const channel = new Channel<string>((line) => this.#onLine(line));
    this.#listening = invoke<void>("core_listen", { channel });
    this.#listening.then(
      () => onStatus({ state: "ready" }),
      (e: unknown) => onStatus({ state: "failed", detail: String(e) }),
    );
  }

  send(line: string): void {
    // Chained on the same promise, so the lines leave in the order they were sent.
    void this.#listening.then(() => invoke<void>("core_send", { line }));
  }

  listen(onLine: (line: string) => void): void {
    this.#onLine = onLine;
  }
}
