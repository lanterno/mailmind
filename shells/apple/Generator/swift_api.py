"""`just apple gen-api`: the Swift types of the routes' answers and bodies, from the OpenAPI document their schemas make.

tests/support/openapi_doc.py writes the document, as it does for the page's TypeScript (`just gen-api`); this writes it as
Swift: a Codable struct for each object schema, an enum for each closed set, an enum with a case for each member of a union,
and under `API.Get` and `API.Post` each route under /api/ with what it answers and, for a POST, what it takes. The support
these lean on (`JSONValue`, `Route`, `Union`) is hand-written beside them, in APISupport.swift.

A union is decoded as the page reads one: the first member whose keys the answer's keys fit (every key it requires there, and
no key it doesn't name), else the first that decodes. The schemas name every field an answer has (the schema contract holds
them to it), so the keys alone tell the members apart.

    python shells/apple/Generator/swift_api.py <openapi.json> <API.gen.swift>
"""
import json
import keyword
import re
import sys

SWIFT_RESERVED = {
    "associatedtype", "class", "deinit", "enum", "extension", "fileprivate", "func", "import", "init", "inout", "internal",
    "let", "open", "operator", "private", "precedencegroup", "protocol", "public", "rethrows", "static", "struct", "subscript",
    "typealias", "var", "break", "case", "catch", "continue", "default", "defer", "do", "else", "fallthrough", "for", "guard",
    "if", "in", "repeat", "return", "throw", "switch", "where", "while", "as", "false", "is", "nil", "self", "Self", "super",
    "throws", "true", "try", "Type", "Protocol", "any", "some",
}
# What APISupport.swift declares by hand and the Swift names the types use, which no schema may be named.
SUPPORT = {"JSONValue", "Route", "GetRoute", "PostRoute", "NotJSON", "Union", "API", "Get", "Post",
           "String", "Int", "Double", "Bool", "Set", "Codable", "Hashable", "Sendable", "CaseIterable"}
PRIMITIVES = {"string": "String", "integer": "Int", "number": "Double", "boolean": "Bool"}
HEADER = """\
// Written by `just apple gen-api` from the routes' schemas: the OpenAPI document tests/support/openapi_doc.py makes, as the
// page's web/src/lib/api/schema.gen.ts is. Never edit it by hand: `just apple check-api` fails when it no longer matches.
// swiftlint:disable all
import Foundation
"""


