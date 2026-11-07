"""The Messages API's wire format, which both HTTP adapters speak: the request body for a Prompt,
and the Reply in a response."""
from mailmind.assistant.application.ports import BatchResult, Prompt, Reply, Usage


def body(prompt: Prompt) -> dict:
    """The JSON body of a POST /v1/messages for `prompt`, `fallbacks` included when it asks."""
    out = {"model": prompt.model, "max_tokens": prompt.max_tokens,
           "messages": [{"role": "user", "content": prompt.text}]}
    if prompt.system:
        out["system"] = prompt.system
    config = {}
    if prompt.schema is not None:
        config["format"] = {"type": "json_schema", "schema": prompt.schema}
    if prompt.effort:
        config["effort"] = prompt.effort
    if config:
        out["output_config"] = config
    if prompt.fallback:
        out["fallbacks"] = "default"
    return out


def _count(usage: dict, name: str) -> int:
    try:
        return max(0, int(usage.get(name) or 0))
    except (TypeError, ValueError):
        return 0


def _served(resp: dict, blocks: list, asked: str) -> str:
    """The model that served the reply. After a server-side fallback that is the fallback's,
    named by its fallback_message entry in usage.iterations, or by the last fallback block."""
    for it in reversed((resp.get("usage") or {}).get("iterations") or []):
        if isinstance(it, dict) and it.get("type") == "fallback_message" and it.get("model"):
            return str(it["model"])
    for b in reversed(blocks):
        if isinstance(b, dict) and b.get("type") == "fallback":
            to = (b.get("to") or {}).get("model")
            if to:
                return str(to)
    return str(resp.get("model") or asked)


def reply(resp: dict, asked: str) -> Reply:
    """The Reply in a response to a request for model `asked`. A refusal reads as "": the call
    worked, so callers store their fallback rather than retry. A model that declined mid-reply hands
    over at a "fallback" block, and only the text after the last one is the answer."""
    usage = resp.get("usage") or {}
    blocks = [b for b in (resp.get("content") or []) if isinstance(b, dict)]
    spent = Usage(_count(usage, "input_tokens"), _count(usage, "output_tokens"),
                  _count(usage, "cache_creation_input_tokens"), _count(usage, "cache_read_input_tokens"))
    stop = str(resp.get("stop_reason") or "")
    served = _served(resp, blocks, asked)
    if stop == "refusal":
        return Reply("", served, spent, stop)
    for i in range(len(blocks) - 1, -1, -1):
        if blocks[i].get("type") == "fallback":
            blocks = blocks[i + 1:]
            break
    text = "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text").strip()
    return Reply(text, served, spent, stop)


def batch_request(custom_id: str, prompt: Prompt) -> dict:
    """One request of a batch. Batches reject `fallbacks`, so the gateway doesn't ask for it in one."""
    return {"custom_id": custom_id, "params": body(prompt)}


def batch_result(line: dict, asked: str) -> BatchResult:
    """One line of a batch's results: a message that succeeded, or one errored, canceled or expired."""
    result = line.get("result") or {}
    custom_id = str(line.get("custom_id") or "")
    if result.get("type") == "succeeded":
        return BatchResult(custom_id, reply(result.get("message") or {}, asked))
    error = result.get("error") or {}
    nested = error.get("error")
    inner = nested if isinstance(nested, dict) else error
    return BatchResult(custom_id, None, "invalid_request" in str(inner.get("type") or ""))
