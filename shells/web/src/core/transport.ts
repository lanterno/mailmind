// How the page reaches the core: lines of JSON each way (mm_app::wire). The browser's worker and the desktop
// shell's channel each carry them; nothing above this file knows which.
export interface Transport {
  send(line: string): void;
  listen(onLine: (line: string) => void): void;
}

// What the carrier says about the core before it can answer: the demo mailbox being made, or a failure to start.
export type CoreStatus = { state: "starting" } | { state: "building"; messages: number } | { state: "ready" } | { state: "failed"; detail: string };
