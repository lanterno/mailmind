// Small things said about a person or a file, shared by the list and the reader.
export const shortAddress = (a: string | null | undefined) => (a ?? "").split("@")[0] ?? "";

// A hue from the whole wheel at low saturation: amber stays the needs-you signal.
export function avatarHue(s: string | null | undefined): number {
  let h = 0;
  for (const c of s ?? "") h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

export function initialsOf(name: string | null | undefined, addr: string | null | undefined): string {
  const all = /@/.test(name ?? "") ? [] : (String(name ?? "").match(/\p{L}[\p{L}\p{M}]*/gu) ?? []);
  const caps = all.filter((x) => x[0] !== x[0]!.toLowerCase());
  const words = caps.length >= 2 ? caps : all;   // "Library of Ash Hollow" is LA, not LO
  const letters = words.length ? words.slice(0, 2).map((x) => x[0]).join("") : (String(addr ?? name ?? "?").match(/\p{L}|\d/u) ?? ["?"])[0];
  return (letters ?? "?").toUpperCase();
}

export function fileSize(n: number | null | undefined): string {
  if (!n) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

// The list's section for a message's time (epoch seconds), by local calendar day.
export function dayGroup(ts: number, now: Date = new Date()): string {
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const day = new Date(ts * 1000);
  day.setHours(0, 0, 0, 0);
  const days = Math.round((today.getTime() - day.getTime()) / 86400000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "Past week" : "Earlier";
}

// Money the ledger counts, in millionths of a dollar, rounded up to the cent.
export function usd(micros: number): string {
  const cents = Math.ceil(micros / 10000);
  return `$${Math.floor(cents / 100).toLocaleString("en-US")}.${String(cents % 100).padStart(2, "0")}`;
}

// How long ago, in the fewest words.
export function relWhen(iso: string, now = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "";
  const s = Math.max(0, (now - at) / 1000);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
