import { createStore } from "./store";

const text = createStore<{ message: string; shown: boolean }>({ message: "", shown: false });
const spoken = createStore("");
let timer: ReturnType<typeof setTimeout> | undefined;

export function toast(message: string) {
  text.set({ message, shown: true });
  spoken.set(message);
  clearTimeout(timer);
  timer = setTimeout(() => text.set({ message: text.get().message, shown: false }), 2600);
}

export function Toaster() {
  const { message, shown } = text.useValue();
  const live = spoken.useValue();
  return (
    <>
      <div className={shown ? "toast show" : "toast"} id="toast" aria-hidden="true">{message}</div>
      <div id="srLive" className="visually-hidden" aria-live="polite" aria-atomic="true">{live}</div>
    </>
  );
}