def words(text):
    return [w for w in re.split(r"[^0-9A-Za-z]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)) if w]


def type_name(text):
    name = "".join(w[:1].upper() + w[1:] for w in words(text)) or "Unnamed"
    return f"_{name}" if name[0].isdigit() else name


def member_name(text):
    parts = words(text)
    if not parts:
        return "_unnamed"
    name = parts[0].lower() + "".join(w[:1].upper() + w[1:].lower() if w.isupper() else w[:1].upper() + w[1:] for w in parts[1:])
    if name[0].isdigit():
        name = f"_{name}"
    if name in ("self", "Self"):
        return f"{name}_"         # a parameter named self would hide the instance in an init's body
    return f"`{name}`" if name in SWIFT_RESERVED or keyword.iskeyword(name) else name


def bare(name):
    return name.strip("`")


def comment(text, indent):
    if not text:
        return []
    return [f"{indent}/// {line}".rstrip() for line in text.strip().splitlines()]


def literal(value):
    return json.dumps(value, ensure_ascii=False)


class Writer:
    def __init__(self, doc):
        self.doc = doc
        self.schemas = doc["components"]["schemas"]
        self.lines = []
        self.unions = set()            # the structs some union decodes, which say which keys they name
        self.inline = []               # unions declared inside a struct: (owner, name, members)

    # ── types ────────────────────────────────────────────────────────────────

    def ref(self, schema):
        return schema["$ref"].rsplit("/", 1)[-1]

    def is_null(self, schema):
        return schema.get("type") == "null"

    def string_set(self, schema):
        """The strings a schema allows when it is a closed set of them (an enum, a const, or a union of those), else None."""
        if "$ref" in schema:
            return self.string_set(self.schemas[self.ref(schema)])
        if "enum" in schema and all(isinstance(v, str) for v in schema["enum"]):
            return list(schema["enum"])
        if isinstance(schema.get("const"), str):
            return [schema["const"]]
        if "anyOf" in schema:
            found = [self.string_set(m) for m in schema["anyOf"]]
            if found and all(f is not None for f in found):
                return [v for f in found for v in f]
        return None

    def swift_type(self, schema, owner, field):
        """The Swift type of `schema`, and whether null is one of its values."""
        if "$ref" in schema:
            return type_name(self.ref(schema)), False
        if "anyOf" in schema:
            members = [m for m in schema["anyOf"] if not self.is_null(m)]
            nullable = len(members) < len(schema["anyOf"])
            if len(members) == 1:
                inner, also_null = self.swift_type(members[0], owner, field)
                return inner, nullable or also_null
            name = owner + type_name(field)
            self.inline.append((owner, name, members))
            return name, nullable
        kind = schema.get("type")
        if isinstance(kind, list):
            members = [{"type": k} for k in kind if k != "null"]
            inner, _ = self.swift_type(members[0] if len(members) == 1 else {"anyOf": members}, owner, field)
            return inner, "null" in kind
        if kind in PRIMITIVES:
            return PRIMITIVES[kind], False
        if kind == "array":
            inner, nullable = self.swift_type(schema.get("items", {}), owner, field + " item")
            return f"[{inner}{'?' if nullable else ''}]", False
        if kind == "object":
            extra = schema.get("additionalProperties")
            if isinstance(extra, dict) and extra:
                inner, nullable = self.swift_type(extra, owner, field + " value")
                return f"[String: {inner}{'?' if nullable else ''}]", False
            return "[String: JSONValue]", False
        if kind == "null":
            return "JSONValue", True
        return "JSONValue", False

    # ── declarations ─────────────────────────────────────────────────────────

    def struct(self, name, schema, indent=""):
        swift = type_name(name)
        required = set(schema.get("required", []))
        props = schema.get("properties", {})
        out = comment(schema.get("description"), indent)
        out.append(f"{indent}public struct {swift}: Codable, Hashable, Sendable {{")
        fields = []
        for key in sorted(props):
            prop = props[key]
            kind, nullable = self.swift_type(prop, swift, key)
            optional = nullable or key not in required
            member = member_name(key)
            fields.append((key, member, kind + ("?" if optional else ""), optional))
            out += comment(prop.get("description"), indent + "    ")
            out.append(f"{indent}    public var {member}: {kind}{'?' if optional else ''}")
        if fields:
            params = ", ".join(f"{m}: {k}{' = nil' if opt else ''}" for _, m, k, opt in fields)
            out.append("")
            out.append(f"{indent}    public init({params}) {{")
            out += [f"{indent}        self.{m} = {m}" for _, m, _, _ in fields]
            out.append(f"{indent}    }}")
            if any(bare(m) != key for key, m, _, _ in fields):
                out.append("")
                out.append(f"{indent}    enum CodingKeys: String, CodingKey {{")
                for key, m, _, _ in fields:
                    out.append(f"{indent}        case {m}" + ("" if bare(m) == key else f" = {literal(key)}"))
                out.append(f"{indent}    }}")
        else:
            out.append(f"{indent}    public init() {{}}")
        if name in self.unions:
            out.append("")
            out.append(f"{indent}    /// The keys it names, and those it requires, which a union's decoding goes by.")
            out.append(f"{indent}    static let named: Set<String> = [{', '.join(literal(k) for k in sorted(props))}]")
            out.append(f"{indent}    static let required: Set<String> = [{', '.join(literal(k) for k in sorted(required))}]")
        out.append(f"{indent}}}")
        return out

    def string_enum(self, name, values, description=None, indent=""):
        out = comment(description, indent)
        out.append(f"{indent}public enum {name}: String, Codable, Hashable, Sendable, CaseIterable {{")
        seen = set()
        for value in values:
            case = member_name(value)
            while bare(case) in seen:
                case = f"{bare(case)}_"
            seen.add(bare(case))
            out.append(f"{indent}    case {case}" + ("" if bare(case) == value else f" = {literal(value)}"))
        out.append(f"{indent}}}")
        return out

    def union(self, name, members, description=None, indent=""):
        """An enum with a case for each member, decoded by `Union`'s rule (see the module's docstring)."""
        out = comment(description, indent)
        out.append(f"{indent}public enum {name}: Codable, Hashable, Sendable {{")
        cases = []
        for member in members:
            if "$ref" in member and "properties" in self.schemas[self.ref(member)]:
                swift = type_name(self.ref(member))
                cases.append((member_name(swift), swift, True))
            else:
                swift, _ = self.swift_type(member, name, "member")
                label = {"[String]": "strings", "[Int]": "ints", "[String: JSONValue]": "object"}.get(swift, swift)
                cases.append((member_name(label), swift, False))
        for case, swift, _ in cases:
            out.append(f"{indent}    case {case}({swift})")
        out.append("")
        out.append(f"{indent}    public init(from decoder: any Decoder) throws {{")
        if any(keyed for _, _, keyed in cases):
            out.append(f"{indent}        let keys = Union.keys(of: decoder)")
        for case, swift, keyed in cases:
            if keyed:
                out.append(f"{indent}        if Union.fits(keys, named: {swift}.named, required: {swift}.required),"
                           f" let value = try? {swift}(from: decoder) {{")
                out.append(f"{indent}            self = .{bare(case)}(value)")
                out.append(f"{indent}            return")
                out.append(f"{indent}        }}")
        for case, swift, _ in cases:
            out.append(f"{indent}        if let value = try? {swift}(from: decoder) {{")
            out.append(f"{indent}            self = .{bare(case)}(value)")
            out.append(f"{indent}            return")
            out.append(f"{indent}        }}")
        out.append(f"{indent}        throw Union.noMember({name}.self, decoder)")
        out.append(f"{indent}    }}")
        out.append("")
        out.append(f"{indent}    public func encode(to encoder: any Encoder) throws {{")
        out.append(f"{indent}        switch self {{")
        for case, _, _ in cases:
            out.append(f"{indent}        case .{bare(case)}(let value): try value.encode(to: encoder)")
        out.append(f"{indent}        }}")
        out.append(f"{indent}    }}")
        out.append(f"{indent}}}")
        return out

    def note_unions(self, members):
        for member in members:
            if "$ref" in member:
                self.unions.add(self.ref(member))

    # ── routes ───────────────────────────────────────────────────────────────

    def routes(self):
        """Each route under /api/, by method: what it answers, and a POST's body. Their unions are declared beside them."""
        found = {"get": [], "post": []}
        for path in sorted(self.doc["paths"]):
            if not path.startswith("/api/"):
                continue
            for method, op in sorted(self.doc["paths"][path].items()):
                if method not in found:
                    continue
                route = type_name(path[len("/api/"):])
                schema = op["responses"].get("200", {}).get("content", {}).get("application/json", {}).get("schema")
                answer = "NotJSON"
                if schema is not None:
                    if "$ref" in schema:
                        answer = type_name(self.ref(schema))
                    else:
                        answer = f"{route}Answer"
                        if any(kind == "union" and name == answer for routes in found.values() for kind, name, _, _ in routes):
                            answer = f"{route}{method.capitalize()}Answer"
                        members = [m for m in schema.get("anyOf", [schema]) if not self.is_null(m)]
                        self.note_unions(members)
                        found[method].append(("union", answer, members, op))
                body = None
                if "requestBody" in op:
                    body = type_name(self.ref(op["requestBody"]["content"]["application/json"]["schema"]))
                found[method].append(("route", route, (path, answer, body), op))
        return found

    def write(self):
        routes = self.routes()
        for schema in self.schemas.values():
            for prop in schema.get("properties", {}).values():
                if "anyOf" in prop:
                    self.note_unions([m for m in prop["anyOf"] if not self.is_null(m)])
        taken = SUPPORT & {type_name(name) for name in self.schemas}
        if taken:
            sys.exit(f"schemas named as APISupport.swift's own types: {sorted(taken)}")
        unions = {name for method in routes.values() for kind, name, _, _ in method if kind == "union"}
        if unions & {type_name(name) for name in self.schemas}:
            sys.exit(f"routes' unions named as schemas: {sorted(unions & {type_name(n) for n in self.schemas})}")
        # Every type is API's, so a schema named as one of Swift's (Task) or the app's own hides nothing outside it.
        out = [HEADER, "/// The routes' answers and bodies, each route under /api/ by method (`API.Get`, `API.Post`).", "public enum API {}", ""]
        out.append("extension API {")
        inner = "    "
        for name in sorted(self.schemas):
            schema = self.schemas[name]
            values = self.string_set(schema)
            if "properties" in schema or schema.get("type") == "object":
                out += self.struct(name, schema, inner)
            elif values is not None:
                out += self.string_enum(type_name(name), values, schema.get("description"), inner)
            else:
                kind, _ = self.swift_type(schema, type_name(name), "value")
                out.append(f"{inner}public typealias {type_name(name)} = {kind}")
            out.append("")
        while self.inline:
            owner, name, members = self.inline.pop(0)
            values = self.string_set({"anyOf": members})
            out += self.string_enum(name, values, indent=inner) if values is not None else self.union(name, members, indent=inner)
            out.append("")
        for method in ("get", "post"):
            for kind, name, what, _ in routes[method]:
                if kind == "union":
                    out += self.union(name, what, indent=inner)
                    out.append("")
        # Each route is declared here and says what it answers in an extension of its own, naming API's types in full:
        # inside API.Get a route named for a schema (Digest) would hide it.
        for method in ("get", "post"):
            out.append(f"    /// Each route under /api/ a {method.upper()} reaches.")
            out.append(f"    public enum {method.capitalize()} {{")
            for kind, name, what, _ in routes[method]:
                if kind == "route":
                    out.append(f"        /// {method.upper()} {what[0]}")
                    out.append(f"        public enum {name} {{}}")
            out.append("    }")
        out.append("}")
        for method, protocol in (("get", "GetRoute"), ("post", "PostRoute")):
            for kind, name, what, _ in routes[method]:
                if kind != "route":
                    continue
                path, answer, body = what
                out.append("")
                out.append(f"extension API.{method.capitalize()}.{name}: {protocol} {{")
                out.append(f"    public static let path = {literal(path)}")
                out.append(f"    public typealias Answer = {answer if answer == 'NotJSON' else 'API.' + answer}")
                if method == "post":
                    out.append(f"    public typealias Body = API.{body or 'NoBody'}")
                out.append("}")
        return "\n".join(out) + "\n"


def main(doc_path, out_path):
    with open(doc_path, encoding="utf-8") as f:
        doc = json.load(f)
    text = Writer(doc).write()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main(*sys.argv[1:3])
