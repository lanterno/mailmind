// What a message proposes, as the page handles it: asked for on a click, shown as suggestions someone confirms or edits. Free
// of the DOM and of `state`, so its tests run alone.

// A datetime-local field is wall-clock time, so a suggestion's time goes in as that and comes back an instant: a time the text
// named in another zone shows here as it falls here, and a calendar then has it right wherever it is.
export function toField(moment: string | null | undefined, allDay: boolean): string {
  if (!moment) return "";
  if (allDay) return moment.slice(0, 10);
  // A time as the text named it, in no zone: its clock reading is the field.
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(moment)) return moment.slice(0, 16);
  const d = new Date(moment);
  if (Number.isNaN(d.getTime())) return "";
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}:${two(d.getMinutes())}`;
}

// What a form's field says, as the server reads it. A day stays a day; a time becomes the instant it is here.
export function fromField(field: string, allDay: boolean): string {
  if (!field || allDay) return field;
  const d = new Date(field);
  return Number.isNaN(d.getTime()) ? field : d.toISOString();
}

const WHEN_FMT: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" };

// A task's date, for a list: "Mon, Nov 9", and said to be overdue or today against the local day.
export function dueWords(due: string | null | undefined, today: string): { words: string; late: boolean } {
  if (!due) return { words: "", late: false };
  const words = new Date(`${due}T00:00:00Z`).toLocaleDateString(undefined, WHEN_FMT);
  if (due < today) return { words: `${words} · overdue`, late: true };
  return { words: due === today ? `${words} · today` : words, late: false };
}

export function localDay(now: Date = new Date()): string {
  const two = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`;
}
