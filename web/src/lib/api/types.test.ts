// What types.ts makes of the schemas. These are checked by tsc, which `just check-web` runs over the tests too.
import { describe, expectTypeOf, it } from "vitest";
import type { AnyOf, GetAnswers, PostAnswers, PostBodies } from "./types";

describe("the API's types", () => {
  it("give a file, a document or a stream no JSON to read", () => {
    expectTypeOf<"/api/events" | "/api/export" | "/api/message/body.html" | "/api/message/attachment">().not.toExtend<keyof GetAnswers>();
    expectTypeOf<"/api/message/body">().toExtend<keyof GetAnswers>();
  });

  it("hold only the API's paths, each by its method", () => {
    expectTypeOf<keyof GetAnswers>().toExtend<`/api/${string}`>();
    expectTypeOf<"/api/snooze">().toExtend<keyof PostAnswers>();
    expectTypeOf<"/api/snooze">().not.toExtend<keyof GetAnswers>();
  });

  it("say what an answer that varies by branch may be", () => {
    expectTypeOf<GetAnswers["/api/search"]>().toExtend<{ ok: true; query: string; has_more: boolean }>();
    expectTypeOf<GetAnswers["/api/thread"]>().toExtend<{ ok: true; reply_key: number | null }>();
  });

  it("hold no refusal: a failure is a problem document at a status of its own, never a 200", () => {
    type Any<A> = A[keyof A];
    expectTypeOf<Extract<Any<GetAnswers> | Any<PostAnswers>, { ok: false }>>().toBeNever();
  });

  it("give a POST's body what its model reads", () => {
    expectTypeOf<PostBodies["/api/snooze"]>().toExtend<{ key?: number | null; folder?: string | null; until?: number | string | null }>();
    expectTypeOf<{ key: 1; folder: "INBOX"; preset: "tomorrow" }>().toExtend<PostBodies["/api/snooze"]>();
  });

  it("name a message by its key and a copy by its key and folder, and nothing by a pk", () => {
    expectTypeOf<PostBodies["/api/archive"]>().toExtend<{ key: number; folder: string }>();
    expectTypeOf<{ pk: 1 }>().not.toExtend<PostBodies["/api/archive"]>();
    expectTypeOf<"/api/message/{pk}/body">().not.toExtend<keyof GetAnswers>();
    expectTypeOf<"pk">().not.toExtend<keyof GetAnswers["/api/message/body"]>();
  });
});

describe("AnyOf", () => {
  type Either = AnyOf<{ syncing: true; n: number } | { syncing: false; why: string }>;

  it("keeps what every member has, and makes the rest optional", () => {
    expectTypeOf<Either["syncing"]>().toEqualTypeOf<boolean>();
    expectTypeOf<Either["n"]>().toEqualTypeOf<number | undefined>();
    expectTypeOf<{ syncing: false; why: string }>().toExtend<Either>();
  });
});
