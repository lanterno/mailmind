import { describe, expect, it } from "vitest";
import { addrOf, canQuickReply, chip, draftHash, fmtWhen, forwardSeed, guardView, looksLikeAddr, parseAddrs, quoteLabel, rcptAddr, rePrefix, sendLaterPresets, splitChips } from "./model";

describe("addresses", () => {
  it("finds addresses in a header, and the one a chip's mail goes to", () => {
    expect(parseAddrs('Bea <bea@example.test>, "Cy, Jr" <cy@example.test>')).toEqual(["bea@example.test", "cy@example.test"]);
    expect(addrOf("nobody")).toBe("");
    expect(rcptAddr("spoof@evil.test <Real@Example.test>")).toBe("real@example.test");
    expect(rcptAddr("BEA@example.test")).toBe("bea@example.test");
  });

  it("tells an address from what isn't one", () => {
    expect(looksLikeAddr("bea@example.test")).toBe(true);
    expect(looksLikeAddr("Bea <bea@example.test>")).toBe(true);
    expect(looksLikeAddr("bea")).toBe(false);
    expect(looksLikeAddr("bea@example")).toBe(false);
    expect(chip("nope").bad).toBe(true);
  });

  it("splits what was typed on commas and semicolons", () => {
    expect(splitChips(" a@x.test, b@x.test;; ")).toEqual(["a@x.test", "b@x.test"]);
  });
});

describe("subjects", () => {
  it("prefixes once, swapping what was there", () => {
    expect(rePrefix("Lunch", "Re:")).toBe("Re: Lunch");
    expect(rePrefix("re: Lunch", "Re:")).toBe("Re: Lunch");
    expect(rePrefix("Fwd: Lunch", "Re:")).toBe("Re: Lunch");
    expect(rePrefix("", "Fwd:")).toBe("Fwd:");
    expect(rePrefix(null, "Re:")).toBe("Re:");
  });
});

describe("forwarding", () => {
  it("counts the files that travel, not the pictures inside the text", () => {
    const seed = forwardSeed({ account: "a", subject: "x", attachments: [{ is_inline: true, content_id: "c1" }, { is_inline: false }, { is_inline: true }] });
    expect(seed.fwdAttach).toBe(2);
    expect(seed.subject).toBe("Fwd: x");
    expect(quoteLabel({ mode: "forward", fwdAttach: 2 }, false)).toBe("▸ forwarded message · 2 attachments");
    expect(quoteLabel({ mode: "reply", fwdAttach: 0 }, true)).toBe("▾ quoted message");
  });
});

describe("the draft's hash", () => {
  const p = { to: ["a"], cc: [], bcc: [], subject: "s", text: "t", account: "x", attachments: [] } as never;
  it("moves with what a save would write", () => {
    expect(draftHash(p)).toBe(draftHash(p));
    expect(draftHash(p)).not.toBe(draftHash({ ...(p as object), text: "u" } as never));
  });
});

describe("quick replies", () => {
  const mine = new Set(["ada@example.test"]);
  it("are for people you could answer", () => {
    expect(canQuickReply("bea@example.test", mine)).toBe(true);
    expect(canQuickReply("ada@example.test", mine)).toBe(false);
    expect(canQuickReply("no-reply@example.test", mine)).toBe(false);
    expect(canQuickReply("notifications@example.test", mine)).toBe(false);
    expect(canQuickReply("bea@example.test", mine, { isJunk: true })).toBe(false);
    expect(canQuickReply("bea@example.test", mine, { bucket: "receipts" })).toBe(false);
    expect(canQuickReply("", mine)).toBe(false);
  });
});

describe("when", () => {
  const noon = new Date(2026, 10, 2, 12, 0);   // a Monday
  const at = (d: Date) => Math.floor(d.getTime() / 1000);
  it("says today and tomorrow in words", () => {
    expect(fmtWhen(at(new Date(2026, 10, 2, 15, 0)), noon)).toMatch(/^today /);
    expect(fmtWhen(at(new Date(2026, 10, 3, 8, 0)), noon)).toMatch(/^tomorrow /);
    expect(fmtWhen(at(new Date(2026, 10, 9, 8, 0)), noon)).not.toMatch(/^(today|tomorrow)/);
  });

  it("offers four times to send later, all ahead of now", () => {
    const presets = sendLaterPresets(noon);
    expect(presets.map((p) => p[0])).toEqual(["In 1 hour", "This evening", "Tomorrow 8 AM", "Monday 8 AM"]);
    expect(presets.every((p) => p[1] > at(noon))).toBe(true);
    expect(new Date(presets[3]![1] * 1000).getDay()).toBe(1);
    expect(new Date(presets[3]![1] * 1000).getDate()).toBe(9);   // the next Monday, not today
  });

  it("moves this evening to tomorrow once it has passed", () => {
    const late = new Date(2026, 10, 2, 19, 0);
    expect(new Date(sendLaterPresets(late)[1]![1] * 1000).getDate()).toBe(3);
  });
});

describe("the check's lines", () => {
  it("says schedule when it's a Later send, and has no way past a hard one", () => {
    const soft = guardView([{ key: "subject", block: "confirm" }], 100, 0, () => "");
    expect(soft.hard).toBe(false);
    expect(soft.said).toMatch(/to schedule anyway/);
    const hard = guardView([{ key: "norcpt", block: "hard" }], 0, 0, () => "");
    expect(hard.hard).toBe(true);
    expect(hard.said).toMatch(/Press Escape to keep editing\.$/);
  });

  it("writes the time of a Later that has passed", () => {
    const late = guardView([{ key: "late", block: "hard", when: 5 }], 5, 0, () => "today 9:00");
    expect(late.lines[0]!.text).toMatch(/today 9:00/);
  });

  it("leaves a soft line out of what must be waved through", () => {
    const g = guardView([{ key: "new", block: "soft", addrs: ["a@x.test"] }, { key: "subject", block: "confirm" }], 0, 0, () => "");
    expect(g.keys).toEqual(["subject"]);
    expect(g.lines[0]!.soft).toBe(true);
  });
});
