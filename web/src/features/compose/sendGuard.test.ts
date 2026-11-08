// The send-time checks, on their word lists (tests/fixtures/send_guard_words.json) and the exact copy.
import { describe, expect, it } from "vitest";
import WORDS from "../../../../tests/fixtures/send_guard_words.json";
import { guardAttachWord as W, guardBlocks, guardCheck, guardFanout, guardHard, guardLine, guardList, guardSubjectEmpty, guardTypedText, isMine, mineSet, untagged,
  type GuardSnapshot, type GuardWarning } from "./sendGuard";

const S = (o: Partial<GuardSnapshot> = {}): GuardSnapshot => ({ mode: "new", subject: "Plans", subject0: "", body: "", sigBlock: "", nAttach: 0,
  fwdAttach: 0, reading: [], sendAfter: 0, now: 1e9, to: ["bo@example.test"], cc: [], mine: ["ada@example.test"], autoCc: null, fresh: [],
  ack: { subject: false, attach: false, fanout: 0 }, ...o });
const keys = (w: GuardWarning[]) => w.map((x) => x.key);
const ppl = (n: number) => Array.from({ length: n }, (_, i) => `p${i}@example.test`);
const fanout = (o: Partial<GuardSnapshot>) => guardCheck(S(o)).find((x) => x.key === "fanout");
const reply = { mode: "reply", subject: "Re: x", subject0: "Re: x" };

describe("the attachment word", () => {
  it.each(WORDS.strong)("strong in every mode: %s", (t) => {
    expect(W(t, false)).toBeTruthy();
    expect(W(t, true)).toBeTruthy();
  });
  it.each(WORDS.weak)("weak, only where allowed: %s", (t) => {
    expect(W(t, false)).toBe("");
    expect(W(t, true)).toBeTruthy();
  });
  it.each(WORDS.never)("never: %s", (t) => expect(W(t, true)).toBe(""));
  it.each(WORDS.reply_never)("a reply about their file never warns: %s", (t) => {
    expect(W(t, false)).toBe("");
    expect(keys(guardCheck(S({ ...reply, body: t })))).toEqual([]);
  });
  it("is echoed as typed", () => expect(W("Voici le devis en pièce jointe.", false)).toBe("en pièce jointe"));
});

// Long runs with no spaces: a pasted base64 blob, minified JSON, a line of dashes. It was 2-5 s when it was wrong.
const SLOW_MS = 1000;
const SLOW: [string, string][] = [["base64", "QUJD".repeat(12500)], ["dashes", "-".repeat(50000)], ["underscores", "_".repeat(50000)],
  ["a-", "a-".repeat(25000)], ["dots", ".".repeat(50000)], ["ats", "@".repeat(50000)], ["arabic", "ال".repeat(25000)], ["commas", ", ".repeat(25000)],
  ["gap", "a" + " ".repeat(49998) + "b"], ["json", JSON.stringify(Array.from({ length: 4000 }, (_, i) => ({ k: i, v: "x" + i }))).slice(0, 50000)]];

it.each(SLOW)("checks long unbroken text fast: %s", (_name, b) => {
  const t0 = performance.now();
  for (const mode of ["new", "reply"]) {
    guardCheck(S({ mode, body: b }));
    guardCheck(S({ mode, subject: b }));
  }
  expect(performance.now() - t0).toBeLessThan(SLOW_MS);
});

