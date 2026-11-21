// Small things said about a person or a file, shared by the list and the reader.
export const shortAddress = (a: string | null | undefined) => (a ?? "").split("@")[0] ?? "";

// A hue from the whole wheel at low saturation: amber stays the needs-you signal.
export function avatarHue(s: string | null | undefined): number {
  let h = 0;
  for (const c of s ?? "") h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

// Words a person's name doesn't have and an organisation's often does, folded.
const ORGANISATION_WORDS = new Set([
  "the", "co", "inc", "ltd", "llc", "gmbh", "corp", "plc", "company", "service", "services", "desk", "team",
  "support", "bank", "deals", "shop", "store", "club", "library", "school", "university", "college", "news",
  "newsletter", "weekly", "daily", "bot", "mailer", "notifications", "alerts", "updates", "billing", "help",
  "info", "group", "foundation", "agency", "studio", "cafe", "restaurant", "bakery", "hotel", "airlines",
  "labs", "press", "media", "council", "office", "clinic", "insurance", "payments", "delivery", "online",
]);
// An address's part before the @, letters only, that a role writes from rather than a person.
const ROLE_ADDRESSES = new Set([
  "hello", "hi", "info", "news", "newsletter", "deals", "offers", "orders", "order", "support", "help", "team",
  "contact", "billing", "notifications", "notification", "alerts", "updates", "tracking", "mailer", "admin",
  "service", "sales", "marketing", "receipts", "accounts", "mail", "postmaster", "feedback", "store", "shop",
]);
const NO_REPLY = ["noreply", "no-reply", "donotreply", "do-not-reply"];
const folded = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
const letters = (s: string) => s.replace(/[^\p{L}]/gu, "");

// Whether a name of several words is a person's. Never what an organisation's name has: a figure, an ampersand, a comma
// ("Mori, Ren" is said whole), a word such as Service, Desk, Bank or The. Then, with an address, a person's mostly carries
// their first name (ren@, ren.mori@), or their last with the first's initial beside it (rmori@, mori.r@), and a role's
// doesn't (news@, orders@, noreply@). The last name alone isn't a person's sign: an organisation's address carries the
// last word of its name as often ("Guild Gazette" <gazette@>), and a person's name said whole is never wrong. With no
// address, the name's shape: two to four capitalised words.
function isPerson(words: string[], address: string | null | undefined): boolean {
  const fold = words.map((w) => folded(w).replace(/^\p{P}+|\p{P}+$/gu, ""));
  if (/[\p{N}&+,/|:;()[\]!?]/u.test(words.join(""))) return false;
  if (fold.some((w) => ORGANISATION_WORDS.has(w))) return false;
  const at = address ? address.indexOf("@") : -1;
  if (!address || at < 0) return words.length <= 4 && words.every((w) => /^\p{Lu}[\p{L}\p{M}\-'’.]*$/u.test(w));
  const local = folded(address.slice(0, at));
  if (ROLE_ADDRESSES.has(letters(local)) || NO_REPLY.some((p) => local.startsWith(p))) return false;
  const carried = letters(local);
  const first = letters(fold[0]!), last = letters(fold[fold.length - 1]!);
  if (first.length >= 2 && carried.includes(first)) return true;
  const initial = [...first][0];
  return last.length >= 2 && initial !== undefined && (carried === initial + last || carried === last + initial);
}

// What a short line calls someone ("To Ren"): a person by their first name, and an organisation, or a name it can't tell,
// whole, as "To Parcel" would cut "Parcel Service" short. Null with no name to go by, only an address. The Mac's
// `Words.calling` says the same.
export function calling(name: string | null | undefined, address?: string | null): string | null {
  const said = (name ?? "").replace(/^[\s"']+|[\s"']+$/gu, "");
  if (!said || said.includes("@")) return null;
  const words = said.split(/\s+/u);
  return words.length > 1 && isPerson(words, address) ? words[0]! : said;
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
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
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
