// The client's handling of the core's messages, on lines mm-daemon sent for a 3-message fixture
// (`just fixtures` then mm-daemon). Each line is also written out as an Outgoing, so tsc fails if the generated
// types stop matching what the core sends.
import { describe as group, expect, test } from "vitest";
import { CoreClient, Refused } from "./client";
import type { Outgoing } from "./generated/Outgoing";
import type { ViewBatch } from "./generated/ViewBatch";
import { ListRows } from "./list-rows";
import type { Transport } from "./transport";

const OPENED = `{"kind":"reply","id":1,"outcome":{"kind":"opened","view":1,"total":3}}`;
const DONE = `{"kind":"reply","id":2,"outcome":{"kind":"done"}}`;
const NO_VIEW = `{"kind":"refused","id":3,"refusal":{"kind":"not_found","what":"this view"}}`;
const NOT_JSON = `{"kind":"refused","id":0,"refusal":{"kind":"invalid","detail":"not a request this core knows (line 1, column 2)"}}`;
const BATCH = `{"kind":"batch","view":1,"batch":{"total":3,"held_first":0,"held_count":3,"ranges":[{"first":0,"rows":[{"key":1,"sender":"Greta Dorsey","subject":"Garden plot confirmed","snippet":"We are still waiting on two of the quotes. We are still waiting on two of the quotes.","date":1793490512,"unread":false,"flagged":false,"attachment":false},{"key":2,"sender":"Ada Hale","subject":"Trip to the coast (updated)","snippet":"We are still waiting on two of the quotes. We are still waiting on two of the quotes. Bring a jacket, it gets cold by the water in the eveni","date":1793489967,"unread":false,"flagged":false,"attachment":true},{"key":3,"sender":"Femi Quist","subject":"Quarterly plan questions","snippet":"Thanks again for sorting this out so quickly. I moved the meeting notes into the shared folder.","date":1793488727,"unread":false,"flagged":false,"attachment":false}]}]}}`;

const TYPED: Outgoing[] = [
  { kind: "reply", id: 1, outcome: { kind: "opened", view: 1, total: 3 } },
  { kind: "reply", id: 2, outcome: { kind: "done" } },
  { kind: "refused", id: 3, refusal: { kind: "not_found", what: "this view" } },
  { kind: "refused", id: 0, refusal: { kind: "invalid", detail: "not a request this core knows (line 1, column 2)" } },
  {
    kind: "batch",
    view: 1,
    batch: {
      total: 3,
      held_first: 0,
      held_count: 3,
      ranges: [
        {
          first: 0,
          rows: [
            { key: 1, sender: "Greta Dorsey", subject: "Garden plot confirmed", snippet: "We are still waiting on two of the quotes. We are still waiting on two of the quotes.", date: 1793490512, unread: false, flagged: false, attachment: false },
            { key: 2, sender: "Ada Hale", subject: "Trip to the coast (updated)", snippet: "We are still waiting on two of the quotes. We are still waiting on two of the quotes. Bring a jacket, it gets cold by the water in the eveni", date: 1793489967, unread: false, flagged: false, attachment: true },
            { key: 3, sender: "Femi Quist", subject: "Quarterly plan questions", snippet: "Thanks again for sorting this out so quickly. I moved the meeting notes into the shared folder.", date: 1793488727, unread: false, flagged: false, attachment: false },
          ],
        },
      ],
    },
  },
];

const INBOX = { accounts: ["work@example.test", "home@example.test", "club@example.test"], folder: "INBOX" };

class FakeTransport implements Transport {
  sent: string[] = [];
  #onLine: (line: string) => void = () => {};
  send(line: string): void {
    this.sent.push(line);
  }
  listen(onLine: (line: string) => void): void {
    this.#onLine = onLine;
  }
  deliver(line: string): void {
    this.#onLine(line);
  }
}

