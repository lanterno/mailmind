// What the tray says of each row, apart from the screen. Read only queues nothing a person asks for, so a row that waits while it
// is on is one a full run queued earlier, and waits for the next; a draft saved read-only is kept on this Mac instead.
import type { GetAnswers } from "../../lib/api/types";

export type Held = GetAnswers["/api/pending"]["items"][number];
export type Waiting = GetAnswers["/api/outbox"]["drafts"][number];

// A folder_gone change was given back because another app deleted or renamed its folder, which no retry undoes.
const WHY: Record<string, string> = {
  refused: "the server refused it",
  no_folder: "no folder to move it to",
  folder_gone: "its folder was deleted or renamed in another app",
  "account removed": "its account is gone",
};

// Why a change is still waiting: stuck, and why; or when it goes.
export function heldWhy(p: Pick<Held, "state" | "error" | "attempts">, readOnly: boolean): string {
  if (p.state === "error") return WHY[p.error ?? ""] ?? "couldn’t reach the server";
  return readOnly ? "waits for a full run" : p.attempts ? `tried ${p.attempts}×, trying again` : "once the server is back";
}

// Why a draft is in the tray: kept on this Mac by a read-only save, which it leaves only when saved again with read only off;
// or on its way to or off its server.
export function draftWhy(d: Pick<Waiting, "kept" | "discarding" | "attempts">, readOnly: boolean): string {
  if (d.kept) return "Kept on this Mac";
  const goes = d.discarding ? "leaves the server" : "reaches the server";
  return readOnly ? `${goes} on a full run` : d.attempts ? `tried ${d.attempts}×, trying again` : `${goes} once it’s back`;
}
