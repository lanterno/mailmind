import { describe, expect, it } from "vitest";
import { outgoingBits, outgoingOf } from "./outgoing";

// An Other IMAP account as /api/accounts lists it: its outgoing server resolved, and which of it the file holds.
const row = (saved: { smtp_host?: string; smtp_port?: number; smtp_starttls?: boolean } = {}, type = "imap") => ({
  email: "dee@example.test", type, alias: null, host: "imap.example.test", port: 993, starttls: false, trash: "Trash",
  smtp_host: saved.smtp_host ?? "imap.example.test", smtp_port: saved.smtp_port ?? 587, smtp_starttls: saved.smtp_starttls ?? true,
  disabled: false, signature: "", credential: { set: true, style: "keychain" },
  defaults_applied: { host: false, port: false, starttls: false, trash: true, smtp_host: saved.smtp_host === undefined, smtp_port: saved.smtp_port === undefined, smtp_starttls: saved.smtp_starttls === undefined },
}) as never;

describe("an Other IMAP account's outgoing server", () => {
  it("starts blank for a new account, and sends what is typed", () => {
    expect(outgoingOf(null)).toEqual({ host: "", port: "587", security: "starttls" });
    expect(outgoingBits({ host: "", port: "587", security: "starttls" }, null)).toEqual({ smtp_port: 587, smtp_starttls: true });
    expect(outgoingBits({ host: " smtp.example.test ", port: "465", security: "tls" }, null))
      .toEqual({ smtp_host: "smtp.example.test", smtp_port: 465, smtp_starttls: false });
  });
  it("shows no host when an edited account saved none, rather than the incoming host it falls back to", () => {
    expect(outgoingOf(row())).toEqual({ host: "", port: "587", security: "starttls" });
    expect(outgoingOf(row({ smtp_host: "smtp.example.test", smtp_port: 465, smtp_starttls: false })))
      .toEqual({ host: "smtp.example.test", port: "465", security: "tls" });
  });
  it("sends nothing of it on an edit that left it as it was, so it goes on following the incoming host", () => {
    const edited = row();
    expect(outgoingBits(outgoingOf(edited), edited)).toEqual({});
  });
  it("sends what a person changed, and what the file holds", () => {
    const edited = row();
    expect(outgoingBits({ ...outgoingOf(edited), host: "smtp.example.test" }, edited)).toEqual({ smtp_host: "smtp.example.test" });
    expect(outgoingBits({ ...outgoingOf(edited), port: "465" }, edited)).toEqual({ smtp_port: 465 });
    expect(outgoingBits({ ...outgoingOf(edited), security: "tls" }, edited)).toEqual({ smtp_starttls: false });
    const saved = row({ smtp_host: "smtp.example.test", smtp_port: 465, smtp_starttls: false });
    expect(outgoingBits(outgoingOf(saved), saved)).toEqual({ smtp_host: "smtp.example.test", smtp_port: 465, smtp_starttls: false });
  });
  it("sends what the form shows for an account saved as another type that becomes Other IMAP", () => {
    const gmail = row({}, "gmail");
    expect(outgoingBits(outgoingOf(gmail), gmail)).toEqual({ smtp_port: 587, smtp_starttls: true });
  });
});
