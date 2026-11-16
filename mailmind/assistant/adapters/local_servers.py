"""The model servers on this Mac (#49): the ones a person may run, and which answer with a list of models.

Each is asked `GET {url}/models` with no key, all at once and each for a second at most, so a port nothing listens on
costs nothing and one that hangs no more than that. One that answers anything but a 2xx JSON object whose `data` is a list
isn't a model server, or isn't one yet, and is left out. The suite replaces `known`, so no test asks a server running on
the machine.
"""
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from mailmind.assistant.adapters import chat_wire
from mailmind.assistant.adapters.openai_chat import Http
from mailmind.assistant.application.ports import ModelError
from mailmind.assistant.domain.providers import LOCAL_SERVERS
from mailmind.platform.db import txguard

WAIT = 1                           # second, at most, that a server is waited for


def known() -> Sequence[tuple[str, str]]:
    """The servers there may be on this Mac, as (name, base URL)."""
    return LOCAL_SERVERS


def listing(urls: Sequence[str]) -> dict[str, list[str]]:
    """The ids each of `urls` that answered with a list of models names. One that didn't is left out."""
    txguard.refuse_network("the model servers on this Mac")
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=len(urls), thread_name_prefix="local-ai") as pool:
        found = dict(zip(urls, pool.map(_listed, urls), strict=True))
    return {url: ids for url, ids in found.items() if ids is not None}


def _listed(url: str) -> list[str] | None:
    try:
        answer = Http("local", url, "", WAIT).send("GET", "/models")
    except ModelError:
        return None
    body = answer.json()
    if not 200 <= answer.status < 300 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return None
    return chat_wire.model_ids(body)
