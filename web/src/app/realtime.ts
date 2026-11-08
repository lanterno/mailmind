// The event stream says what changed, in ids and counts. Each answer here is to read again what that touches; a list
// refetches in place, so it never yanks the reader or the row you're on.
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GetAnswers } from "../lib/api/types";
import { asking } from "../features/assistant/ask";
import { onNewMail } from "../features/assistant/NewMail";
import { composing } from "../features/compose/compose";
import { onDraftReady } from "../features/compose/draftAhead";
import { marked } from "../features/mailbox/actions";
import type { ListData } from "../features/mailbox/data";
import { createStore } from "../lib/store";
import { shortAddress } from "../lib/text";
import { toast } from "../lib/toast";
import { parseRoute } from "./route";

export const newBuild = createStore(false);
const LOADED = document.querySelector<HTMLMetaElement>('meta[name="mailmind-build"]')?.content ?? "";

const MAIL_CHANGED = ["new-mail", "snooze-wake", "mute", "reconcile", "flags-changed", "followup", "undo-done", "commands-changed", "resync"];

export function useRealtime() {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/events");
    // A tab nobody is looking at reads nothing: most reads count as someone here, and that lets the AI go on drafting ahead.
    // It marks what changed, and that is read when the tab comes back.
    const again = (key: string) => void qc.invalidateQueries({ queryKey: [key], refetchType: document.hidden ? "none" : "active" });
    let digestSoon: ReturnType<typeof setTimeout> | undefined;
    const mail = () => {
      again("messages");
      again("folders");
      again("screener");
      clearTimeout(digestSoon);
      digestSoon = setTimeout(() => again("digest"), 400);
    };
    for (const topic of MAIL_CHANGED) es.addEventListener(topic, mail);
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
      onNewMail(JSON.parse((e as MessageEvent<string>).data) as object, parseRoute(location.pathname, location.search), Boolean(digest && digest.ai.engine !== "none"), (a) => digest?.aliases?.[a] || shortAddress(a));
    });
    es.addEventListener("snooze-wake", (e) => {   // the list on screen shows it; anywhere else this says so
      const d = JSON.parse((e as MessageEvent<string>).data) as { accounts?: string[]; count?: number };
      const { source } = parseRoute(location.pathname, location.search);
      const onList = source?.kind === "allInboxes" || (source?.kind === "folder" && d.accounts?.includes(source.account));
      if (onList && !composing.isOpen() && !asking.get() && !marked.get().size) return;
      const n = d.count || 1;
      toast(`${n} snoozed message${n > 1 ? "s" : ""} back`);
    });
    es.addEventListener("resync", () => again("digest"));
    // With the stream down nothing says what changed, so the counts and the tray are read on a timer until it reconnects.
    let up = false;
    es.onopen = () => { up = true; };
    es.onerror = () => { up = false; };
    const whileDown = (run: () => void, ms: number) => setInterval(() => { if (!up && !document.hidden) run(); }, ms);
    const timers = [
      whileDown(() => again("digest"), 45_000),
      whileDown(() => { again("outbox"); again("pending"); }, 20_000),
    ];
    es.addEventListener("sync-done", () => {
      again("digest");
      again("folders");
      again("documents");
    });
    es.addEventListener("outbox", () => {
      again("outbox");
      again("pending");
    });
    es.addEventListener("commands-changed", () => again("pending"));
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
