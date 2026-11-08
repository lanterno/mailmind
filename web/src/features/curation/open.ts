import { createStore } from "../../lib/store";

export const curateOpen = createStore(false);

// The filter the dialog holds: one being made, or with an id one being edited.
export interface FilterForm { id?: string; q: string; star: boolean; read: boolean }
export const filterForm = createStore<FilterForm | null>(null);
export const newFilter = (q = ""): FilterForm => ({ q, star: true, read: false });      // a filter has to do something
