import { describe, expect, it } from "vitest";
import { addrOf, canQuickReply, chip, draftHash, DRAFT_TONES, fmtWhen, forwardSeed, guardSnapshot, guardView, isDraftTone, looksLikeAddr, nearMisses, parseAddrs, quoteLabel, rcptAddr, redirectOf, rePrefix, replySeed, sendLaterPresets, splitChips, type Composer, type Person } from "./model";

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

describe("near misses", () => {
  const composing = (over: Partial<Composer>, people: [string, Person][]) => ({ to: [], cc: [], bcc: [], participants: new Set<string>(), people: new Map(people), ...over }) as unknown as Composer;
  const jon: [string, Person] = ["jon@example.test", { known: false, self: false, near: "john@example.test" }];
  const mine = new Set(["ada@example.test"]);

  it("names a chip the lookup called a typo of someone, once, whichever field holds it", () => {
    const c = composing({ to: [chip("Jon <jon@example.test>")], bcc: [chip("jon@example.test")] }, [jon]);
    expect(nearMisses(c, mine)).toEqual([{ addr: "jon@example.test", near: "john@example.test" }]);
  });

  it("names none the lookup said nothing of, and none it never answered", () => {
    expect(nearMisses(composing({ to: [chip("bo@example.test")] }, [["bo@example.test", { known: false, self: false }]]), mine)).toEqual([]);
    expect(nearMisses(composing({ to: [chip("jon@example.test")] }, []), mine)).toEqual([]);
  });

  it("leaves out you, the thread's people and a chip that isn't an address", () => {
    expect(nearMisses(composing({ to: [chip("ada+x@example.test")] }, [["ada+x@example.test", { known: false, self: false, near: "ada@example.test" }]]), mine)).toEqual([]);
    expect(nearMisses(composing({ to: [chip("jon@example.test")], participants: new Set(["jon@example.test"]) }, [jon]), mine)).toEqual([]);
    expect(nearMisses(composing({ to: [{ v: "jon@example.test", bad: true }] }, [jon]), mine)).toEqual([]);
  });
});

describe("replying", () => {
  it("knows when a Reply-To sends it somewhere other than the sender", () => {
    expect(redirectOf("Desk <Desk@elsewhere.example.test>", "Bo@example.test")).toEqual({ to: "desk@elsewhere.example.test", sender: "bo@example.test" });
    expect(redirectOf("bo@example.test", "BO@example.test")).toBeNull();
    expect(redirectOf("", "bo@example.test")).toBeNull();
    expect(redirectOf("desk@elsewhere.example.test", null)).toBeNull();
    expect(replySeed({ account: "a", from_addr: "bo@example.test", reply_to: "desk@elsewhere.example.test" }, false, new Set()).redirect)
      .toEqual({ to: "desk@elsewhere.example.test", sender: "bo@example.test" });
    expect(replySeed({ account: "a", from_addr: "bo@example.test" }, false, new Set()).redirect).toBeNull();
  });

  it("calls a Reply-To a stranger until someone says otherwise", () => {
    const away = "desk@elsewhere.example.test";
    const composing = (over: Partial<Composer>) => ({ mode: "reply", to: [chip(away)], cc: [], bcc: [], attachments: [], reading: new Map(), participants: new Set<string>(), people: new Map(), ack: {}, redirect: { to: away, sender: "bo@example.test" }, ...over }) as unknown as Composer;
    const stranger = (c: Composer) => guardSnapshot(c, 0, 1, new Set()).redirect?.stranger;
    expect(stranger(composing({}))).toBe(true);
    expect(stranger(composing({ people: new Map([[away, { known: false, self: false }]]) }))).toBe(true);
    expect(stranger(composing({ people: new Map([[away, { known: true, self: false }]]) }))).toBe(false);
    expect(stranger(composing({ participants: new Set([away]) }))).toBe(false);
  });

  it("leaves your tags out of a reply-all as it does your address", () => {
    const seed = replySeed({ account: "ada@example.test", from_addr: "bo@example.test", to: "ada+news@example.test, cy@example.test", cc: "ada@example.test" }, true, new Set(["ada@example.test"]));
    expect(seed.cc).toEqual(["cy@example.test"]);
  });
});

describe("forwarding", () => {
  it("counts the files the server says travel, not what stays with the text", () => {
    const seed = forwardSeed({ account: "a", subject: "x", attachments: [{ travels: true, size: 3 }, { travels: false, size: 9 }, { travels: true, size: 2 }] }, 1000);
    expect(seed.fwdAttach).toBe(2);
    expect(seed.subject).toBe("Fwd: x");
    expect(quoteLabel({ mode: "forward", fwdAttach: 2 }, false)).toBe("▸ forwarded message · 2 attachments");
    expect(quoteLabel({ mode: "reply", fwdAttach: 0 }, true)).toBe("▾ quoted message");
  });

  it("counts nothing the budget ends before", () => {
    const seed = forwardSeed({ account: "a", attachments: [{ travels: true, size: 40 }, { travels: true, size: 70 }, { travels: true, size: 1 }] }, 100);
    expect(seed.fwdAttach).toBe(1);
    expect(forwardSeed({ account: "a" }, 100).fwdAttach).toBe(0);
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
    expect(canQuickReply("ada+news@example.test", mine)).toBe(false);
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

describe("a reply's tone", () => {
  it("is one of the server's, whatever storage held", () => {
    for (const tone of Object.keys(DRAFT_TONES)) expect(isDraftTone(tone)).toBe(true);
    for (const said of ["", "nope", "toString", "constructor", null, undefined, 3]) expect(isDraftTone(said)).toBe(false);
  });
});
