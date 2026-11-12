// The page's client of the core: numbers requests, matches replies to them, and hands each view its batches.
// It holds no mail and decides nothing about it.
import type { Call } from "./generated/Call";
import type { ListQuery } from "./generated/ListQuery";
import type { Outcome } from "./generated/Outcome";
import type { Outgoing } from "./generated/Outgoing";
import type { Refusal } from "./generated/Refusal";
import type { Request } from "./generated/Request";
import type { ViewBatch } from "./generated/ViewBatch";
import type { Transport } from "./transport";

export class Refused extends Error {
  readonly refusal: Refusal;

  constructor(refusal: Refusal) {
    super(describe(refusal));
    this.refusal = refusal;
  }
}

// The core's words for a refusal, which never hold a person's data.
export function describe(refusal: Refusal): string {
  switch (refusal.kind) {
    case "key_does_not_fit":
      return "the database key opens nothing in this file";
    case "not_found":
      return `${refusal.what} is not there`;
    case "invalid":
      return `invalid request: ${refusal.detail}`;
    case "storage":
      return `the store failed: ${refusal.detail}`;
  }
}

export interface ViewListener {
  batch(batch: ViewBatch): void;
  refused(refusal: Refusal): void;
}

interface Waiting {
  resolve(outcome: Outcome): void;
  reject(error: Error): void;
}

export class CoreClient {
  readonly #transport: Transport;
  readonly #waiting = new Map<number, Waiting>();
  readonly #views = new Map<number, ViewListener>();
  #next = 1;
  // A refusal under no request the client made: a line the core could not read.
  onStray: (refusal: Refusal) => void = () => {};

  constructor(transport: Transport) {
    this.#transport = transport;
    transport.listen((line) => this.#receive(line));
  }

  call(call: Call): Promise<Outcome> {
    const id = this.#next++;
    const request: Request = { id, ...call };
    return new Promise((resolve, reject) => {
      this.#waiting.set(id, { resolve, reject });
      this.#transport.send(JSON.stringify(request));
    });
  }

  // Open a list. Its batches start once the page says which rows are visible.
  async openList(query: ListQuery, listener: ViewListener): Promise<{ view: number; total: number }> {
    const outcome = await this.call({ method: "open_list", params: { query } });
    if (outcome.kind !== "opened") throw new Error("the core answered an open with no view");
    this.#views.set(outcome.view, listener);
    return { view: outcome.view, total: outcome.total };
  }

  setVisible(view: number, first: number, count: number): Promise<Outcome> {
    return this.call({ method: "set_visible", params: { view, first, count } });
  }

  closeView(view: number): Promise<Outcome> {
    this.#views.delete(view);
    return this.call({ method: "close_view", params: { view } });
  }

  #receive(line: string): void {
    const message = JSON.parse(line) as Outgoing;
    switch (message.kind) {
      case "reply": {
        const waiting = this.#take(message.id);
        waiting?.resolve(message.outcome);
        return;
      }
      case "refused": {
        const waiting = this.#take(message.id);
        if (waiting) waiting.reject(new Refused(message.refusal));
        else this.onStray(message.refusal);
        return;
      }
      case "batch":
        this.#views.get(message.view)?.batch(message.batch);
        return;
      case "view_refused":
        this.#views.get(message.view)?.refused(message.refusal);
        return;
    }
  }

  #take(id: number): Waiting | undefined {
    const waiting = this.#waiting.get(id);
    this.#waiting.delete(id);
    return waiting;
  }
}