describe("what you typed", () => {
  it("leaves out signature and quote", () => {
    expect(guardTypedText("see attached\n\n-- \nAda", "\n\n-- \nAda")).toBe("see attached");
    expect(guardTypedText("Hi there\n\n-- \nAda, attachments policy", "\n\n-- \nZed").trim()).toBe("Hi there");
    expect(guardTypedText("Hi there\n-- \nAda\nAttachments may contain viruses.", "")).toBe("Hi there");
    expect(guardTypedText("Thanks\nOn Mon, 3 Feb 2026, Dana <dana@example.test> wrote:\n> see attached\nsee attached", "")).toBe("Thanks");
    expect(guardTypedText("FYI\n\n---------- Forwarded message ----------\nFrom: Dana\nsee attached", "").trim()).toBe("FYI");
    expect(guardTypedText("Noted\n-----Original Message-----\nsee attached", "")).toBe("Noted");
    expect(guardTypedText("> see attached\nok, thanks", "")).toBe("ok, thanks");
    expect(keys(guardCheck(S({ body: "Hello\n-- \nAda\nAttachments may contain viruses." })))).toEqual([]);
  });
  it("keeps text on either side of your signature", () => {
    expect(guardTypedText("Hi Bo,\nnotes below.\n\n-- \nAda Lovelace\n\nP.S. I attached the deck.", "\n\n-- \nAda Lovelace"))
      .toBe("Hi Bo,\nnotes below.\n\n\n\nP.S. I attached the deck.");
    expect(guardTypedText("\n\n-- \nAda Lovelace\nHi Bo, the deck is attached.", "\n\n-- \nAda Lovelace").trim()).toBe("Hi Bo, the deck is attached.");
    expect(guardTypedText("See x-- \nAda Lovelace", "\n\n-- \nAda Lovelace")).toBe("See x-- \nAda Lovelace");
    expect(keys(guardCheck(S({ ...reply, sigBlock: "\n\n-- \nAda", body: "Thanks.\n\n-- \nAda\n\nP.S. The deck is attached." })))).toEqual(["attach"]);
  });
});

describe("the subject", () => {
  it("counts as empty", () => {
    expect([guardSubjectEmpty("", "reply"), guardSubjectEmpty("  ", "new"), guardSubjectEmpty("Re:", "reply"), guardSubjectEmpty("Fwd:", "forward"),
      guardSubjectEmpty("Re:", "new")]).toEqual([true, true, false, false, true]);
    expect(guardCheck(S({ subject: "", ack: { subject: true, attach: false, fanout: 0 } }))).toEqual([]);
  });
  it("isn't your wording when inherited", () => {
    const inherited = { mode: "reply", subject0: "Re: Contract attached" };
    expect(keys(guardCheck(S({ ...inherited, subject: "Re: Contract attached" })))).toEqual([]);
    expect(keys(guardCheck(S({ ...inherited, subject: "Re: Contract attached, v2" })))).toEqual(["attach"]);
    expect(keys(guardCheck(S({ subject: "Contract attached" })))).toEqual(["attach"]);
  });
});

it("has a file still reading as the only line", () => {
  expect(guardCheck(S({ subject: "", body: "see attached", reading: ["a.pdf", "b.pdf"] }))).toEqual([{ key: "reading", block: "hard", name: "a.pdf", more: 1 }]);
});

it("stops a passed send time", () => {
  expect(keys(guardCheck(S({ sendAfter: 1e9 + 3 })))).toEqual(["late"]);
  expect(keys(guardCheck(S({ sendAfter: 1e9 + 3600 })))).toEqual([]);
  expect(guardHard(guardCheck(S({ sendAfter: 1e9 - 60 })))).toBe(true);
});

it("knows what counts as attached", () => {
  expect(keys(guardCheck(S({ mode: "forward", body: "see attached", fwdAttach: 2 })))).toEqual([]);
  expect(keys(guardCheck(S({ mode: "forward", body: "see attached", fwdAttach: 0 })))).toEqual(["attach"]);
  expect(keys(guardCheck(S({ body: "see attached", nAttach: 1 })))).toEqual([]);
  expect(keys(guardCheck(S({ body: "see attached", ack: { subject: false, attach: true, fanout: 0 } })))).toEqual([]);
  expect(keys(guardCheck(S({ ...reply, body: "the attachment you sent" })))).toEqual([]);
});

