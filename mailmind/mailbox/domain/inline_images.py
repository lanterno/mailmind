"""What of a message's inline images its frame may hold: `src="cid:..."` made into a `data:` URI, which the frame's policy
allows, so a logo shows with nothing fetched. Only a raster image is ever embedded: an SVG or HTML part is active content. A
set over the budget stops there, and the rest stay as cid references, which show as broken images and cost nothing."""
import base64
import re
from collections.abc import Mapping

_CID_IMG = re.compile(r"""src\s*=\s*(['"])cid:([^'"]+)\1""", re.I)
RASTER = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
BUDGET = 3 << 20                # the bytes one message may embed


def embed(safe_html: str, parts: Mapping[str, tuple[str, bytes]]) -> str:
    """`safe_html` with each reference to one of `parts` ({content id: (content type, bytes)}) that may be embedded, embedded."""
    used = 0

    def sub(m: re.Match[str]) -> str:
        nonlocal used
        cid = m.group(2).strip().strip("<>")
        ent = parts.get(cid)
        if not ent:
            return m.group(0)
        ctype, data = ent
        if ctype not in RASTER or used + len(data) > BUDGET:
            return m.group(0)
        used += len(data)
        b64 = base64.b64encode(data).decode("ascii")
        return f'src={m.group(1)}data:{ctype};base64,{b64}{m.group(1)}'
    return _CID_IMG.sub(sub, safe_html)
