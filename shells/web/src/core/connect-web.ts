// The browser's page: Tauri's carrier when it runs inside the desktop shell, the worker otherwise.
import { isTauri } from "@tauri-apps/api/core";
import { DEMO_MESSAGES } from "./demo";
import type { CoreStatus, Transport } from "./transport";

export async function connect(onStatus: (status: CoreStatus) => void): Promise<Transport> {
  if (isTauri()) {
    const { TauriTransport } = await import("./tauri-transport");
    return new TauriTransport(onStatus);
  }
  const { WorkerTransport } = await import("./worker-transport");
  const asked = Number(new URLSearchParams(location.search).get("messages"));
  const messages = Number.isInteger(asked) && asked > 0 ? asked : DEMO_MESSAGES;
  const database = messages === DEMO_MESSAGES ? "/demo.db" : `/demo-${messages}.db`;
  return new WorkerTransport({ database, messages }, onStatus);
}
