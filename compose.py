"""Pure outgoing-message construction for MailMind — stdlib email only, NO I/O.

build_message(spec) -> dict with the wire-ready RFC822 bytes, the minted Message-ID,
and the SMTP envelope recipient list. Every header is set through EmailMessage (which
refuses embedded CR/LF) and every address is validated + IDNA-normalized BEFORE
construction, so header/SMTP injection is structurally impossible. Bcc is NEVER written
into the bytes (privacy): it rides only in the returned envelope list.

The reply_bodies/forward_bodies helpers assemble the quoted-original body, running any
HTML original through the verified sanitize_html allowlist so a hostile message can't
inject active content into what we send.
"""
import re
import email.policy
import email.utils
from email.message import EmailMessage
from email.headerregistry import Address
from datetime import datetime, timezone

import sanitize_html

MAX_RECIPIENTS = 100
# Codepoints that must never survive into a header value: C0/C1 controls, bidi
# overrides/isolates, zero-width, and line/paragraph separators (homograph + smuggling).
_DANGER = re.compile(
    "[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠⁦-⁩  ﻿]")
# An addr-spec may not contain whitespace, separators, brackets, or control chars.
_ADDR_BAD = re.compile(r'[\s,;:<>()\[\]\\"]|[\x00-\x1f\x7f]')
_MSGID = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")


def _clean(s):
    """Strip dangerous codepoints from a free-text header value (subject/display name)."""
    return _DANGER.sub("", s or "").strip()


def _idna_domain(domain):
    """ASCII (IDNA) form of a domain, or raise ValueError. ASCII domains pass through."""
    domain = (domain or "").strip().rstrip(".")
    if not domain or "." not in domain and domain.lower() not in ("localhost",):
        raise ValueError("invalid domain")
    try:
        return domain.encode("idna").decode("ascii")
    except (UnicodeError, Exception):
        raise ValueError(f"invalid domain: {domain!r}")


def _validate_pair(name, addr):
    """(clean_display_name, addr_spec_ascii) from an already-split (name, addr), or
    raise ValueError. addr_spec is normalized to local@idna-domain."""
    addr = (addr or "").strip()
    if not addr or addr.count("@") != 1:
        raise ValueError(f"not a valid address: {addr!r}")
    # reject bidi/zero-width/control codepoints anywhere in the addr-spec (homograph +
    # non-ASCII envelope that would crash smtplib's ascii encoder mid-transaction)
    if _DANGER.search(addr):
        raise ValueError(f"unsafe characters in address: {addr!r}")
    local, _, domain = addr.rpartition("@")
    if not local or _ADDR_BAD.search(local) or _ADDR_BAD.search(domain):
        raise ValueError(f"not a valid address: {addr!r}")
    return _clean(name), f"{local}@{_idna_domain(domain)}"


def parse_addr_list(raw):
    """Validate a raw recipient string OR a list of tokens -> [(name, addr_spec)].
    Uses getaddresses so a comma inside a quoted display name is not mis-split."""
    tokens = raw if isinstance(raw, list) else [raw or ""]
    out, seen = [], set()
    for name, addr in email.utils.getaddresses([t for t in tokens if t]):
        dn, spec = _validate_pair(name, addr)   # validate the split pair directly
        key = spec.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((dn, spec))
    return out


def _addr(name, spec):
    local, _, domain = spec.rpartition("@")
    return Address(display_name=name or "", username=local, domain=domain)


def _references(orig_message_id, orig_refs, orig_in_reply_to):
    """RFC 5322 §3.6.4 References chain for a reply, de-duped, parent appended last."""
    ids = []
    for blob in (orig_refs or "", orig_in_reply_to or ""):
        ids += re.findall(r"<[^<>\s]+>", blob)
    if orig_message_id:
        ids.append(orig_message_id.strip())
    out = []
    for i in ids:
        if _MSGID.match(i) and i not in out:
            out.append(i)
    return out


