// Links pasted into the composer: what comes off them and what stays. Every host is fictional except the redirectors themselves.
import { describe, expect, it } from "vitest";
import { cleanLinks as C } from "./linkClean";

describe("tracking parameters", () => {
  it.each([
    ["https://shop.example.test/a?utm_source=news&utm_medium=email", "https://shop.example.test/a"],
    ["https://shop.example.test/a?id=7&utm_campaign=x&page=2", "https://shop.example.test/a?id=7&page=2"],
    ["https://shop.example.test/a?UTM_Source=x", "https://shop.example.test/a"],
    ["https://shop.example.test/a?fbclid=abc", "https://shop.example.test/a"],
    ["https://shop.example.test/a?gclid=abc&msclkid=def&id=1", "https://shop.example.test/a?id=1"],
    ["https://shop.example.test/a?utm_source=x#frag", "https://shop.example.test/a#frag"],
    ["https://shop.example.test/a?id=1&utm_id=2#frag", "https://shop.example.test/a?id=1#frag"],
  ])("are dropped: %s", (link, want) => expect(C(link)).toBe(want));

  it("leave the rest of the link exactly as written", () => {
    expect(C("https://shop.example.test/a?q=hello%20world&sig=a+b%2Fc&utm_source=x")).toBe("https://shop.example.test/a?q=hello%20world&sig=a+b%2Fc");
  });

  it("leave a parameter alone that only starts like one", () => {
    expect(C("https://shop.example.test/a?utm=1&utmost=2&fbclids=3&ref=4&source=5&si=6"))
      .toBe("https://shop.example.test/a?utm=1&utmost=2&fbclids=3&ref=4&source=5&si=6");
  });

  it("leave a link with none as it was, down to the byte", () => {
    for (const link of ["https://shop.example.test/", "http://SHOP.example.test/A%20b", "https://shop.example.test/a?b=1#c", "http://SHOP.example.test/A%20b?x=%7Ey"]) expect(C(link)).toBe(link);
  });

  it("are left alone in a route after the fragment", () => {
    expect(C("https://app.example.test/#/page?utm_source=x")).toBe("https://app.example.test/#/page?utm_source=x");
  });
});

describe("redirect wrappers", () => {
  const target = "https://real.example.test/page?id=1";
  const e = encodeURIComponent(target);
  it.each([
    ["Google", `https://www.google.com/url?q=${e}&sa=D&ust=1`],
    ["Google, by country", `https://www.google.co.uk/url?url=${e}`],
    ["Facebook", `https://l.facebook.com/l.php?u=${e}&h=AT0x`],
    ["Facebook on a phone", `https://lm.facebook.com/l.php?u=${e}&h=AT0x`],
    ["Instagram", `https://l.instagram.com/?u=${e}&e=AT0`],
    ["LinkedIn", `https://www.linkedin.com/redir/redirect?url=${e}&urlhash=x`],
    ["LinkedIn's safety page", `https://www.linkedin.com/safety/go?url=${e}&trk=x`],
    ["Outlook Safe Links", `https://nam04.safelinks.protection.outlook.com/?url=${e}&data=05%7C01%7C&sdata=x&reserved=0`],
    ["YouTube", `https://www.youtube.com/redirect?q=${e}&v=x`],
    ["Slack", `https://slack-redir.net/link?url=${e}`],
  ])("come off: %s", (_name, link) => expect(C(link)).toBe(target));

  it("come off layer by layer, and what's inside is cleaned too", () => {
    const inner = `https://real.example.test/page?utm_source=x&id=1`;
    const google = `https://www.google.com/url?q=${encodeURIComponent(inner)}`;
    const outlook = `https://eur01.safelinks.protection.outlook.com/?url=${encodeURIComponent(google)}&data=x`;
    expect(C(outlook)).toBe("https://real.example.test/page?id=1");
  });

  it("stop after so many layers", () => {
    let link = "https://real.example.test/";
    for (let i = 0; i < 6; i++) link = `https://www.google.com/url?q=${encodeURIComponent(link)}`;
    expect(C(link)).toContain("google.com/url");
  });

  it("are left on when their target is no link, or the host only looks like theirs", () => {
    for (const link of [
      "https://www.google.com/url?q=javascript:alert(1)",
      "https://www.google.com/url?q=not-a-link",
      "https://www.google.com/url?q=https%3A%2F%2F%5Bbad",
      "https://www.google.com/url?q=https%3A%2F%2Freal.example.test%2Fa%20b",
      "https://www.google.com/url?sa=D",
      "https://www.google.com/search?q=https%3A%2F%2Freal.example.test%2F",
      "https://google.example.test/url?q=https%3A%2F%2Freal.example.test%2F",
      "https://notgoogle.com/url?q=https%3A%2F%2Freal.example.test%2F",
      "https://l.facebook.com/other?u=https%3A%2F%2Freal.example.test%2F",
    ]) expect(C(link)).toBe(link);
  });
});

describe("in pasted text", () => {
  it("cleans each link and leaves the words", () => {
    expect(C("See https://a.example.test/?utm_source=x and http://b.example.test/?fbclid=y for more."))
      .toBe("See https://a.example.test/ and http://b.example.test/ for more.");
  });

  it("leaves the sentence's punctuation after a link", () => {
    expect(C("(see https://a.example.test/x?utm_source=y).")).toBe("(see https://a.example.test/x).");
    expect(C("See https://a.example.test/x?utm_source=y, then")).toBe("See https://a.example.test/x, then");
    expect(C("Look: https://a.example.test/x?utm_source=y!")).toBe("Look: https://a.example.test/x!");
    expect(C("見て https://a.example.test/x?utm_source=y。")).toBe("見て https://a.example.test/x。");
  });

  it("keeps a closing bracket that belongs to the path", () => {
    expect(C("https://a.example.test/Foo_(bar)")).toBe("https://a.example.test/Foo_(bar)");
    expect(C("https://a.example.test/Foo_(bar)?utm_source=x")).toBe("https://a.example.test/Foo_(bar)");
  });

  it("finds a link inside angle brackets, and takes nothing that isn't one", () => {
    expect(C("<https://a.example.test/?utm_source=x>")).toBe("<https://a.example.test/>");
    expect(C("no links here, utm_source=x")).toBe("no links here, utm_source=x");
    expect(C("")).toBe("");
  });

  it("stays fast on a long paste", () => {
    const long = "https://a.example.test/?utm_source=x " + "word ".repeat(40000) + "https://b.example.test/" + ".".repeat(50000);
    const t0 = performance.now();
    expect(C(long).startsWith("https://a.example.test/ word")).toBe(true);
    expect(performance.now() - t0).toBeLessThan(1000);
  });
});
