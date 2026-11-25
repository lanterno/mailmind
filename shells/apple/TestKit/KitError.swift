import Foundation

/// What the test kit couldn't do, in words. The kit throws rather than asserting, so the driver can use it as the
/// tests do: a test lets the error fail it, and the driver sends the words back.
struct KitError: Error, CustomStringConvertible {
    let description: String

    init(_ description: String) {
        self.description = description
    }
}
