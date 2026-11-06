// A value outside React that components read and any code sets: toasts, the undo float, what is being filed.
import { useSyncExternalStore } from "react";

export interface Store<T> { get(): T; set(value: T): void; useValue(): T }

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();
  const subscribe = (fn: () => void) => {
    listeners.add(fn);
    return () => void listeners.delete(fn);
  };
  const get = () => value;
  return {
    get,
    set(next) {
      if (Object.is(next, value)) return;
      value = next;
      listeners.forEach((fn) => fn());
    },
    useValue: () => useSyncExternalStore(subscribe, get),
  };
}

// Read from storage that can be missing or refuse: a private window, cleared site data.
export function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function store(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* it just won't be remembered */ }
}
