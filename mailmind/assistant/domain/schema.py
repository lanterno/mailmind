"""A JSON Schema in the form structured output accepts."""
from typing import Any, cast

# JSON Schema keywords structured output doesn't support. Callers clamp what they read anyway.
_UNSUPPORTED = frozenset({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                          "minLength", "maxLength"})


def structured(schema: Any) -> Any:
    """`schema` with every object closed (additionalProperties: false) and the unsupported bounds
    dropped. The caller's schema is left as it was."""
    if isinstance(schema, list):
        return [structured(s) for s in cast(list[Any], schema)]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for k, v in cast(dict[str, Any], schema).items():
        if k in _UNSUPPORTED:
            continue
        if k in ("properties", "$defs") and isinstance(v, dict):   # names → schemas, not a schema
            out[k] = {name: structured(s) for name, s in cast(dict[str, Any], v).items()}
        else:
            out[k] = structured(v)
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out
