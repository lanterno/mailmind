// The rows a view's batches leave the page holding: the held range the core names, and nothing outside it,
// so memory stays the same at row 10 and at row 90,000.
import type { MessageRow } from "./generated/MessageRow";
import type { ViewBatch } from "./generated/ViewBatch";

export class ListRows {
  total = 0;
  #first = 0;
  #rows: (MessageRow | undefined)[] = [];

  apply(batch: ViewBatch): void {
    const rows = new Array<MessageRow | undefined>(batch.held_count);
    const start = Math.max(batch.held_first, this.#first);
    const end = Math.min(batch.held_first + batch.held_count, this.#first + this.#rows.length);
    for (let i = start; i < end; i++) rows[i - batch.held_first] = this.#rows[i - this.#first];
    for (const range of batch.ranges) {
      range.rows.forEach((row, k) => {
        const at = range.first + k - batch.held_first;
        if (at >= 0 && at < rows.length) rows[at] = row;
      });
    }
    this.total = batch.total;
    this.#first = batch.held_first;
    this.#rows = rows;
  }

  // The row at a position in the list, or undefined while the core has not sent it.
  row(index: number): MessageRow | undefined {
    return this.#rows[index - this.#first];
  }

  get held(): number {
    return this.#rows.length;
  }
}
