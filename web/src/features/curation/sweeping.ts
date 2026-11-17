// What runs for each account's sweep, a plan or the sweep itself, as the words its row says meanwhile. A store, so it outlives the
// drawer being closed and opened again; and while one runs for an account, Sweep and Confirm ask nothing more of it.
import { createStore } from "../../lib/store";

export const sweeps = createStore<ReadonlyMap<string, string>>(new Map());

export const PLANNING = "Looking at what a sweep would move…";
export const sweepingWords = (planned: number) => `Sweeping ${planned} to Trash…`;

// Runs `work` for `account` with its row saying `saying`, unless a plan or a sweep already runs for it: then nothing runs, and
// it answers false.
export async function sweepWhile(account: string, saying: string, work: () => Promise<void>): Promise<boolean> {
  if (sweeps.get().has(account)) return false;
  sweeps.set(new Map([...sweeps.get(), [account, saying]]));
  try {
    await work();
  } finally {
    const left = new Map(sweeps.get());
    left.delete(account);
    sweeps.set(left);
  }
  return true;
}
