// The event stream says what changed, in ids and counts. Each answer here is to read again what that touches; a list
// refetches in place, so it never yanks the reader or the row you're on.
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GetAnswers } from "../lib/api/types";
import { onNewMail, onSnoozeWake } from "../features/assistant/NewMail";
import { onDraftReady } from "../features/compose/draftAhead";
import type { ListData } from "../features/mailbox/data";
import { createStore } from "../lib/store";
import { shortAddress } from "../lib/text";
import { currentRoute } from "./route";

export const newBuild = createStore(false);
const LOADED = document.querySelector<HTMLMetaElement>('meta[name="mailmind-build"]')?.content ?? "";

const MAIL_CHANGED = ["new-mail", "snooze-wake", "mute", "flags-changed", "followup", "undo-done", "commands-changed", "resync"];

export function useRealtime() {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/events");
    // A tab nobody is looking at reads nothing: most reads count as someone here, and that lets the AI go on drafting ahead.
    // It marks what changed, and that is read when the tab comes back.
    // `gone`: an account removed, whose own lists the server no longer answers for, so they aren't read again.
    const again = (key: string, gone?: string) => void qc.invalidateQueries({
      queryKey: [key], refetchType: document.hidden ? "none" : "active", predicate: (query) => !gone || !query.queryKey.includes(gone),
    });
    let digestSoon: ReturnType<typeof setTimeout> | undefined;
    const digestLater = () => {
      clearTimeout(digestSoon);
      digestSoon = setTimeout(() => again("digest"), 400);
    };
    const mail = (gone?: string) => {
      again("messages", gone);
      again("folders", gone);
      again("screener");
      again("tidy");
      digestLater();
    };
    for (const topic of MAIL_CHANGED) es.addEventListener(topic, () => mail());
    // A round of summaries landed: no mail changed, but what needs you and what the AI rates safe to file did, which the
    // lists, the Inbox's tabs, Tidy and the brief's tallies (the digest) count. The folders' counts are of unread mail.
    es.addEventListener("summarized", () => {
      again("messages");
      again("tidy");
      digestLater();
    });
    // Mail gone from a server, or an account removed: the digest, read at once then, takes it off the rail.
    es.addEventListener("reconcile", (e) => {
      const d = JSON.parse((e as MessageEvent<string>).data) as { account?: string; removed?: boolean };
      if (!d.removed || !d.account) return mail();
      mail(d.account);
      clearTimeout(digestSoon);
      again("digest");
    });
    es.addEventListener("folder-synced", (e) => {
      const d = JSON.parse((e as MessageEvent<string>).data) as { account?: string; folder?: string; ok?: boolean };
      if (d.ok) return mail();
      // A read that failed: the list says so and stays. Reading it again would read the folder again, and the end of that
      // would come back here, for as long as the server says no. Retry, or opening the folder again, is a person asking.
      const settled = (failed: boolean) => (list?: ListData) => list && { ...list, pages: list.pages.map((p) => ({ ...p, syncing: false, catchingUp: false, syncFailed: p.syncFailed || (failed && p.syncing) })) };
      qc.setQueriesData<ListData>({ queryKey: ["messages", "folder", d.account, d.folder] }, settled(true));
      qc.setQueriesData<ListData>({ queryKey: ["messages", "all"] }, settled(false));
    });
    es.addEventListener("new-mail", (e) => {
      const digest = qc.getQueryData<GetAnswers["/api/digest"]>(["digest"]);
      onNewMail(JSON.parse((e as MessageEvent<string>).data) as object, currentRoute(), Boolean(digest && digest.ai.engine !== "none"), (a) => digest?.aliases?.[a] || shortAddress(a), digest?.accounts ?? []);
    });
    es.addEventListener("snooze-wake", (e) => {
      const digest = qc.getQueryData<GetAnswers["/api/digest"]>(["digest"]);
      onSnoozeWake(JSON.parse((e as MessageEvent<string>).data) as { accounts?: string[]; count?: number }, currentRoute(), digest?.accounts ?? []);
    });
    es.addEventListener("resync", () => again("digest"));
    // An account's server didn't answer, to a sync or to a connection outside one: the status says it is trying again,
    // or can't connect, rather than synced, so the digest that holds it is read again. Nothing else changed.
    es.addEventListener("sync-trouble", () => again("digest"));
    // With the stream down nothing says what changed, so the counts and the tray are read on a timer until it reconnects.
    let up = false;
    es.onopen = () => { up = true; };
    es.onerror = () => { up = false; };
    const whileDown = (run: () => void, ms: number) => setInterval(() => { if (!up && !document.hidden) run(); }, ms);
    const timers = [
      whileDown(() => again("digest"), 45_000),
      whileDown(() => { again("outbox"); again("pending"); }, 20_000),
    ];
    es.addEventListener("sync-done", (e) => {
      // A first scan stores mail and says no new-mail, since none of it is new: a list already open (All inboxes, say,
      // as a new account's first sync runs) shows it once the sync that stored it ends.
      const d = JSON.parse((e as MessageEvent<string>).data) as { kept?: number; junk?: number };
      if ((d.kept ?? 0) + (d.junk ?? 0) > 0) {
        again("messages");
        again("screener");
        again("tidy");
      }
      again("digest");
      again("folders");
      again("documents");
      again("labels");
    });
    es.addEventListener("outbox", () => {
      again("outbox");
      again("pending");
    });
    es.addEventListener("commands-changed", () => again("pending"));
    es.addEventListener("tasks-changed", () => again("tasks"));
    es.addEventListener("draft-ready", (e) => onDraftReady((JSON.parse((e as MessageEvent<string>).data) as { key?: number }).key));
    es.addEventListener("hello", (e) => {
      const { build } = JSON.parse((e as MessageEvent<string>).data) as { build?: string };
      if (build && build !== LOADED) newBuild.set(true);
    });
    return () => {
      clearTimeout(digestSoon);
      timers.forEach(clearInterval);
      es.close();
    };
  }, [qc]);
}
