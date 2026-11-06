import { describe, expect, it } from "vitest";
import { escapeHtml, foldPlainQuote, mailDoc, type FrameTheme, type MailPrefs } from "./mailDoc";

const theme: FrameTheme = { light: true, bg: "#111", panel: "#eee", ink: "#fff", primaryInk: "#9cf", lineHi: "#555", mute: "#888" };
const prefs: MailPrefs = { colors: "auto", font: "serif", lineHeight: 1.6, sizePx: 16 };

describe("a mail's document", () => {
  it("states every channel closed, and sends no referrer", () => {
    const doc = mailDoc({ has_html: true, html: "<p>hi</p>" }, prefs, theme);
    for (const rule of ["default-src 'none'", "script-src 'none'", "object-src 'none'", "form-action 'none'", "frame-src 'none'", "connect-src 'none'"]) expect(doc).toContain(rule);
    expect(doc).toContain('name="referrer" content="no-referrer"');
  });
  it("escapes plain text, so a body can't become markup", () => {
    const doc = mailDoc({ text: "<script>alert(1)</script>" }, prefs, theme);
    expect(doc).not.toContain("<script>");
    expect(doc).toContain("&lt;script&gt;");
  });
  it("says so when there is no body", () => expect(mailDoc({}, prefs, theme)).toContain("(no content)"));
  it("darkens rich mail by inverting it, and media back", () => {
    const doc = mailDoc({ has_html: true, html: "<img>" }, { ...prefs, colors: "dark" }, theme);
    expect(doc).toContain('<div class="mm-dark"><img></div>');
    expect(doc).toContain(".mm-dark img");
  });
  it("leaves rich mail its own colors on the paper themes, and on auto in the night theme", () => {
    expect(mailDoc({ has_html: true, html: "x" }, prefs, theme)).not.toContain("mm-dark");
    expect(mailDoc({ has_html: true, html: "x" }, prefs, { ...theme, light: false })).not.toContain("mm-dark");
  });
  it("sets plain text on the app's own colors at night", () => {
    expect(mailDoc({ text: "x" }, prefs, { ...theme, light: false })).toContain("color:#fff;background:#111");
    expect(mailDoc({ text: "x" }, prefs, theme)).toContain("color:#24221E;background:#FFFDF8");
  });
  it("folds a plain reply's quote", () => {
    const doc = mailDoc({ text: "Yes.\n\nOn Monday Bo wrote:\n> hello" }, prefs, theme);
    expect(doc).toContain('<details class="mm-quote">');
    expect(doc).toContain("<pre>Yes.\n</pre>");
  });
});

describe("foldPlainQuote", () => {
  it("finds an attribution line, or a trailing run of quoted lines", () => {
    expect(foldPlainQuote("Hi\nOn Mon Bo wrote:\nold")).toEqual({ head: "Hi", quoted: "On Mon Bo wrote:\nold" });
    expect(foldPlainQuote("Hi\n&gt; old\n&gt; older\n")).toEqual({ head: "Hi", quoted: "&gt; old\n&gt; older\n" });
  });
  it("won't fold when nothing stands above the quote, or there is no quote", () => {
    expect(foldPlainQuote("On Mon Bo wrote:\nold")).toBeNull();
    expect(foldPlainQuote("\n&gt; old")).toBeNull();
    expect(foldPlainQuote("just text")).toBeNull();
  });
});

it("escapes the five characters", () => expect(escapeHtml(`<a href="x">&'`)).toBe("&lt;a href=&quot;x&quot;&gt;&amp;&#39;"));
