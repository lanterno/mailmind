// A mail body in its frame. The frame's sandbox is the wall: it allows same-origin so this page can size it and take its
// link clicks, which is safe only because no script can run in it. So the attribute is exactly this, and guardFrames
// strips allow-scripts off any frame that ever gets it.
import { useEffect, useRef } from "react";
import { stored } from "../../lib/store";
import { theme as themeStore, type ThemeName } from "../../lib/theme";
import { toast } from "../../lib/toast";
import { linkRisk } from "./linkRisk";
import { mailDoc, type FrameTheme, type MailPrefs } from "./mailDoc";

const SIZE: Record<string, number> = { s: 15, m: 16.5, l: 18, xl: 20 };
const FONT: Record<string, string> = {
  sans: '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
  serif: '"Iowan Old Style",Charter,"Source Serif 4",Georgia,serif',
  mono: 'ui-monospace,"SF Mono",Menlo,Consolas,monospace',
};
const MAX_HEIGHT = 5000;

function readPrefs(): MailPrefs {
  let saved: { size?: string; font?: string } = {};
  try {
    saved = JSON.parse(stored("mm_read") ?? "{}") ?? {};
  } catch { /* a bad value is no setting */ }
  const colors = stored("mm_mailtheme");
  const font = saved.font && FONT[saved.font] ? saved.font : "sans";
  return {
    colors: colors === "dark" || colors === "original" ? colors : "auto",
    font: FONT[font]!,
    lineHeight: font === "mono" ? 1.55 : 1.62,
    sizePx: SIZE[saved.size ?? "m"] ?? 16.5,
  };
}

function frameTheme(name: ThemeName): FrameTheme {
  const css = getComputedStyle(document.body), tk = (t: string) => css.getPropertyValue(t).trim();
  return { light: name !== "night", bg: tk("--bg"), panel: tk("--panel"), ink: tk("--ink"), primaryInk: tk("--primary-ink"), lineHi: tk("--line-hi"), mute: tk("--mute") };
}

export interface MailBody { pk?: number; has_html?: boolean; html?: string | null; text?: string | null; from_addr?: string; from_name?: string; subject?: string }

function wireLinks(frame: HTMLIFrameElement, senderDomain: string | undefined) {
  const doc = frame.contentWindow?.document;
  if (!doc) return;
  const take = (e: MouseEvent) => {
    const a = (e.target as Element | null)?.closest?.("a[href]");
    if (!a) return;
    e.preventDefault();
    const href = a.getAttribute("href") ?? "";
    if (/^mailto:/i.test(href)) {
      window.open(href);
      return;
    }
    if (!/^https?:/i.test(href)) {
      toast("This link can’t be opened");
      return;
    }
    const risk = linkRisk(href, a.textContent ?? "", senderDomain);
    if (risk && !window.confirm(`⚠ Careful — this link points to ${risk}.\n\nOpen ${href} anyway?`)) return;
    window.open(href, "_blank", "noopener,noreferrer");
  };
  doc.addEventListener("click", take, true);
  doc.addEventListener("auxclick", take, true);    // a middle click goes through the check too
}

export function MailFrame({ body, minHeight, pad }: { body: MailBody; minHeight: number; pad: number }) {
  const name = themeStore.useValue();
  const prefs = readPrefs();
  const srcDoc = mailDoc(body, prefs, frameTheme(name));
  const frame = useRef<HTMLIFrameElement>(null);
  const fit = () => {
    const f = frame.current;
    const height = f?.contentWindow?.document.body?.scrollHeight;
    if (f) f.style.height = `${Math.min((height || minHeight) + pad, MAX_HEIGHT)}px`;
  };
  // A narrower or wider column reflows the content, so fit again whenever the frame's width changes.
  useEffect(() => {
    const f = frame.current;
    if (!f || typeof ResizeObserver === "undefined") return;
    let width = Math.round(f.getBoundingClientRect().width);
    const seen = new ResizeObserver(([entry]) => {
      const now = Math.round(entry!.contentRect.width);
      if (now !== width) {
        width = now;
        fit();
      }
    });
    seen.observe(f);
    return () => seen.disconnect();
  });
  return (
    <iframe
      ref={frame}
      className="rd-iframe"
      title={`Email body — from ${body.from_name || body.from_addr || "sender"}: ${body.subject || "(no subject)"}`}
      sandbox="allow-same-origin"
      srcDoc={srcDoc}
      onLoad={() => {
        fit();
        wireLinks(frame.current!, body.from_addr?.split("@")[1]);
      }}
    />
  );
}

export function guardFrames() {
  new MutationObserver((changes) => {
    for (const { target } of changes) {
      const f = target as Element;
      if (f.tagName === "IFRAME" && f.classList.contains("rd-iframe") && (f.getAttribute("sandbox") ?? "").split(/\s+/).includes("allow-scripts")) {
        f.setAttribute("sandbox", "allow-same-origin");
        console.error("SECURITY: stripped allow-scripts from a reader iframe");
      }
    }
  }).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ["sandbox"] });
}
