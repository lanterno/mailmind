#if os(macOS)
import Foundation

/// The routes as compose and the reader ask them, an AI call a person started with their leave to go over today's
/// limit among them: `Core` answers them in the app, and a stand-in in the tests.
protocol Routes: OverLimitRoutes {
    func get<R: GetRoute>(_ route: R.Type, _ query: KeyValuePairs<String, String?>) async throws -> R.Answer
        where R.Answer: Decodable
}

extension Core: Routes {}

/// What a person is told when a route said no: the server's words, else a word of our own.
func said(_ error: any Error) -> String {
    (error as? Problem)?.description ?? String(localized: "Something went wrong.")
}

/// Whether a refusal says the thing it names isn't there or has moved on.
func gone(_ error: any Error) -> Bool {
    guard let problem = error as? Problem else { return false }
    return problem.status == 404 || problem.status == 409
}
#endif
