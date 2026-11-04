"""The Claude Messages API over plain HTTPS. The project is standard-library only, so this is
urllib rather than the SDK: one user turn per call, structured output when a schema is given,
and Anthropic's server-side refusal fallback on the models that take it.

The key comes from mailmind.config.json's "anthropic_api_key", else ANTHROPIC_API_KEY.
ANTHROPIC_BASE_URL points the client somewhere else
(a proxy, or the local stub the tests run). Nothing here retries forever: a failed call raises
RuntimeError and the caller decides.
"""
import os
import json
import time
import random
import urllib.error
import urllib.request

import config
from mailmind.platform.db import txguard

MODEL = "claude-opus-5-5"          # MAILMIND_MODEL overrides it (see summarize.MODEL)
EFFORT = "medium"                  # sent explicitly: models differ in their default
VERSION = "2026-10-21"             # anthropic-version header
MAX_TOKENS = 16000                 # a ceiling, not a target; adaptive thinking counts against it
TIMEOUT = 120                      # seconds per attempt
RETRIES = 2                        # after the first attempt, on the statuses below and network errors
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

# Models that accept `fallbacks: "default"`: a classifier decline is re-run server-side on the
# model Anthropic recommends for that category instead of coming back as a refusal.
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models older than output_config.effort, which reject it (MAILMIND_MODEL may name one).
_NO_EFFORT = ("claude-haiku-4-5", "claude-sonnet-4-5")
# JSON Schema keywords structured output doesn't support. The callers clamp what they read anyway.
_UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                "minLength", "maxLength"}


def _key():
    """(key, where it came from). The config file wins, so a MailMind-only key isn't shadowed
    by one the shell exports for other tools. A config key that can't be resolved is no key,
    not a quiet switch to the environment's."""
    try:
        k = config.anthropic_key()
    except Exception:
        return "", "mailmind.config.json"
    if k is not None:
        return k, "mailmind.config.json"
    return os.environ.get("ANTHROPIC_API_KEY", "").strip(), "ANTHROPIC_API_KEY"


def key():
    return _key()[0]


def key_source():
    return _key()[1]


def api_schema(schema):
    """`schema` in the form structured output accepts: every object closed with
    additionalProperties:false and the unsupported bounds dropped."""
    if isinstance(schema, list):
        return [api_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in _UNSUPPORTED:
            continue
        if k in ("properties", "$defs") and isinstance(v, dict):   # names → schemas, not a schema
            out[k] = {name: api_schema(s) for name, s in v.items()}
        else:
            out[k] = api_schema(v)
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def _retry_after(headers):
    try:
        return min(30.0, max(0.0, float(headers.get("retry-after"))))
    except (TypeError, ValueError):
        return None


def _error_text(e):
    try:
        return json.loads(e.read())["error"]["message"][:200]
    except Exception:
        return e.reason or ""


def _post(body, beta=None):
    url = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    headers = {"content-type": "application/json", "x-api-key": key(), "anthropic-version": VERSION}
    if beta:
        headers["anthropic-beta"] = beta
    data = json.dumps(body).encode()
    attempt = 0
    while True:
        txguard.refuse_network("a Claude API request")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_STATUS or attempt == RETRIES:
                raise RuntimeError(f"Claude API {e.code}: {_error_text(e)}") from None
            wait = _retry_after(e.headers)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == RETRIES:
                raise RuntimeError(f"Claude API unreachable: {getattr(e, 'reason', e)}") from None
            wait = None
        time.sleep(wait if wait is not None else min(8.0, 0.5 * 2 ** attempt) + random.random() / 4)
        attempt += 1


def message(prompt, schema=None, model=None, effort=None):
    """Send `prompt` as one user turn and return the reply text: the JSON document when
    `schema` is given. A refusal returns "" — the call worked, so callers store their fallback
    rather than retry. Raises RuntimeError when the API can't be reached or rejects the call."""
    model = model or MODEL
    body = {"model": model, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}]}
    config = {}
    if schema is not None:
        config["format"] = {"type": "json_schema", "schema": api_schema(schema)}
    if not model.startswith(_NO_EFFORT):
        config["effort"] = effort or EFFORT
    if config:
        body["output_config"] = config
    beta = None
    if model in _FALLBACK_MODELS:
        body["fallbacks"] = "default"
        beta = _FALLBACK_BETA
    resp = _post(body, beta)
    if resp.get("stop_reason") == "refusal":
        return ""
    blocks = resp.get("content") or []
    for i in range(len(blocks) - 1, -1, -1):    # a model that declined mid-reply hands over at a
        if blocks[i].get("type") == "fallback":  # "fallback" block; only what follows is the answer
            blocks = blocks[i + 1:]
            break
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
