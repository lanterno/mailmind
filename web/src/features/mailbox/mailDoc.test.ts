import { describe, expect, it } from "vitest";
import { bodyUrl, foldPlainQuote, lookOf, quoteDoc, type FrameTheme, type MailPrefs } from "./mailDoc";

const theme: FrameTheme = { light: true, panel: "#eee", night: { bg: "#111", ink: "#fff", primaryInk: "#9cf", lineHi: "#555", mute: "#888" } };
const prefs: MailPrefs = { colors: "auto", font: "serif", lineHeight: 1.6, sizePx: 16 };

describe("a body's URL", () => {
  it("names the copy by its key, folder and uid", () => expect(bodyUrl({ key: 7, folder: "INBOX", uid: 3 }, false)).toBe("/api/message/body.html?key=7&folder=INBOX&uid=3&images=0"));
  it("says when images are asked for", () => expect(bodyUrl({ key: 7, folder: "INBOX", uid: 3 }, true)).toBe("/api/message/body.html?key=7&folder=INBOX&uid=3&images=1"));
  it("takes a folder of any name", () => expect(bodyUrl({ key: 7, folder: "Lists/Yarn & Wool", uid: 3 }, false)).toBe("/api/message/body.html?key=7&folder=Lists%2FYarn%20%26%20Wool&uid=3&images=0"));
});

describe("a mail's look", () => {
  it("darkens rich mail by inverting it, and media back", () => {
    const look = lookOf(true, { ...prefs, colors: "dark" }, theme);
    expect(look.inverted).toBe(true);
    expect(look.css).toContain(".mm-dark img");
    expect(look.css).toContain("html{background:#111}");
  });
  it("leaves rich mail its own colors on the paper themes, and on auto in the night theme", () => {
    expect(lookOf(true, prefs, theme).inverted).toBe(false);
    expect(lookOf(true, prefs, { ...theme, light: false }).inverted).toBe(false);
  });
  it("sets plain text on the app's own colors at night, and never inverts it", () => {
    expect(lookOf(false, prefs, { ...theme, light: false })).toMatchObject({ inverted: false });
    expect(lookOf(false, prefs, { ...theme, light: false }).css).toContain("color:#fff;background:#111");
    expect(lookOf(false, prefs, theme).css).toContain("color:#24221E;background:#FFFDF8");
  });
  it("sets dark mail on the night palette under a paper theme too", () => {
    expect(lookOf(false, { ...prefs, colors: "dark" }, theme).css).toContain("color:#fff;background:#111");
    expect(lookOf(false, { ...prefs, colors: "dark" }, theme).css).toContain("a{color:#9cf}");
    expect(lookOf(false, { ...prefs, colors: "dark" }, theme).css).toContain("border-left:3px solid #555");
    expect(lookOf(true, { ...prefs, colors: "dark" }, theme).css).toContain("html{background:#111}");
  });
  it("typesets plain text by the reading settings, and leaves rich mail the sender's own type", () => {
    expect(lookOf(false, { ...prefs, font: "mono", sizePx: 20 }, theme).css).toContain("font:20px/1.6 mono");
    expect(lookOf(true, { ...prefs, font: "mono", sizePx: 20 }, theme).css).not.toContain("mono");
  });
  it("draws the fold in every look", () => {
    for (const look of [lookOf(true, prefs, theme), lookOf(true, { ...prefs, colors: "dark" }, theme), lookOf(false, prefs, theme)]) expect(look.css).toContain("details.mm-quote");
  });
});

describe("a quote's document", () => {
  it("states every channel closed, and sends no referrer", () => {
    const doc = quoteDoc("<p>hi</p>", prefs, theme);
    for (const rule of ["default-src 'none'", "script-src 'none'", "object-src 'none'", "form-action 'none'", "frame-src 'none'", "connect-src 'none'", "img-src data:;"]) expect(doc).toContain(rule);
    expect(doc).not.toContain("https:");
    expect(doc).toContain('name="referrer" content="no-referrer"');
  });
  it("inverts by its body's class when dark", () => {
    expect(quoteDoc("<img>", { ...prefs, colors: "dark" }, theme)).toContain('<body class="mm-dark"><img></body>');
    expect(quoteDoc("<img>", prefs, theme)).toContain("<body><img></body>");
  });
});

describe("foldPlainQuote", () => {
  it("finds an attribution line, or a trailing run of quoted lines", () => {
    expect(foldPlainQuote("Hi\nOn Mon Bo wrote:\nold")).toEqual({ head: "Hi", quoted: "On Mon Bo wrote:\nold" });
    expect(foldPlainQuote("Hi\n> old\n> older\n")).toEqual({ head: "Hi", quoted: "> old\n> older\n" });
  });
  it("won't fold when nothing stands above the quote, or there is no quote", () => {
    expect(foldPlainQuote("On Mon Bo wrote:\nold")).toBeNull();
    expect(foldPlainQuote("\n> old")).toBeNull();
    expect(foldPlainQuote("just text")).toBeNull();
  });
});
