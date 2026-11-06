"""Building an outgoing message, with no I/O. Every header is set through EmailMessage, which refuses
an embedded CR or LF, and every address is validated and IDNA-normalized before that, so neither
header nor SMTP injection has a way in. Bcc is never written into the bytes: it rides only in the
envelope list returned."""
import email.policy
import email.utils
from datetime import datetime, timezone
from email.headerregistry import Address
from email.message import EmailMessage
import re

from mailmind.kernel.addresses import MAX_RECIPIENTS, clean, parse_addr_list, validate_pair

_MSGID = re.compile(r"^<[^<>\s]+@[^<>\s]+>$")


def _addr(name, spec):
    local, _, domain = spec.rpartition("@")
    return Address(display_name=name or "", username=local, domain=domain)


def reference_chain(orig_message_id, orig_refs, orig_in_reply_to):
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


def thread_ids(in_reply_to, references):
    """(in_reply_to|None, [references]) taken from a saved draft's own headers: well-formed
    Message-IDs only, references de-duplicated in order and capped at 50 (the first, which
    names the thread's root, and the last 49)."""
    irt = next((t for t in re.findall(r"<[^<>\s]+>", str(in_reply_to or "")) if _MSGID.match(t)), None)
    refs, seen = [], set()
    for t in re.findall(r"<[^<>\s]+>", str(references or "")):
        if _MSGID.match(t) and t not in seen:
            seen.add(t)
            refs.append(t)
    if len(refs) > 50:
        refs = refs[:1] + refs[-49:]
    return irt, refs


_BCC_HEADER = re.compile(rb"(?im)^bcc[ \t]*:")


def build_message(spec, draft=False):
    """spec: {account, account_name, to, cc, bcc (lists of raw tokens or strings),
    subject, text, html|None, attachments [{filename,content_type,data:bytes}],
    in_reply_to|None, references|list|None}.
    Returns {raw: bytes (Bcc-less, CRLF), message_id, envelope: [addr-spec,...],
    to_disp/cc_disp/bcc_disp: [addr-spec,...] for the outbox UI}.
    draft=True relaxes the require-a-recipient rule (a draft may be half-written) but
    still validates every typed address.
    """
    from_name, from_spec = validate_pair(spec.get("account_name") or "", spec["account"])
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
    m["Subject"] = clean(spec.get("subject") or "")
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
        filename = clean(att.get("filename") or "attachment")
        if (maintype, subtype) == ("message", "rfc822"):     # an attached email may not be base64 (RFC 2046)
            m.add_attachment(email.message_from_bytes(att["data"], policy=email.policy.default), filename=filename)
            continue
        m.add_attachment(att["data"], maintype=maintype, subtype=subtype, filename=filename, **extra)

    raw = m.as_bytes(policy=email.policy.SMTP)
    # A raise, not an assert: python -O strips asserts. Header names are case-insensitive.
    if _BCC_HEADER.search(raw.split(b"\r\n\r\n", 1)[0]):
        raise ValueError("Bcc must never appear in the transmitted/stored bytes")
    return {
        "raw": raw, "message_id": mid,
        "envelope": [a for _, a in to] + [a for _, a in cc] + [a for _, a in bcc],
        "to_disp": [a for _, a in to], "cc_disp": [a for _, a in cc],
        "bcc_disp": [a for _, a in bcc],
    }
