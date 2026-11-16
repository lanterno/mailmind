// An Other IMAP account's outgoing (SMTP) server, as its form holds it and as a save sends it. What the file doesn't hold, the
// server works out as it reads the account: no host is the incoming host, and the port and security are its type's. An edit
// keeps it so: it shows no host where none is saved, and sends what the file doesn't hold only once a person changes it, so
// a later change of the incoming host still moves the outgoing server with it.
import type { GetAnswers } from "../../lib/api/types";

type Connection = NonNullable<GetAnswers["/api/accounts"]["accounts"]>[number];
export type Security = "tls" | "starttls";
export interface Outgoing { host: string; port: string; security: Security }
export interface OutgoingBits { smtp_host?: string; smtp_port?: number; smtp_starttls?: boolean }

/** The fields as a form starts them: a new account's blank, an edited one's as saved, with no host where none is. */
export function outgoingOf(row: Connection | null): Outgoing {
  if (!row) return { host: "", port: "587", security: "starttls" };
  return {
    host: row.defaults_applied.smtp_host ? "" : row.smtp_host ?? "",
    port: String(row.smtp_port),
    security: row.smtp_starttls ? "starttls" : "tls",
  };
}

/** What a save or a test sends of the outgoing server: a host only when one is typed; for an edit, a port or security the
 * file doesn't hold only once it was changed. */
export function outgoingBits(now: Outgoing, row: Connection | null): OutgoingBits {
  // One saved as another type becomes Other IMAP with what the form shows, as a new one does.
  const edited = row?.type === "imap" ? row : null;
  const was = outgoingOf(edited);
  const sends = (key: "smtp_port" | "smtp_starttls", changed: boolean) => !edited || !edited.defaults_applied[key] || changed;
  return {
    ...(now.host.trim() ? { smtp_host: now.host.trim() } : {}),
    ...(sends("smtp_port", now.port !== was.port) ? { smtp_port: Number(now.port) || 587 } : {}),
    ...(sends("smtp_starttls", now.security !== was.security) ? { smtp_starttls: now.security === "starttls" } : {}),
  };
}
