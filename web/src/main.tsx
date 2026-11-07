import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./app/App";
import "./app/app.css";
import { bindComposer } from "./features/compose/compose";
import { guardFrames } from "./features/mailbox/MailFrame";

guardFrames();

// The server is on this machine, so a browser that says it's offline can still reach it: nothing waits for the network.
const queries = new QueryClient({ defaultOptions: { queries: { retry: 1, networkMode: "always" }, mutations: { networkMode: "always" } } });
bindComposer(queries);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
