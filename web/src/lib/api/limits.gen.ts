// Written by `just gen-api` from mailmind/http/page_limits.py: the limits the page holds a person to and what each
// preference is with nothing said, each the server's own. Never edit it by hand: `just check-web` fails when it no longer matches.
export const LIMITS = {
  ai_daily_limit_usd: 1000,
  attach_bytes: 26214400,
  bulk_rows: 500,
  instruction_chars: 400,
  snooze_copies: 500,
  undo_send_longest: 60,
  undo_send_shortest: 5,
} as const;

export const PREFERENCE_DEFAULTS = {
  undo_send_seconds: 10,
  density: "comfortable",
  high_contrast: false,
  theme: "day",
  read_width: "medium",
  read_size: "m",
  read_font: "sans",
  mail_colors: "auto",
  single_keys: true,
  conversations: true,
  search_scope: "all",
  draft_tone: "balanced",
  notify_new_mail: true,
  notify_meetings: true,
  quiet_hours: false,
  quiet_from: "22:00",
  quiet_until: "07:00",
  guide_offered: false,
  ai_daily_limit_usd: 15.0,
} as const;