def build_message(spec, draft=False):
    """spec: {account, account_name, to, cc, bcc (lists of raw tokens or strings),
    subject, text, html|None, attachments [{filename,content_type,data:bytes}],
    in_reply_to|None, references|list|None}.
    Returns {raw: bytes (Bcc-less, CRLF), message_id, envelope: [addr-spec,...],
    to_disp/cc_disp/bcc_disp: [addr-spec,...] for the outbox UI}.
    draft=True relaxes the require-a-recipient rule (a draft may be half-written) but
    still validates every typed address.
    """
    from_name, from_spec = _validate_pair(spec.get("account_name") or "", spec["account"])
    from_domain = from_spec.rpartition("@")[2]

    to = parse_addr_list(spec.get("to") or [])
    cc = parse_addr_list(spec.get("cc") or [])
    bcc = parse_addr_list(spec.get("bcc") or [])
    if not (to or cc or bcc) and not draft:
        raise ValueError("at least one recipient is required")
    if len(to) + len(cc) + len(bcc) > MAX_RECIPIENTS:
        raise ValueError(f"too many recipients (max {MAX_RECIPIENTS})")

    m = EmailMessage()
    m["From"] = _addr(from_name, from_spec)           # forced to the authenticated account
    if to:
        m["To"] = [_addr(n, a) for n, a in to]
    if cc:
        m["Cc"] = [_addr(n, a) for n, a in cc]
    # NOTE: deliberately NO Bcc header — blind recipients live only in the envelope.
    m["Subject"] = _clean(spec.get("subject") or "")
    m["Date"] = email.utils.format_datetime(datetime.now(timezone.utc))
    mid = email.utils.make_msgid(domain=from_domain)
    m["Message-ID"] = mid
    irt = (spec.get("in_reply_to") or "").strip()
    if _MSGID.match(irt):
        m["In-Reply-To"] = irt
    refs = spec.get("references")
    if refs:
        toks = refs if isinstance(refs, list) else re.findall(r"<[^<>\s]+>", refs)
        toks = [t for t in toks if _MSGID.match(t)]
        if toks:
            m["References"] = " ".join(toks)
    m["User-Agent"] = "MailMind"

    m.set_content(spec.get("text") or "", subtype="plain", charset="utf-8",
                  cte="quoted-printable")
    if spec.get("html"):
        m.add_alternative(spec["html"], subtype="html", charset="utf-8",
                          cte="quoted-printable")
    for att in spec.get("attachments") or []:
        ct = (att.get("content_type") or "application/octet-stream").split(";")[0].strip()
        maintype, _, subtype = ct.partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        extra = {}
        if att.get("params"):                # e.g. iMIP text/calendar; method=REPLY
            extra["params"] = {str(k): str(v) for k, v in att["params"].items()}
        m.add_attachment(att["data"], maintype=maintype, subtype=subtype,
                         filename=_clean(att.get("filename") or "attachment"), **extra)

    raw = m.as_bytes(policy=email.policy.SMTP)
    assert b"\nBcc:" not in b"\n" + raw.split(b"\r\n\r\n", 1)[0].replace(b"\r\n", b"\n"), \
        "Bcc must never appear in the transmitted/stored bytes"
    return {
        "raw": raw, "message_id": mid,
        "envelope": [a for _, a in to] + [a for _, a in cc] + [a for _, a in bcc],
        "to_disp": [a for _, a in to], "cc_disp": [a for _, a in cc],
        "bcc_disp": [a for _, a in bcc],
    }


# --------------------------------------------------------- quoted-body assembly
def _attribution(orig):
    who = orig.get("from_name") or orig.get("from_addr") or "someone"
    addr = orig.get("from_addr") or ""
    date = orig.get("date") or ""
    tail = f" <{addr}>" if addr and addr != who else ""
    return f"On {date}, {who}{tail} wrote:".strip()


def _quote_plain(text):
    return "\n".join("> " + ln for ln in (text or "").splitlines()) or ">"


def _html_escape(s):
    import html
    return html.escape(s or "")


def _plain_from_html(html_str):
    """Crude HTML->text fallback when an original has no text/plain part."""
    safe, _ = sanitize_html.sanitize(html_str or "", load_images=False)
    txt = re.sub(r"(?is)<br\s*/?>", "\n", safe)
    txt = re.sub(r"(?is)</p\s*>", "\n\n", txt)
    txt = re.sub(r"(?s)<[^>]+>", "", txt)
    import html as _h
    return _h.unescape(txt).strip()


def reply_bodies(new_text, orig):
    """(text, html|None) for a reply. orig: {from_name,from_addr,date,text,html}."""
    attr = _attribution(orig)
    o_text = orig.get("text") or (_plain_from_html(orig.get("html")) if orig.get("html") else "")
    text = f"{new_text or ''}\n\n{attr}\n{_quote_plain(o_text)}\n"
    if orig.get("html"):
        safe, _ = sanitize_html.sanitize(orig["html"], load_images=False)
        html_body = (
            f"<div>{_html_escape(new_text or '').replace(chr(10), '<br>')}</div>"
            f"<br><div>{_html_escape(attr)}</div>"
            f'<blockquote type="cite" style="margin:0 0 0 .8ex;'
            f'border-left:2px solid #ccc;padding-left:1ex">{safe}</blockquote>')
        return text, html_body
    return text, None


def forward_bodies(new_text, orig):
    """(text, html|None) for a forward — banner + original (un-quoted)."""
    rows = [("From", f"{orig.get('from_name') or ''} <{orig.get('from_addr') or ''}>".strip()),
            ("Date", orig.get("date") or ""), ("Subject", orig.get("subject") or ""),
            ("To", orig.get("to") or "")]
    if orig.get("cc"):
        rows.append(("Cc", orig["cc"]))
    banner = "---------- Forwarded message ----------\n" + \
        "\n".join(f"{k}: {v}" for k, v in rows if v)
    o_text = orig.get("text") or (_plain_from_html(orig.get("html")) if orig.get("html") else "")
    text = f"{new_text or ''}\n\n{banner}\n\n{o_text}"
    if orig.get("html"):
        safe, _ = sanitize_html.sanitize(orig["html"], load_images=False)
        banner_html = "<br>".join(_html_escape(f"{k}: {v}") for k, v in rows if v)
        html_body = (
            f"<div>{_html_escape(new_text or '').replace(chr(10), '<br>')}</div><br>"
            f"<div>---------- Forwarded message ----------<br>{banner_html}</div><hr>{safe}")
        return text, html_body
    return text, None
