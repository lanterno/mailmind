// The page's way to the API: same origin, the CSRF token on every request, and an error that says what went wrong.
// Paths and answers are typed from types.gen.ts, which `just gen-api` writes from what the routes really answer.
import type { GetAnswers, PostAnswers } from "./types.gen";

export const AI_OVER_HEADER = "X-MailMind-AI-Over-Limit";

export interface OverLimit { spent_micros?: number; limit_micros?: number }

export class ApiError extends Error {
  readonly status: number;
  readonly overLimit: boolean;
  readonly aiOff: boolean;

  constructor(message: string, status: number, overLimit: boolean, aiOff: boolean) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.overLimit = overLimit;
    this.aiOff = aiOff;
  }
}

export interface ApiOptions extends RequestInit {
  // At the daily AI limit: asked for a request a person started. Return true to send it again over the limit.
  // One the page makes on its own leaves this out and gets the 402 as an ApiError (overLimit).
  askOverLimit?: (answer: OverLimit) => Promise<boolean>;
}

export function csrfToken(doc: Document = document): string {
  return doc.querySelector<HTMLMetaElement>('meta[name="csrf"]')?.content ?? "";
}

async function errorBody(r: Response): Promise<Record<string, unknown> | null> {
  try {
    const parsed: unknown = await r.json();
    return parsed !== null && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { askOverLimit, ...init } = options;
  const headers = new Headers(init.headers);
  headers.set("X-CSRF-Token", csrfToken());
  const r = await fetch(path, { ...init, headers });
  if (r.ok) return (await r.json()) as T;
  const body = await errorBody(r);
  const overLimit = Boolean(body?.["over_limit"]);
  if (r.status === 402 && overLimit && askOverLimit && !headers.has(AI_OVER_HEADER) && (await askOverLimit(body as OverLimit))) {
    headers.set(AI_OVER_HEADER, "allow");
    return request<T>(path, { ...init, headers });
  }
  const message = typeof body?.["error"] === "string" ? body["error"] : `HTTP ${r.status}`;
  throw new ApiError(message, r.status, overLimit, Boolean(body?.["ai_off"]));
}

// A path of the API with its {pk} filled in, and a query if it has one.
type Filled<P extends string> = P extends `${infer A}{pk}${infer B}` ? `${A}${number}${Filled<B>}` : P;
type WithQuery<P extends string> = Filled<P> | `${Filled<P>}?${string}`;

type RouteOf<Answers, Path extends string> = {
  [P in keyof Answers & string]: Path extends WithQuery<P> ? P : never;
}[keyof Answers & string];

export function get<Path extends WithQuery<keyof GetAnswers & string>>(path: Path, options?: ApiOptions) {
  return request<GetAnswers[RouteOf<GetAnswers, Path>]>(path, options);
}

export function post<Path extends WithQuery<keyof PostAnswers & string>>(path: Path, body: unknown, options: ApiOptions = {}) {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  return request<PostAnswers[RouteOf<PostAnswers, Path>]>(path, { ...options, method: "POST", headers, body: JSON.stringify(body) });
}
