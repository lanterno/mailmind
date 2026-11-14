import Foundation

/// What the generated API types (API.gen.swift) lean on: a value of any JSON, the routes as types, and how a union of
/// answers is decoded.

/// Any JSON value: what a schema leaves open, as `Any` is in the server's models.
public enum JSONValue: Codable, Hashable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public init(from decoder: any Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() {
            self = .null
        } else if let bool = try? value.decode(Bool.self) {
            self = .bool(bool)
        } else if let number = try? value.decode(Double.self) {
            self = .number(number)
        } else if let string = try? value.decode(String.self) {
            self = .string(string)
        } else if let array = try? value.decode([JSONValue].self) {
            self = .array(array)
        } else {
            self = .object(try value.decode([String: JSONValue].self))
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .null: try value.encodeNil()
        case .bool(let bool): try value.encode(bool)
        case .number(let number): try value.encode(number)
        case .string(let string): try value.encode(string)
        case .array(let array): try value.encode(array)
        case .object(let object): try value.encode(object)
        }
    }
}

/// A route under /api/: its literal path, and what it answers.
public protocol Route {
    associatedtype Answer: Sendable
    static var path: String { get }
}

/// A route a GET reaches.
public protocol GetRoute: Route {}

/// A route a POST reaches, with the body it takes.
public protocol PostRoute: Route {
    associatedtype Body: Encodable & Sendable
}

/// What a route answers that isn't JSON: a mail body's frame, an attachment, an export. Its bytes and their type.
public struct NotJSON: Sendable, Hashable {
    public var data: Data
    public var contentType: String
}

/// How a union of answers is decoded: the first member whose keys the answer's keys fit, else the first that decodes.
/// The routes' schemas name every field an answer has, so the keys alone tell the members apart.
enum Union {
    private struct AnyKey: CodingKey {
        var stringValue: String
        var intValue: Int? { nil }
        init(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }

    /// The keys of the object being decoded; none for what isn't an object.
    static func keys(of decoder: any Decoder) -> Set<String> {
        guard let object = try? decoder.container(keyedBy: AnyKey.self) else { return [] }
        return Set(object.allKeys.map(\.stringValue))
    }

    /// Whether `keys` hold every key a member requires and none it doesn't name.
    static func fits(_ keys: Set<String>, named: Set<String>, required: Set<String>) -> Bool {
        required.isSubset(of: keys) && keys.isSubset(of: named)
    }

    static func noMember<T>(_ type: T.Type, _ decoder: any Decoder) -> DecodingError {
        DecodingError.typeMismatch(type, .init(codingPath: decoder.codingPath,
                                               debugDescription: "no member of \(type) reads this answer"))
    }
}
