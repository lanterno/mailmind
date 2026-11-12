import { connect } from "#transport";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { CoreClient } from "./core/client";
import type { CoreStatus } from "./core/transport";

const root = document.getElementById("root");
if (!root) throw new Error("the page has no #root");

// The status arrives before React renders anything, so it is kept here and handed to the app.
let status: CoreStatus = { state: "starting" };
const listeners = new Set<() => void>();
function setStatus(next: CoreStatus): void {
  status = next;
  for (const listener of listeners) listener();
}
const statusStore = {
  get: () => status,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};

const client = new CoreClient(await connect(setStatus));

createRoot(root).render(
  <StrictMode>
    <App client={client} status={statusStore} />
  </StrictMode>,
);
