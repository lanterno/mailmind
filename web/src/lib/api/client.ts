// The page's way to the API: same origin, the CSRF token on every request, and an error that says what went wrong.
// Paths and answers are typed from types.ts, which reads them off schema.gen.ts: `just gen-api` writes that from the routes' schemas.
import type { GetAnswers, PostAnswers } from "./types";

export const AI_OVER_HEADER = "X-MailMind-AI-Over-Limit";

export interface OverLimit { spent_micros?: number; limit_micros?: number }

// What a failure answers: a problem document (RFC 9457). `detail` is what a person is told, `type` names the kind of problem
// (`about:blank` where the status says it all), and the rest is what the page acts on: `over_limit`, `ai_off`, `busy` and the like.
export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  [extension: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly detail: string;       // "" where the server left the words to the page
  readonly problem: Problem;     // the whole of it, for what else it says
  readonly overLimit: boolean;
  readonly aiOff: boolean;
  readonly busy: boolean;
  readonly readOnly: boolean;
  readonly conflict: boolean;
  readonly retry: boolean;

  constructor(status: number, problem: Problem) {
    const said = typeof problem.detail === "string" ? problem.detail : "";
    super(said || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.type = typeof problem.type === "string" ? problem.type : "about:blank";
    this.detail = said;
    this.problem = problem;
    this.overLimit = problem["over_limit"] === true;
    this.aiOff = problem["ai_off"] === true;
    this.busy = problem["busy"] === true;
    this.readOnly = problem["read_only"] === true;
    this.conflict = problem["conflict"] === true;
    this.retry = problem["retry"] === true;
  }
}

// The server's no: a 4xx, in words for a person where it has some. A 5xx, or no answer at all, is the page's to word.
export function refusalOf(e: unknown): ApiError | null {
  return e instanceof ApiError && e.status >= 400 && e.status < 500 ? e : null;
}

export interface ApiOptions extends RequestInit {
  // At the daily AI limit: asked for a request a person started. Return true to send it again over the limit.
  // One the page makes on its own leaves this out and gets the 402 as an ApiError (overLimit).
  askOverLimit?: (answer: OverLimit) => Promise<boolean>;
}

export function csrfToken(doc: Document = document): string {
  return doc.querySelector<HTMLMetaElement>('meta[name="csrf"]')?.content ?? "";
}

async function problemOf(r: Response): Promise<Problem> {
  try {
    const parsed: unknown = await r.json();
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Problem) : {};
  } catch {
    return {};
  }
}

export async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { askOverLimit, ...init } = options;
  const headers = new Headers(init.headers);
  headers.set("X-CSRF-Token", csrfToken());
  const r = await fetch(path, { ...init, headers });
  if (r.ok) return (await r.json()) as T;
  const e = new ApiError(r.status, await problemOf(r));
  if (r.status === 402 && e.overLimit && askOverLimit && !headers.has(AI_OVER_HEADER) && (await askOverLimit(e.problem as OverLimit))) {
    headers.set(AI_OVER_HEADER, "allow");
    return request<T>(path, { ...init, headers });
  }
  throw e;
}

// A path of the API, and a query if it has one.
type WithQuery<P extends string> = P | `${P}?${string}`;

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
