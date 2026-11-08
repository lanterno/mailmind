// Written by `just gen-api` from mailmind/http/page_limits.py: the limits the page holds a person to, each the server's
// own. Never edit it by hand: `just check-web` fails when it no longer matches.
export const LIMITS = {
  attach_bytes: 26214400,
  bulk_rows: 500,
  instruction_chars: 400,
  snooze_copies: 500,
  undo_send_seconds: 10,
} as const;
