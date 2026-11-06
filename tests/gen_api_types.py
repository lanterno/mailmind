"""`just gen-api`: TypeScript types for the API, from what its routes really answer.

The routes have no schema to generate from, so the types come from the answers
the whole test suite saw (`MAILMIND_RECORD_API`, tests/app_client.py). Every answer is fictional, and only shapes are
written, never a value. A key seen in only some of a route's answers is optional. An object whose keys are data rather
than field names (addresses, folders, ids) becomes a Record. It knows only the routes the tests call and only with 2xx
answers, so a field no test ever sees won't be in the types.

    python tests/gen_api_types.py <recorded answers> <out.ts>
"""
import json
import re
import sys

FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MANY_KEYS = 40       # more distinct keys than any real answer has fields means they are data


def empty():
    return {"prims": set(), "items": None, "fields": {}, "objects": 0}


def note(shape, value):
    if isinstance(value, bool):
        shape["prims"].add("boolean")
    elif value is None:
        shape["prims"].add("null")
    elif isinstance(value, (int, float)):
        shape["prims"].add("number")
    elif isinstance(value, str):
        shape["prims"].add("string")
    elif isinstance(value, list):
        if shape["items"] is None:
            shape["items"] = empty()
        for item in value:
            note(shape["items"], item)
    else:
        shape["objects"] += 1
        for key, inner in value.items():
            field = shape["fields"].setdefault(key, {"shape": empty(), "seen": 0})
            field["seen"] += 1
            note(field["shape"], inner)


def merge_into(target, shape):
    target["prims"] |= shape["prims"]
    if shape["items"] is not None:
        target["items"] = target["items"] or empty()
        merge_into(target["items"], shape["items"])
    target["objects"] += shape["objects"]
    for key, field in shape["fields"].items():
        mine = target["fields"].setdefault(key, {"shape": empty(), "seen": 0})
        mine["seen"] += field["seen"]
        merge_into(mine["shape"], field["shape"])


def keys_are_data(fields):
    return len(fields) > MANY_KEYS or any(not FIELD.match(k) for k in fields)


def render(shape, indent=2):
    parts = sorted(shape["prims"] - {"null"})
    if shape["items"] is not None:
        inner = render(shape["items"], indent)
        parts.append(f"({inner})[]" if " | " in inner else f"{inner}[]")
    if shape["fields"] or shape["objects"]:
        if keys_are_data(shape["fields"]):
            every = empty()
            for field in shape["fields"].values():
                merge_into(every, field["shape"])
            parts.append(f"Record<string, {render(every, indent)}>")
        elif not shape["fields"]:
            parts.append("Record<string, never>")
        else:
            pad = " " * indent
            lines = [f"{pad}{k}{'' if f['seen'] == shape['objects'] else '?'}: {render(f['shape'], indent + 2)};"
                     for k, f in sorted(shape["fields"].items())]
            parts.append("{\n" + "\n".join(lines) + "\n" + " " * (indent - 2) + "}")
    if "null" in shape["prims"]:
        parts.append("null")
    return " | ".join(parts) or "unknown"


def template(path):
    """The route a request went to: no query, and each number in the path a {pk}."""
    return re.sub(r"/\d+(?=/|$)", "/{pk}", path.split("?", 1)[0])


def generate(lines):
    routes = {"GET": {}, "POST": {}}
    for line in lines:
        method, path, body = json.loads(line)
        if method in routes and path.startswith("/api/"):
            note(routes[method].setdefault(template(path), empty()), body)
    out = ["// Written by `just gen-api` from what the routes answer in the tests: don't edit. Only shapes, no values.", ""]
    for method, name in (("GET", "GetAnswers"), ("POST", "PostAnswers")):
        out.append(f"export interface {name} {{")
        for path, shape in sorted(routes[method].items()):
            out.append(f"  {json.dumps(path)}: {render(shape, 4)};")
        out += ["}", ""]
    return "\n".join(out)


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        text = generate(f)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(text)
