// The event stream says what changed, in ids and counts. Each answer here is to read again what that touches; a list
// refetches in place, so it never yanks the reader or the row you're on.
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { createStore } from "../lib/store";

export const newBuild = createStore(false);
const LOADED = document.querySelector<HTMLMetaElement>('meta[name="mailmind-build"]')?.content ?? "";

const MAIL_CHANGED = ["new-mail", "snooze-wake", "mute", "reconcile", "flags-changed", "folder-synced", "followup", "undo-done", "commands-changed", "resync"];

export function useRealtime() {
  const qc = useQueryClient();
  useEffect(() => {
    const es = new EventSource("/api/events");
    const again = (key: string) => void qc.invalidateQueries({ queryKey: [key] });
    const mail = () => {
      again("messages");
      again("folders");
    };
    for (const topic of MAIL_CHANGED) es.addEventListener(topic, mail);
    es.addEventListener("resync", () => again("digest"));
    es.addEventListener("sync-done", () => {
      again("digest");
      again("folders");
    });
    es.addEventListener("hello", (e) => {
      const { build } = JSON.parse((e as MessageEvent<string>).data) as { build?: string };
      if (build && build !== LOADED) newBuild.set(true);
    });
    return () => es.close();
  }, [qc]);
}
