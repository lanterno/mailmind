// The desktop's page: the core is in the Tauri process, so this build carries no worker and no WebAssembly.
import { TauriTransport } from "./tauri-transport";
import type { CoreStatus, Transport } from "./transport";

export async function connect(onStatus: (status: CoreStatus) => void): Promise<Transport> {
  return new TauriTransport(onStatus);
}
