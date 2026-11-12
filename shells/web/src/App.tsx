import { useSyncExternalStore } from "react";
import type { CoreClient } from "./core/client";
import { DEMO_INBOX } from "./core/demo";
import type { CoreStatus } from "./core/transport";
import { MessageList } from "./list/MessageList";

export interface StatusStore {
  get(): CoreStatus;
  subscribe(listener: () => void): () => void;
}

export function App({ client, status }: { client: CoreClient; status: StatusStore }) {
  const current = useSyncExternalStore(status.subscribe, status.get);
  return (
    <main className="app">
      <header className="bar">
        <h1>Inbox</h1>
        <p className="status" role="status">
          {said(current)}
        </p>
      </header>
      <MessageList client={client} query={DEMO_INBOX} />
    </main>
  );
}

function said(status: CoreStatus): string {
  switch (status.state) {
    case "starting":
      return "Opening…";
    case "building":
      return `Making a fictional mailbox of ${status.messages.toLocaleString()} messages, once…`;
    case "ready":
      return "";
    case "failed":
      return `The core did not start: ${status.detail}`;
  }
}
