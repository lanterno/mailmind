// A mail body in its frame, loaded from its own URL. Two walls stand around it: the sandbox attribute here, and a sandbox
// in the policy its document's response carries, which holds even if the attribute goes. Both allow the same origin, so this
// page can size the frame, take its link clicks and give it its look. That is safe only because no script can run in it,
// so guardFrames strips allow-scripts off any frame that ever gets it.
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { theme as themeStore, type ThemeName } from "../../lib/theme";
import { toast } from "../../lib/toast";
import { linkRisk } from "./linkRisk";
import { bodyUrl, foldPlainQuote, lookOf, quoteDoc, type FrameTheme, type Look, type MailPrefs } from "./mailDoc";
import { FONTS, readPrefs, SIZES, type ReadPrefs } from "./readPrefs";

const MAX_HEIGHT = 5000;

function mailPrefs(p: ReadPrefs): MailPrefs {
  return { colors: p.colors, font: FONTS[p.font], lineHeight: p.font === "mono" ? 1.55 : 1.62, sizePx: SIZES[p.size] };
}

// :root holds the night palette and only the body's classes change it, so the root element reads as night under every theme.
function frameTheme(name: ThemeName): FrameTheme {
  const body = getComputedStyle(document.body), root = getComputedStyle(document.documentElement);
  const tk = (css: CSSStyleDeclaration, t: string) => css.getPropertyValue(t).trim();
  return { light: name !== "night", panel: tk(body, "--panel"), night: { bg: tk(root, "--bg"), ink: tk(root, "--ink"), primaryInk: tk(root, "--primary-ink"), lineHi: tk(root, "--line-hi"), mute: tk(root, "--mute") } };
}

export interface MailBody { key: number; folder: string; uid: number; has_html?: boolean; from_addr?: string; from_name?: string; subject?: string }

function wireLinks(doc: Document, senderDomain: string | undefined) {
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

// A plain mail comes as one <pre>; its quote is folded away here, where the rule for finding one lives.
function foldQuote(doc: Document) {
  const pre = doc.body.querySelector("pre");
  const fold = pre && foldPlainQuote(pre.textContent ?? "");
  if (!fold) return;
  const details = doc.createElement("details"), summary = doc.createElement("summary"), quoted = doc.createElement("pre");
  details.className = "mm-quote";
  summary.textContent = "··· quoted text";
  quoted.textContent = fold.quoted;
  details.append(summary, quoted);
  pre.textContent = fold.head;
  pre.after(details);
}

function giveLook(doc: Document, { css, inverted }: Look) {
  const style = doc.head.querySelector("style") ?? doc.head.appendChild(doc.createElement("style"));
  style.textContent = css;
  doc.body.classList.toggle("mm-dark", inverted);
}

const met = new WeakSet<Document>();    // the documents that have had what each gets only once

// How tall the mail's own content is, whatever height its frame has now. A mail whose html and body follow the frame's height
// (height:100%, 100vh) measures as tall as the frame, and a fit on that grew the frame by `pad` every time it was asked, or by
// whatever the frame was at partway through its height's transition. So for that moment the two are held to their content,
// above anything the mail says; only the frame's own document is touched, and the page around it doesn't move.
function contentHeight(doc: Document): number | undefined {
  const { documentElement: root, body } = doc;
  if (!body) return undefined;
  const styles = [root, body].map((e) => [e, e.getAttribute("style")] as const);
  for (const [e] of styles) {
    e.style.setProperty("height", "auto", "important");
    e.style.setProperty("min-height", "0", "important");
  }
  const needs = body.scrollHeight;
  for (const [e, style] of styles) {
    if (style === null) e.removeAttribute("style");
    else e.setAttribute("style", style);
  }
  return needs;
}

export function MailFrame({ body, images, minHeight, pad }: { body: MailBody; images: boolean; minHeight: number; pad: number }) {
  const name = themeStore.useValue();
  const prefs = readPrefs.useValue();
  const isHtml = Boolean(body.has_html);
  const look = lookOf(isHtml, mailPrefs(prefs), frameTheme(name));
  const src = bodyUrl(body, images);
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState<number | null>(null);
  const fit = () => {
    const doc = frame.current?.contentDocument;
    if (doc && !doc.URL.endsWith(src)) return;      // the blank document before the mail's: nothing of the mail to fit yet
    setHeight(Math.min(((doc && contentHeight(doc)) || minHeight) + pad, MAX_HEIGHT));
  };
  // True once there is nothing more to wait for: the frame's document is dressed, or it has none this page may reach.
  const dress = () => {
    const doc = frame.current?.contentDocument;
    if (doc === null) fit();
    if (!doc) return true;
    if (doc.readyState === "loading" || !doc.URL.endsWith(src)) return false;
    giveLook(doc, look);
    if (!met.has(doc)) {
      met.add(doc);
      if (!isHtml) foldQuote(doc);
      wireLinks(doc, body.from_addr?.split("@")[1]);
      doc.addEventListener("toggle", fit, true);      // a folded quote opened or closed: the frame takes what it now needs
    }
    fit();
    return true;
  };
  // As soon as the document is parsed, in the frame before it paints, not at load: asked-for images could hold that up for
  // as long as their servers like, with the mail showing unstyled. A hidden tab runs no frames, and load does it instead.
  const dressNow = useEffectEvent(dress);
  useEffect(() => {
    let tick = requestAnimationFrame(function again() {
      if (!dressNow()) tick = requestAnimationFrame(again);
    });
    return () => cancelAnimationFrame(tick);
  }, [src, look.css, look.inverted]);
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
    // A new URL is a new frame: setting src on one that has loaded would add a history entry, and Back would step inside it.
    <iframe
      key={src}
      ref={frame}
      className="rd-iframe"
      title={`Email body — from ${body.from_name || body.from_addr || "sender"}: ${body.subject || "(no subject)"}`}
      sandbox="allow-same-origin"
      src={src}
      style={height === null ? undefined : { height }}
      onLoad={dress}
    />
  );
}

// The message a reply quotes, or how what you typed will read, in the composer. Nothing here reaches inside this one, so its
// sandbox allows nothing at all.
export function QuoteFrame({ html, title = "The message you’re answering" }: { html: string; title?: string }) {
  const name = themeStore.useValue();
  const prefs = readPrefs.useValue();
  return <iframe sandbox="" title={title} srcDoc={quoteDoc(html, mailPrefs(prefs), frameTheme(name))} />;
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
