import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./app/App";
import "./app/app.css";
import { bindComposer } from "./features/compose/compose";
import { refusalOf } from "./lib/api/client";
import { guardFrames } from "./features/mailbox/MailFrame";
import { loadPrefs } from "./lib/prefs";

guardFrames();

// The server is on this machine, so a browser that says it's offline can still reach it: nothing waits for the network. A read
// that failed goes once more, unless the server said no (refusalOf), which it would say again.
const queries = new QueryClient({
  defaultOptions: { queries: { retry: (failures, e) => failures < 1 && !refusalOf(e), networkMode: "always" }, mutations: { networkMode: "always" } },
});
bindComposer(queries);
void loadPrefs();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
