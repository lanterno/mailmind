// What types.ts makes of the schemas. These are checked by tsc, which `just check-web` runs over the tests too.
import { describe, expectTypeOf, it } from "vitest";
import type { AnyOf, GetAnswers, PostAnswers, PostBodies } from "./types";

describe("the API's types", () => {
  it("give a file, a document or a stream no JSON to read", () => {
    expectTypeOf<"/api/events" | "/api/export" | "/api/message/{pk}/body.html" | "/api/message/{pk}/attachment/{part_id}">().not.toExtend<keyof GetAnswers>();
    expectTypeOf<"/api/message/{pk}/body">().toExtend<keyof GetAnswers>();
  });

  it("hold only the API's paths, each by its method", () => {
    expectTypeOf<keyof GetAnswers>().toExtend<`/api/${string}`>();
    expectTypeOf<"/api/snooze">().toExtend<keyof PostAnswers>();
    expectTypeOf<"/api/snooze">().not.toExtend<keyof GetAnswers>();
  });

  it("say what an answer that varies by branch may be", () => {
    expectTypeOf<GetAnswers["/api/search"]>().toExtend<{ ok: true; query: string; has_more: boolean }>();
    expectTypeOf<GetAnswers["/api/thread"]>().toExtend<{ ok: true; reply_pk: number | null }>();
  });

  it("hold no refusal: a failure is a problem document at a status of its own, never a 200", () => {
    type Any<A> = A[keyof A];
    expectTypeOf<Extract<Any<GetAnswers> | Any<PostAnswers>, { ok: false }>>().toBeNever();
  });

  it("give a POST's body what its model reads", () => {
    expectTypeOf<PostBodies["/api/snooze"]>().toExtend<{ pk?: number | null; until?: number | string | null }>();
    expectTypeOf<{ pk: 1; preset: "tomorrow" }>().toExtend<PostBodies["/api/snooze"]>();
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
