// The API's answers and bodies by path, from schema.gen.ts, which `just gen-api` writes from the server's schemas (its OpenAPI
// document). A path is here only if its route answers JSON: a file, a document or a stream (the mail frame, an attachment, the
// event stream, the export) has no JSON type to misuse.
import type { paths } from "./schema.gen";

type ApiPath = keyof paths & `/api/${string}`;

type JsonOf<Response> = Response extends { content: { "application/json": infer Json } } ? Json : never;

// What a route answers when it can, as its schema says: a union where it varies by branch. A failure is no 200: it comes to the
// caller as an ApiError (client.ts), the problem document it answered.
type Answers<Method extends "get" | "post"> = {
  [P in ApiPath as [JsonOf<NonNullable<paths[P][Method]>["responses"][200]>] extends [never] ? never : P]:
    JsonOf<NonNullable<paths[P][Method]>["responses"][200]>;
};

export type GetAnswers = Answers<"get">;
export type PostAnswers = Answers<"post">;

// Any member of a union, read field by field: a field some member lacks is optional, since reading it there gives undefined. For
// code that takes each of an answer's shapes as one, as a list's rows do. (`InEvery` is `keyof U`, the fields every member has, but
// not written as one: a mapped type over a bare `keyof U` would go member by member.)
type InEvery<U> = Extract<keyof U, PropertyKey>;
type KeyOfAny<U> = U extends unknown ? keyof U : never;
type FieldOf<U, K extends PropertyKey> = U extends unknown ? (K extends keyof U ? U[K] : undefined) : never;
export type AnyOf<U> = { [K in InEvery<U>]: FieldOf<U, K> } & { [K in Exclude<KeyOfAny<U>, InEvery<U>>]?: FieldOf<U, K> };

// What a POST's body may say, as its schema says. A field the body leaves out is the use case's to word.
export type PostBodies = {
  [P in keyof PostAnswers]: NonNullable<NonNullable<paths[P]["post"]>["requestBody"]> extends { content: { "application/json": infer Body } } ? Body : never;
};
