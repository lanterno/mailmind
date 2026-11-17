import { describe, expect, it } from "vitest";
import { ApiError } from "./api/client";
import { READ_ONLY_NOTE, READ_ONLY_REFUSED, readOnlyOr, refusedReadOnly } from "./readOnly";

const readOnly = new ApiError(409, { type: "/problems/read-only", title: "MailMind is running read-only", read_only: true });
const busy = new ApiError(409, { type: "/problems/busy", busy: true });

describe("read only, in words", () => {
  // The page's Settings has no switch for it, so neither names one.
  it("says while it is on that nothing on a mail server changes, and what is off", () => {
    expect(READ_ONLY_NOTE).toBe("Read only is on: MailMind changes nothing on your mail servers, so sending, filing and starring are "
      + "turned off.");
  });
  it("says of a change it refused that nothing was changed", () => {
    expect(READ_ONLY_REFUSED).toBe("Read only is on, so nothing was changed.");
  });
  it("knows its refusal by the problem's read_only, ahead of any other 409", () => {
    expect(refusedReadOnly(readOnly)).toBe(true);
    expect(refusedReadOnly(busy)).toBe(false);
    expect(refusedReadOnly(new ApiError(409, { read_only: true, busy: true }))).toBe(true);
    expect(refusedReadOnly(new Error("MailMind is running read-only"))).toBe(false);
    expect(refusedReadOnly(undefined)).toBe(false);
    expect(refusedReadOnly(null)).toBe(false);
  });
  it("says its own words for its refusal, and the caller's for anything else", () => {
    expect(readOnlyOr(readOnly, "It’s being tried now. Try again in a moment")).toBe(READ_ONLY_REFUSED);
    expect(readOnlyOr(busy, "It’s being tried now. Try again in a moment")).toBe("It’s being tried now. Try again in a moment");
    expect(readOnlyOr(new Error("offline"), "Couldn’t archive")).toBe("Couldn’t archive");
  });
});
