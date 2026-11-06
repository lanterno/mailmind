// A mail body as its frame's document. The frame's sandbox is the wall; this document's policy says the same again, with
// every channel named, so an edit can't quietly reopen one. Pure, given the theme's colors.
export interface FrameTheme { light: boolean; bg: string; panel: string; ink: string; primaryInk: string; lineHi: string; mute: string }
export interface MailPrefs { colors: "auto" | "dark" | "original"; font: string; lineHeight: number; sizePx: number }

const POLICY = "default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; child-src 'none'; connect-src 'none'; img-src https: data:; style-src 'unsafe-inline'; font-src data:;";
const HEAD = `<meta http-equiv="Content-Security-Policy" content="${POLICY}"><meta name="referrer" content="no-referrer">`;
const QUOTE = "details.mm-quote{margin:10px 0}details.mm-quote>summary{cursor:pointer;list-style:none;color:#8a8a8a;font-size:12px;padding:3px 9px;border:1px solid #8a8a8a66;border-radius:6px;display:inline-block}details.mm-quote>summary::-webkit-details-marker{display:none}details.mm-quote[open]>summary{margin-bottom:8px}";

export const escapeHtml = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export function foldPlainQuote(t: string): { head: string; quoted: string } | null {
  const lines = t.split("\n");
  let at = lines.findIndex((l) => /^\s*(On\s.+\bwrote:|-{2,}\s*Original Message\s*-{2,}|_{10,}|From:\s)/.test(l));
  if (at < 0) {
    let j = lines.length - 1;
    while (j >= 0 && !lines[j]!.trim()) j--;
    if (j >= 0 && /^\s*&gt;/.test(lines[j]!)) {
      while (j >= 0 && (/^\s*&gt;/.test(lines[j]!) || !lines[j]!.trim())) j--;
      at = j + 1;
    }
  }
  if (at <= 0 || !lines.slice(0, at).join("").trim()) return null;
  return { head: lines.slice(0, at).join("\n"), quoted: lines.slice(at).join("\n") };
}

const isDark = (isHtml: boolean, p: MailPrefs, t: FrameTheme) =>
  p.colors !== "auto" ? p.colors === "dark" : t.light ? false : !isHtml;

export function bodyDoc(content: string, isHtml: boolean, prefs: MailPrefs, theme: FrameTheme): string {
  const dark = isDark(isHtml, prefs, theme);
  let style: string, inner: string;
  if (isHtml && dark) {
    // Invert the mail to darken its usually light backgrounds, and invert media back so photos stay true.
    style = `html,body{background:${theme.bg}}body{margin:14px;overflow-wrap:break-word}.mm-dark{filter:invert(.92) hue-rotate(180deg)}.mm-dark img,.mm-dark picture,.mm-dark video,.mm-dark svg,.mm-dark [style*=background-image]{filter:invert(1) hue-rotate(180deg)}img{max-width:100%;height:auto;max-height:8000px}table{max-width:100%}${QUOTE}`;
    inner = `<div class="mm-dark">${content}</div>`;
  } else if (isHtml) {
    style = `body{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;background:${theme.light ? theme.panel : "#ffffff"};margin:14px;overflow-wrap:break-word}a{color:#0b66c3}img{max-width:100%;height:auto;max-height:8000px}blockquote{border-left:3px solid #ccc;margin:0 0 0 4px;padding-left:12px;color:#555}table{max-width:100%}${QUOTE}`;
    inner = content;
  } else {
    const ink = dark ? theme.ink : "#24221E", bg = dark ? theme.bg : "#FFFDF8", link = dark ? theme.primaryInk : "#2D4B70";
    style = `body{margin:14px;color:${ink};background:${bg};overflow-wrap:break-word}a{color:${link}}pre{white-space:pre-wrap;font:${prefs.sizePx}px/${prefs.lineHeight} ${prefs.font};margin:0}blockquote{border-left:3px solid ${dark ? theme.lineHi : "#D5C9B1"};margin:0 0 0 4px;padding-left:12px;color:${dark ? theme.mute : "#625B4D"}}${QUOTE}`;
    const fold = foldPlainQuote(content);
    inner = fold ? `<pre>${fold.head}</pre><details class="mm-quote"><summary>··· quoted text</summary><pre>${fold.quoted}</pre></details>` : `<pre>${content}</pre>`;
  }
  return `<!doctype html><html lang="en"><head><meta charset=utf-8><base target="_blank">${HEAD}<style>${style}</style></head><body>${inner}</body></html>`;
}

// A body payload as its frame's document.
export function mailDoc(body: { has_html?: boolean; html?: string | null; text?: string | null }, prefs: MailPrefs, theme: FrameTheme): string {
  return body.has_html ? bodyDoc(body.html ?? "", true, prefs, theme) : bodyDoc(escapeHtml(body.text || "(no content)"), false, prefs, theme);
}