describe("reply all", () => {
  const all = { mode: "replyall" };
  it("counts each person once and not you", () => {
    expect(fanout({ ...all, to: ppl(1), cc: ppl(6).slice(1) })).toEqual({ key: "fanout", block: "confirm", n: 6, removable: 0 });
    expect(keys(guardCheck(S({ ...all, to: ppl(1), cc: ppl(5).slice(1) })))).toEqual([]);
    expect(keys(guardCheck(S({ ...all, to: ppl(1), cc: [...ppl(5).slice(1), "ada@example.test"] })))).toEqual([]);
    expect(keys(guardCheck(S({ ...all, to: ppl(3), cc: ppl(5) })))).toEqual([]);
    expect(keys(guardCheck(S({ mode: "reply", to: ppl(4), cc: ppl(10).slice(4) })))).toEqual([]);
  });
  it("is waved through up to that many people", () => {
    const ack = { subject: false, attach: false, fanout: 8 };
    expect(keys(guardCheck(S({ ...all, to: ppl(1), cc: ppl(7).slice(1), ack })))).toEqual([]);
    expect(keys(guardCheck(S({ ...all, to: ppl(1), cc: ppl(8).slice(1), ack })))).toEqual([]);
    expect(keys(guardCheck(S({ ...all, to: ppl(1), cc: ppl(9).slice(1), ack })))).toEqual(["fanout"]);
  });
  it("offers to remove only people still copied in", () => {
    const f = fanout({ ...all, to: ["mira@example.test"], cc: [...ppl(4), "own1@example.test", "own2@example.test"], autoCc: [...ppl(6), "ada@example.test"] });
    expect(f).toMatchObject({ removable: 4 });
    expect(fanout({ ...all, to: ppl(1), cc: ppl(7).slice(1) })).toMatchObject({ removable: 0 });
  });
});

describe("your own address, however it is tagged", () => {
  const mine = mineSet(["Ada@example.test"]);
  it("is yours as the mailbox and as any tag of it, and no other", () => {
    expect([isMine("ada@example.test", mine), isMine("ada+news@example.test", mine), isMine("adam@example.test", mine), isMine("bo+ada@example.test", mine)])
      .toEqual([true, true, false, false]);
    expect([untagged("ada+news@example.test"), untagged("ada@example.test"), untagged("+only@example.test"), untagged("no-at")])
      .toEqual(["ada@example.test", "ada@example.test", "+only@example.test", "no-at"]);
  });
  it("takes the plain mailbox of an account that is itself tagged", () => {
    const tagged = mineSet(["ada+work@example.test"]);
    expect([isMine("ada@example.test", tagged), isMine("ada+other@example.test", tagged)]).toEqual([true, true]);
  });
  it("counts for no one in a reply-all's head count", () => {
    expect(guardFanout(["bo@example.test", "ada+news@example.test"], [], ["ada@example.test"])).toBe(1);
  });
  it("is left out of what a reply-all offers to remove", () => {
    const f = fanout({ mode: "replyall", to: ["bo@example.test"], cc: [...ppl(5), "ada+news@example.test"], autoCc: [...ppl(5), "ada+news@example.test"] });
    expect(f).toMatchObject({ n: 6, removable: 5 });
  });
});

