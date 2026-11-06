"""A saved draft, split back into what you typed and the quote it carries."""


def lf(s):
    """Text with LF line ends only."""
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def resume_split(text, rec, exact):
    """Split a saved draft's text/plain into (own, tail, how), or None when that can't be done
    without losing something. rec: {"own_text", "tail_text"} as recorded at MailMind's last
    save of this draft, or None. exact: this is the very copy MailMind saved, so the record is
    the truth even when the server rewrote the bytes. how is "exact" (the record), "tail" (the
    text still ends with the recorded quote, so only that is cut) or "whole" (none of it can be
    told apart, so all of it is yours to edit). own + tail is the drafted text, give or take
    trailing newlines."""
    rec = rec or {}
    if exact and rec.get("own_text") is not None:
        return lf(rec["own_text"]), lf(rec.get("tail_text") or ""), "exact"
    if text is None:
        return None
    t, tail = lf(text), lf(rec.get("tail_text") or "")
    s, b = tail.rstrip("\n"), t.rstrip("\n")
    if s.startswith("\n\n") and b.endswith(s):
        return b[:len(b) - len(s)], tail, "tail"
    return (t[:-1] if t.endswith("\n") else t), "", "whole"   # set_content's own trailing newline
