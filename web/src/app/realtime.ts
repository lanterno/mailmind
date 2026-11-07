// The event stream says what changed, in ids and counts. Each answer here is to read again what that touches; a list
// refetches in place, so it never yanks the reader or the row you're on.
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { GetAnswers } from "../lib/api/types.gen";
import { asking } from "../features/assistant/ask";
import { onNewMail } from "../features/assistant/NewMail";
import { composing } from "../features/compose/compose";
import { onDraftReady } from "../features/compose/draftAhead";
import { marked } from "../features/mailbox/actions";
import { createStore } from "../lib/store";
import { shortAddress } from "../lib/text";
import { toast } from "../lib/toast";
import { parseRoute } from "./route";

export const newBuild = createStore(false);
const LOADED = document.querySelector<HTMLMetaElement>('meta[name="mailmind-build"]')?.content ?? "";

const MAIL_CHANGED = ["new-mail", "snooze-wake", "mute", "reconcile", "flags-changed", "folder-synced", "followup", "undo-done", "commands-changed", "resync"];

export function useRealtime() {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/events");
    const again = (key: string) => void qc.invalidateQueries({ queryKey: [key] });
    let digestSoon: ReturnType<typeof setTimeout> | undefined;
    const mail = () => {
      again("messages");
      again("folders");
      again("screener");
      clearTimeout(digestSoon);
      digestSoon = setTimeout(() => again("digest"), 400);
    };
    for (const topic of MAIL_CHANGED) es.addEventListener(topic, mail);
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
    es.addEventListener("sync-done", () => {
      again("digest");
      again("folders");
    });
    es.addEventListener("outbox", () => {
      again("outbox");
      again("pending");
    });
    es.addEventListener("commands-changed", () => again("pending"));
    es.addEventListener("draft-ready", (e) => onDraftReady((JSON.parse((e as MessageEvent<string>).data) as { pk?: number }).pk));
    es.addEventListener("hello", (e) => {
      const { build } = JSON.parse((e as MessageEvent<string>).data) as { build?: string };
      if (build && build !== LOADED) newBuild.set(true);
    });
    return () => {
      clearTimeout(digestSoon);
      es.close();
    };
  }, [qc]);
}
