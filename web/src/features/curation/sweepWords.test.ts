import { describe, expect, it } from "vitest";
import { sweepFailed, sweepStopped, undoBrings } from "./sweepWords";

describe("what a sweep says", () => {
  it("says Undo puts it all back, or the rest when some of it can't come back", () => {
    expect(undoBrings(0)).toBe("puts it all back");
    expect(undoBrings(undefined)).toBe("puts it all back");
    expect(undoBrings(null)).toBe("puts it all back");
    expect(undoBrings(2)).toBe("puts the rest back");
  });
  it("says why one stopped in plain words with what to do next, its class only after them", () => {
    expect(sweepStopped("ConnectionLost")).toBe("Couldn’t reach the mail server, so nothing was swept. Try again in a moment. (ConnectionLost)");
    expect(sweepStopped("abort", 0)).toMatch(/^Couldn’t reach the mail server, so nothing was swept\./);
    expect(sweepStopped("ConnectionLost", 1)).toBe("Couldn’t reach the mail server, so the sweep stopped after 1. Undo brings it back; sweep again for the rest. (ConnectionLost)");
    expect(sweepStopped("ConnectionLost", 40)).toContain("stopped after 40. Undo brings them back; sweep again for the rest.");
    expect(sweepStopped("ConnectionLost", 40, true)).toContain("stopped after 40. Undo brings them back;");
  });
  it("says it couldn't check how much went when the server couldn't be asked again, and that Undo puts back whatever did", () => {
    expect(sweepStopped("ConnectionLost", 0, true)).toBe("Couldn’t reach the mail server, so the sweep stopped part way and MailMind couldn’t check how much went. Undo puts back whatever did; sweep again for the rest. (ConnectionLost)");
    expect(sweepStopped("ConnectionLost", 0, true)).not.toContain("nothing was swept");
  });
  it("says the server's words when it refused, and what to do when no answer came", () => {
    expect(sweepFailed("MailMind is read-only.", false)).toBe("MailMind is read-only.");
    expect(sweepFailed(undefined, true)).toBe("Couldn’t look at this Inbox. Try again in a moment.");
    expect(sweepFailed("", false)).toBe("The sweep didn’t finish. Try again in a moment.");
  });
});
