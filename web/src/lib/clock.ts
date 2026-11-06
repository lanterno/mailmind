// The time, as a value a component can depend on. The compiler keeps what a call with no reactive input gave, so whatever
// shows the time reads this rather than calling Date itself.
import { createStore } from "./store";

export const minute = createStore(Date.now());
setInterval(() => minute.set(Date.now()), 30_000);