describe("an address one typo from someone you write to", () => {
  const pair = { addr: "jon@example.test", near: "john@example.test" };
  const other = { addr: "kat@example.test", near: "kate@example.test" };

  it("holds the send, naming who it reads as", () => {
    const w = guardCheck(S({ near: [pair] }));
    expect(w).toEqual([{ key: "near", block: "confirm", pairs: [pair] }]);
    expect(guardBlocks(w)).toBe(true);
  });

  it("is said once for all of them, the first by name", () => {
    expect(guardCheck(S({ near: [pair, other] }))).toEqual([{ key: "near", block: "confirm", pairs: [pair, other] }]);
  });

  it("isn't asked again for one waved through, and is for one that wasn't", () => {
    const ack = { subject: false, attach: false, fanout: 0, near: [pair.addr] };
    expect(keys(guardCheck(S({ near: [pair], ack })))).toEqual([]);
    expect(guardCheck(S({ near: [pair, other], ack }))).toEqual([{ key: "near", block: "confirm", pairs: [other] }]);
  });

  it("leaves a person it names out of the new-to-you line, and no one else", () => {
    const w = guardCheck(S({ near: [pair], fresh: [pair.addr, "new@example.test"] }));
    expect(w.map((x) => x.key)).toEqual(["near", "new"]);
    expect(w.find((x) => x.key === "new")).toMatchObject({ addrs: ["new@example.test"] });
  });

  it("says so in words, and offers the address it reads as", () => {
    expect(guardLine({ key: "near", pairs: [pair] })).toEqual({
      text: "“jon@example.test” is one typo away from john@example.test, someone you’ve written to.",
      fix: { kind: "near", label: "Use john@example.test", swap: { from: pair.addr, to: pair.near } },
    });
    expect(guardLine({ key: "near", pairs: [pair, other] }).text).toMatch(/1 more looks the same\.$/);
    expect(guardLine({ key: "near", pairs: [pair, other, { addr: "z@x.test", near: "y@x.test" }] }).text).toMatch(/2 more look the same\.$/);
  });
});

it("never stops a send for someone new alone", () => {
  const w = guardCheck(S({ fresh: ["new@example.test"] }));
  expect([keys(w), guardBlocks(w)]).toEqual([["new"], false]);
  expect(guardBlocks(guardCheck(S({ subject: "", fresh: ["new@example.test"] })))).toBe(true);
});

it("lists names", () => {
  expect([[], ["a"], ["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"]].map(guardList)).toEqual(["", "a", "a and b", "a, b and c", "a, b and 2 more"]);
});

it("words every line exactly", () => {
  expect([guardLine({ key: "reading", name: "a.pdf", more: 0 }), guardLine({ key: "reading", name: "a.pdf", more: 2 }),
    guardLine({ key: "late", whenText: "today 6:00 PM" }), guardLine({ key: "subject" }), guardLine({ key: "attach", word: "pièce jointe" }),
    guardLine({ key: "fanout", n: 7, removable: 6 }), guardLine({ key: "fanout", n: 7, removable: 0 }),
    guardLine({ key: "new", addrs: ["a@example.test", "b@example.test"] }), guardLine({ key: "unchecked" }), guardLine({ key: "norcpt" }),
    guardLine({ key: "bad", v: "bo@", more: 0 }), guardLine({ key: "bad", v: "bo@", more: 1 }), guardLine({ key: "bad", v: "bo@", more: 2 })]).toEqual([
    { text: "Still adding a.pdf. Send again once it’s added.", fix: null },
    { text: "Still adding a.pdf and 2 more. Send again once they’re added.", fix: null },
    { text: "The time you picked, today 6:00 PM, has passed.", fix: { kind: "time", label: "Pick a new time" } },
    { text: "There’s no subject.", fix: { kind: "subject", label: "Add a subject" } },
    { text: "You wrote “pièce jointe”, but nothing is attached.", fix: { kind: "attach", label: "Attach a file" } },
    { text: "This reply goes to 7 people.", fix: { kind: "trim", label: "Remove the 6 copied from the original" } },
    { text: "This reply goes to 7 people.", fix: { kind: "review", label: "Review recipients" } },
    { text: "New to you: a@example.test and b@example.test.", fix: null },
    { text: "The checks before sending couldn’t run.", fix: null },
    { text: "Add at least one recipient.", fix: { kind: "to", label: "Add one" } },
    { text: "“bo@” isn’t a valid address.", fix: { kind: "bad", label: "Edit it" } },
    { text: "“bo@” isn’t a valid address. 1 more needs fixing too.", fix: { kind: "bad", label: "Edit it" } },
    { text: "“bo@” isn’t a valid address. 2 more need fixing too.", fix: { kind: "bad", label: "Edit it" } }]);
});