test("the typed samples are the lines the core sent", () => {
  expect([OPENED, DONE, NO_VIEW, NOT_JSON, BATCH].map((line) => JSON.parse(line))).toEqual(TYPED);
});

group("CoreClient", () => {
  test("sends requests the core reads, and opens a view from its reply", async () => {
    const transport = new FakeTransport();
    const client = new CoreClient(transport);
    const batches: ViewBatch[] = [];
    const opening = client.openList(INBOX, { batch: (b) => batches.push(b), refused: () => {} });
    // The same line mm-app's wire tests send.
    expect(transport.sent).toEqual([
      `{"id":1,"method":"open_list","params":{"query":{"accounts":["work@example.test","home@example.test","club@example.test"],"folder":"INBOX"}}}`,
    ]);
    transport.deliver(OPENED);
    expect(await opening).toEqual({ view: 1, total: 3 });

    const showing = client.setVisible(1, 0, 3);
    expect(transport.sent[1]).toBe(`{"id":2,"method":"set_visible","params":{"view":1,"first":0,"count":3}}`);
    transport.deliver(BATCH);
    transport.deliver(DONE);
    expect(await showing).toEqual({ kind: "done" });
    expect(batches.map((b) => b.held_count)).toEqual([3]);
  });

  test("a refusal rejects the request it answers, and one under no request goes to onStray", async () => {
    const transport = new FakeTransport();
    const client = new CoreClient(transport);
    const strays: string[] = [];
    client.onStray = (refusal) => strays.push(refusal.kind);
    client.call({ method: "open_list", params: { query: INBOX } }).catch(() => {});
    client.call({ method: "open_list", params: { query: INBOX } }).catch(() => {});
    const missing = client.setVisible(9, 0, 3);
    transport.deliver(NO_VIEW);
    await expect(missing).rejects.toThrow(Refused);
    await expect(missing).rejects.toThrow("this view is not there");
    transport.deliver(NOT_JSON);
    expect(strays).toEqual(["invalid"]);
  });

  test("batches for a closed view are dropped", async () => {
    const transport = new FakeTransport();
    const client = new CoreClient(transport);
    const batches: ViewBatch[] = [];
    const opening = client.openList(INBOX, { batch: (b) => batches.push(b), refused: () => {} });
    transport.deliver(OPENED);
    await opening;
    void client.closeView(1);
    transport.deliver(BATCH);
    expect(batches).toEqual([]);
  });
});

group("ListRows", () => {
  const row = (key: number) => ({ key, sender: `Sender ${key}`, subject: "", snippet: "", date: 0, unread: false, flagged: false, attachment: false });
  const rows = (first: number, count: number) => ({ first, rows: Array.from({ length: count }, (_, i) => row(first + i)) });

  test("keeps what the new held range still covers, adds what came, and drops the rest", () => {
    const list = new ListRows();
    list.apply({ total: 1000, held_first: 0, held_count: 100, ranges: [rows(0, 100)] });
    expect(list.row(99)?.key).toBe(99);
    // Scrolled down a page: the core sends only rows 100..150 and says to hold 50..150.
    list.apply({ total: 1000, held_first: 50, held_count: 100, ranges: [rows(100, 50)] });
    expect(list.held).toBe(100);
    expect(list.row(49)).toBeUndefined();
    expect(list.row(50)?.key).toBe(50);
    expect(list.row(149)?.key).toBe(149);
    expect(list.row(150)).toBeUndefined();
  });

  test("a jump far away holds only the rows around it", () => {
    const list = new ListRows();
    list.apply({ total: 100000, held_first: 0, held_count: 100, ranges: [rows(0, 100)] });
    list.apply({ total: 100000, held_first: 89950, held_count: 150, ranges: [rows(89950, 150)] });
    expect(list.held).toBe(150);
    expect(list.row(0)).toBeUndefined();
    expect(list.row(90000)?.key).toBe(90000);
  });
});
