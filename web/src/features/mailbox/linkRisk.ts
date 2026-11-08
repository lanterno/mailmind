// Links in a sandboxed body are inert, so the parent takes each click and warns on a look-alike before it opens one.
// Pure: it reads the link's href and text, and nothing else.
// The domain an organisation registers: the last two labels of a host, or three under a country's own second level (co.uk).
const SECOND_LEVEL = new Set(["ac", "co", "com", "edu", "go", "gob", "gouv", "gov", "k12", "ltd", "me", "mil", "ne", "net", "nom", "or", "org", "plc", "sch"]);
export function registrable(host: string): string {
  const labels = host.toLowerCase().split(".");
  const [second, top] = labels.slice(-2);
  return labels.slice(top?.length === 2 && second && SECOND_LEVEL.has(second) ? -3 : -2).join(".");
}

// The href's own host, and the host of any http(s) URL inside it: legitimate redirectors put it there.
export function linkHosts(href: string): string[] {
  const hosts: string[] = [];
  try {
    hosts.push(new URL(href).hostname.replace(/^www\./, "").toLowerCase());
  } catch { /* not a URL: no host of its own */ }
  let decoded = href;
  try {
    decoded = decodeURIComponent(href);
  } catch { /* a stray % */ }
  for (const m of decoded.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)) hosts.push(m[1]!.replace(/^www\./, "").toLowerCase());
  return hosts;
}

const hostHolds = (claimed: string, hosts: string[]) => hosts.some((h) => h === claimed || h.endsWith(`.${claimed}`) || claimed.endsWith(`.${h}`));

const SENSITIVE = /\b(sign[ -]?in|log[ -]?in|verify|confirm|account|password|reset|unlock|billing|invoice|payment|suspend)\b/i;

const domainOf = (addr: string) => addr.slice(addr.lastIndexOf("@") + 1).trim().replace(/\.$/, "");

// Whether an answer to `sender` would go to `replyTo` outside the sender's organisation: a subdomain of it, or another under the
// domain it registers, is still theirs.
export function leavesOrganisation(replyTo: string, sender: string): boolean {
  const to = domainOf(replyTo), from = domainOf(sender);
  return Boolean(to && from) && registrable(to) !== registrable(from);
}

// Why a link deserves a warning, in words, or null.
export function linkRisk(href: string, text: string, senderDomain: string | undefined): string | null {
  if (!/^https?:/i.test(href)) return null;
  let host: string;
  try {
    host = new URL(href).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return null;
  }
  if (/(^|\.)xn--/i.test(host)) return `a non-Latin “punycode” domain (${host}), often used to imitate a real site`;
  const named = text.trim().match(/\b([a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)\b/i);
  const claimed = named ? named[1]!.toLowerCase().replace(/^www\./, "") : null;
  if (claimed) {
    // Only a real host in the URL clears it, not a substring planted in a path, a query or a subdomain.
    return hostHolds(claimed, linkHosts(href)) ? null : `a different domain (${host}) than the link text suggests (${claimed})`;
  }
  if (senderDomain && SENSITIVE.test(text) && registrable(host) !== registrable(senderDomain.toLowerCase())) {
    return `an unfamiliar domain (${host}) — the text looks like a sign-in/account action but the sender is ${senderDomain}`;
  }
  return null;
}
