// The twin of script_draft_resume_ui.py, on the same cases and the exact copy.
import { describe, expect, it } from "vitest";
import { resumeFields, resumeHidesAi, resumeNotice, resumeOpts, resumeSig } from "./draftResume";

const TAIL = "\n\nOn Mon, 02 Feb 2026, Bo Park <bo@example.test> wrote:\n> Shall we meet Saturday?\n";
const D = { draft_id: 12, mode: "reply", account: "ada@example.test", subject: "Re: Plans", to: ["Bo Park <bo@example.test>"],
  cc: [], bcc: ["dee@example.test"], tail_text: TAIL, in_reply_to: "<orig1@example.test>",
  references: "<a@example.test> <orig1@example.test>", orig_pk: 7, participants: ["bo@example.test"] };

describe("resumeOpts", () => {
  it("opens a reopened reply like a fresh one", () => {
    const o = resumeOpts(D);
    expect([o.mode, o.from, o.draftId, o.origPk, o.noSig]).toEqual(["reply", "ada@example.test", 12, 7, true]);
    expect([o.to, o.cc, o.bcc]).toEqual([["Bo Park <bo@example.test>"], [], ["dee@example.test"]]);
    expect([o.subject, o.subject0, o.participants]).toEqual(["Re: Plans", "Re: Plans", ["bo@example.test"]]);
    expect(o.quote).toEqual({ html: null, text: TAIL.replace(/^\n+/, "") });
    expect(o.fwdAttach).toBe(0);
    expect(o.resume).toEqual({ in_reply_to: "<orig1@example.test>", references: "<a@example.test> <orig1@example.test>", tail_text: TAIL });
  });
  it("gives a whole draft no quote to fold", () => {
    const o = resumeOpts({ ...D, tail_text: "", orig_pk: null });
    expect([o.quote, o.origPk, o.resume.tail_text]).toEqual([null, null, ""]);
  });
  it("keeps a forward's section and no thread", () => {
    const o = resumeOpts({ ...D, mode: "forward", tail_text: "\n\n---------- Forwarded message ----------\nFrom: Bo",
      in_reply_to: "", references: "", orig_pk: null, participants: [] });
    expect(o.mode).toBe("forward");
    expect(o.quote?.text.startsWith("---------- Forwarded message")).toBe(true);
    expect([o.resume.in_reply_to, o.origPk]).toEqual(["", null]);
  });
  it("carries the draft's version", () => {
    expect(resumeOpts({ ...D, version: 4 }).version).toBe(4);
  });
  it("has defaults for a bare answer", () => {
    const o = resumeOpts({ draft_id: 3, account: "ada@example.test" });
    expect([o.mode, o.to, o.subject, o.quote, o.origPk, o.version]).toEqual(["new", [], "", null, null, null]);
    expect(o.resume).toEqual({ in_reply_to: "", references: "", tail_text: "" });
  });
});

it("sends the resume fields on every save and send", () => {
  expect([resumeFields(null), resumeFields(undefined)]).toEqual([{}, {}]);
  expect(resumeFields({ in_reply_to: "<x@example.test>", references: null, tail_text: "q" }))
    .toEqual({ resume: { in_reply_to: "<x@example.test>", references: "", tail_text: "q" } });
});

it("hides the AI draft only when the quote is in the box", () => {
  expect([resumeHidesAi(null), resumeHidesAi({ resume: null, quote: null }), resumeHidesAi({ resume: {}, quote: { text: "q" } }),
    resumeHidesAi({ resume: {}, quote: null })]).toEqual([false, false, false, true]);
});

it("finds the signature a reopened text ends with", () => {
  expect([resumeSig("Saturday works.\n\n-- \nAda", "Ada"), resumeSig("Saturday works.\n\n-- \nAda L.", "Ada"),
    resumeSig("Hi\n\n-- \nAda\nP.S. later", "Ada"), resumeSig("Hi", ""), resumeSig("", "Ada"), resumeSig(null, "Ada"),
    resumeSig("Hi\n-- \nAda", "Ada")]).toEqual(["\n\n-- \nAda", "", "", "", "", "", ""]);
});

it("words its notes exactly", () => {
  const got = [resumeNotice(null), resumeNotice({ inline: 0, bcc_dropped: 0, plain: false }), resumeNotice({ inline: 1 }), resumeNotice({ inline: 3 }),
    resumeNotice({ bcc_dropped: 2 }), resumeNotice({ plain: true }), resumeNotice({ inline: 2, bcc_dropped: 1, plain: true })];
  expect(got).toEqual(["", "", "1 picture in the text is now an attachment.", "3 pictures in the text are now attachments.",
    "The Bcc wasn’t restored, because this draft changed since MailMind saved it.",
    "This draft opens as plain text, so its formatting won’t be kept when you save or send it.",
    "2 pictures in the text are now attachments. The Bcc wasn’t restored, because this draft changed since MailMind saved it. This draft opens as plain text, so its formatting won’t be kept when you save or send it."]);
  expect(got.some((t) => t.includes("!"))).toBe(false);
});
