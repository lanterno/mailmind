// What a pasted link comes to with what follows a click taken off it: a redirector's wrapper, and tracking parameters. Pure:
// the paste handler on the composer's box is all the DOM this needs.

// Names that do nothing but attribute a click. A short, broad one (ref, source, si) could mean something to the site, so it stays.
const TRACKING = /^(?:utm_[a-z0-9_]+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|mc_cid|mc_eid|igshid|_hsenc|_hsmi|mkt_tok)$/i;

// Redirectors whose target is plainly in the query, so nothing has to be asked to read it.
const WRAPPERS: { host: RegExp; path: RegExp; params: string[] }[] = [
  { host: /^(?:www\.)?google\.(?:[a-z]{2,3}|co\.[a-z]{2}|com\.[a-z]{2})$/, path: /^\/url$/, params: ["q", "url"] },
  { host: /^lm?\.facebook\.com$/, path: /^\/l\.php$/, params: ["u"] },
  { host: /^l\.instagram\.com$/, path: /^\/$/, params: ["u"] },
  { host: /^(?:www\.)?linkedin\.com$/, path: /^\/(?:redir\/redirect|safety\/go)\/?$/, params: ["url"] },
  { host: /\.safelinks\.protection\.outlook\.com$/, path: /^\/$/, params: ["url"] },
  { host: /^(?:www\.)?youtube\.com$/, path: /^\/redirect$/, params: ["q"] },
  { host: /^(?:www\.)?slack-redir\.net$/, path: /^\/link$/, params: ["url"] },
];
const UNWRAP_MAX = 4;   // a redirector can wrap another: this far and no further

const URL_IN_TEXT = /\bhttps?:\/\/[^\s<>"'`]+/gi;
const TRAILING = new Set(".,;:!?)]}。、，！？）】》」』»…");   // punctuation after a link, which is the sentence's

function parsed(link: string): URL | null {
  try {
    return new URL(link);
  } catch {
    return null;
  }
}

function targetOf(link: string): string | null {
  const u = parsed(link);
  if (!u) return null;
  const wrapper = WRAPPERS.find((w) => w.host.test(u.hostname) && w.path.test(u.pathname));
  if (!wrapper) return null;
  for (const name of wrapper.params) {
    const target = u.searchParams.get(name);
    if (target && /^https?:\/\/\S+$/i.test(target) && parsed(target)) return target;
  }
  return null;
}

// Cut out of the query, so the rest of the link stays as it was written and a signature in it still holds.
function withoutTracking(link: string): string {
  const hash = link.indexOf("#"), head = hash < 0 ? link : link.slice(0, hash), tail = hash < 0 ? "" : link.slice(hash);
  const q = head.indexOf("?");
  if (q < 0) return link;
  const params = head.slice(q + 1).split("&"), kept = params.filter((p) => !TRACKING.test(p.split("=", 1)[0]!));
  if (kept.length === params.length) return link;
  return head.slice(0, q) + (kept.length ? `?${kept.join("&")}` : "") + tail;
}

function cleaned(url: string): string {
  let link = url;
  for (let n = 0; n < UNWRAP_MAX; n++) {
    const target = targetOf(link);
    if (!target) break;
    link = target;
  }
  return withoutTracking(link);
}

export function cleanLinks(text: string): string {
  return text.replace(URL_IN_TEXT, (found) => {
    let end = found.length;
    while (end > 0 && TRAILING.has(found[end - 1]!)) end -= 1;
    return cleaned(found.slice(0, end)) + found.slice(end);
  });
}
