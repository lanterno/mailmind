"""OpenAI's Chat Completions wire format, which every provider but Claude is asked in (#49): the request body for a Prompt,
and the Reply in a response.

The system prompt goes as a `system` message ahead of the user's. Structured output is `response_format`'s `json_schema`,
strict only where the schema keeps strict mode's rules (every object closed, and every property in it required), since a
strict schema that breaks them is refused. OpenAI takes `max_completion_tokens`, and the others `max_tokens`. No effort and
no server-side fallback go out: neither is theirs.
"""
from typing import Any, cast

from mailmind.assistant.application.ports import Prompt, Reply, Usage
from mailmind.kernel.ai_provider import AiProvider

SCHEMA_NAME = "answer"


def strict(schema: Any) -> bool:
    """Whether `schema` keeps strict mode's rules: every object closed, and every property it names required."""
    if isinstance(schema, list):
        return all(strict(s) for s in cast(list[Any], schema))
    if not isinstance(schema, dict):
        return True
    node = cast(dict[str, Any], schema)
    props = node.get("properties")
    if node.get("type") == "object" or isinstance(props, dict):
        names = set(cast(dict[str, Any], props)) if isinstance(props, dict) else set()
        if node.get("additionalProperties") is not False or set(node.get("required") or []) != names:
            return False
    for key, value in node.items():
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            if not all(strict(s) for s in cast(dict[str, Any], value).values()):
                return False
        elif key in ("items", "anyOf", "oneOf", "allOf", "prefixItems") and not strict(value):
            return False
    return True


def body(prompt: Prompt, provider: AiProvider, with_format: bool = True) -> dict[str, Any]:
    """The body of a POST {base}/chat/completions for `prompt`, with its schema as `response_format` unless `with_format` is
    False (a service that refused it)."""
    messages = [{"role": "system", "content": prompt.system}] if prompt.system else []
    messages.append({"role": "user", "content": prompt.text})
    out: dict[str, Any] = {"model": prompt.model, "messages": messages,
                           "max_completion_tokens" if provider == "openai" else "max_tokens": prompt.max_tokens}
    if prompt.schema is not None and with_format:
        out["response_format"] = {"type": "json_schema", "json_schema": {
            "name": SCHEMA_NAME, "schema": prompt.schema, "strict": strict(prompt.schema)}}
    return out


def _count(usage: Any, name: str) -> int:
    try:
        return max(0, int((usage if isinstance(usage, dict) else {}).get(name) or 0))
    except (TypeError, ValueError):
        return 0


def _text(content: Any) -> str:
    """A message's content: a string, or a list of parts whose text parts are joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in cast(list[Any], content)
                       if isinstance(p, dict) and p.get("type") == "text")
    return ""


def reply(resp: dict[str, Any], asked: str) -> Reply:
    """The Reply in a response to a request for model `asked`. A refusal reads as "", as Claude's does: the call worked, so
    callers store their fallback rather than retry. Cached prompt tokens count as cache reads, and the rest of the prompt as
    input."""
    usage = resp.get("usage") or {}
    prompt_tokens = _count(usage, "prompt_tokens")
    cached = min(_count(usage.get("prompt_tokens_details") if isinstance(usage, dict) else None, "cached_tokens"),
                 prompt_tokens)
    spent = Usage(prompt_tokens - cached, _count(usage, "completion_tokens"), 0, cached)
    served = str(resp.get("model") or asked)
    choices = resp.get("choices")
    first: dict[str, Any] = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    said = first.get("message")
    message: dict[str, Any] = said if isinstance(said, dict) else {}
    if message.get("refusal"):
        return Reply("", served, spent, "refusal")
    return Reply(_text(message.get("content")).strip(), served, spent, str(first.get("finish_reason") or ""))


def model_ids(resp: Any) -> list[str]:
    """The ids a GET {base}/models answered with."""
    data = resp.get("data") if isinstance(resp, dict) else None
    return [str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")] if isinstance(data, list) else []
