// Read only (MAILMIND_PROFILE, or the Mac app's Settings › General) changes nothing on a mail server. The server refuses each
// change that would reach one up front, with a 409 that says `read_only`, so nothing waits for a later run; what changes only
// this Mac (reading, a rule, AI, a draft kept here) still works. The page and the Mac app say it in the same words.
import type { ApiError } from "./api/client";

// The status menu's note while read only is on.
export const READ_ONLY_NOTE = "Read only is on: MailMind changes nothing on your mail servers, so sending, filing and starring are "
  + "turned off. Turn it off in Settings › General.";

// What a change refused read-only says, wherever it was asked for.
export const READ_ONLY_REFUSED = "Read only is on, so nothing was changed. Turn it off in Settings › General.";

// Whether `e` is the server's refusal of a change because it runs read-only: an ApiError whose problem said `read_only`.
export const refusedReadOnly = (e: unknown) => (e as Partial<Pick<ApiError, "readOnly">> | null)?.readOnly === true;

// What a failure says: the read-only words for a read-only refusal, ahead of any other reading of a 409, else `otherwise`.
export const readOnlyOr = (e: unknown, otherwise: string) => (refusedReadOnly(e) ? READ_ONLY_REFUSED : otherwise);
