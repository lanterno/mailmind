# AI calls

Everything routes through `assistant.calls.call_claude_json(prompt, schema)` (or
`call_claude` for plain text), which call `mailmind.bootstrap.assistant.gateway()`. Claude is the
default engine (decision D8); since #49 a person may choose another provider in Settings. The `Gateway` builds each
request from `mailmind.assistant.domain`: effort where the model takes one (`models.takes_effort`), the
refusal fallback where it takes that (`models.takes_fallback`), the schema in structured
output's form (`schema.structured`) and the system prompt. It sends it through a `LanguageModel` and gets back a
`Reply`: the text, the model that served it, its `Usage` and its stop reason. A call with no
reply raises `ModelError` (a `RuntimeError` with `status` and `permanent`).

The providers are a closed set, `kernel.ai_provider.AiProvider`, and `assistant.domain.providers.PROVIDERS` says what
each is: `anthropic` (Claude, through the Anthropic SDK), and `openai`, `gemini`, `local` (a model on this Mac: Ollama,
LM Studio or any other model server here, at Ollama's `http://127.0.0.1:11434/v1` unless another URL is given) and
`compatible` (any other service that speaks OpenAI's Chat Completions, at a URL a person gives), all through one
adapter on the standard library, `adapters.openai_chat`, whose wire format is `adapters.chat_wire`. Four preferences
choose: `ai_enabled` (AI mode), `ai_provider` (`anthropic` by default), `ai_model` and `ai_base_url` (each `""` for the
provider's own; a URL is https, or http on this Mac, with a port from 1 to 65535 where it has one (an empty port, as in
`http://127.0.0.1:/v1`, is malformed), and a model on this Mac's is at 127.0.0.1, ::1 or localhost, or refused in
those words: a server on another machine is `compatible`, over https). Choosing another provider without
naming a model or URL sets both back to `""`. `MAILMIND_MODEL` is the Claude model: it wins over the model chosen
while Claude is the provider, and with any other it is ignored. `ANTHROPIC_BASE_URL` stands in for Claude's URL when
none is chosen.

AI mode the file doesn't say (`accounts.domain.preferences.ai_mode_default`) is on where `mailmind.config.json` names a
key for any provider or `ANTHROPIC_API_KEY` is set, so whoever set Claude up keeps AI, and off otherwise: a fresh install
sends nothing, shows nothing only AI can do, and asks for nothing until AI mode is turned on. `GET /api/preferences`
answers what stands, and setting it writes it to the file. `PREFERENCE_DEFAULTS` says off, as a fresh install has it.

`bootstrap.assistant.engine()` reads what is in force at each call (`providers.in_force`), and the gateway makes the
model afresh when it changes: `AnthropicModel`, `ChatModel`, or `DisabledModel` when AI is off, for the first of
`turned-off`, `no-key`, `no-url` (a compatible service with none) or `no-model` (a model on this Mac or a compatible
service with none chosen; neither needs a key, so neither is ever `no-key`). Off, `assistant.calls.engine()` is `"none"`, and every call, a background job's too, raises `AIOff`
(from `application.ports`, in words that say why) before anything is sent. Turned off, the engine looks up no key at
all: no Keychain read, no `op` run. `tests/contract/test_ai_contract.py` and
`tests/contract/test_openai_chat_contract.py` hold the two adapters to the contract against the fake APIs.

Only `mailmind/assistant/adapters/anthropic_sdk.py` imports `anthropic`, and `adapters.wire` holds what it sends and
how it reads a reply. `ChatModel` posts to `{base}/chat/completions`: the system prompt as the first message, the
schema as a `json_schema` response format (strict only where every object is closed and needs each property), the
16000-token ceiling only for OpenAI (`max_completion_tokens`) and Gemini (`max_tokens`), none for a model on this Mac or
a compatible service, and no effort or fallback. A 400 naming what a service won't take has the call asked once more
without it, each at most once a call: `response_format`, from a model on this Mac or a compatible service, the schema
then asked for in words at the end of the last message, after a blank line ("Reply with only a JSON object that matches
this JSON Schema: " and the schema as compact JSON); `max_tokens` or `max_completion_tokens`, from OpenAI or Gemini, the
ceiling. A 400 saying the key isn't valid (a `details` entry whose reason is `API_KEY_INVALID`, or a message starting
"API key not valid", as Gemini answers) is a refused key, as a 401 or 403 is: its error carries status 401, so its
words, the breaker and the models' listing take it as one. A refused key's error is only
`<provider> API <status>: the key was refused`, with none of what the service said. Any other error carries what the
service said (its `error`, from the first element where the body is an array), the key taken out of the whole of it
before it is cut to 200 characters. A
`message.refusal` is a refusal; a reasoning model's thinking ahead of its reply (`<think>…</think>` or
`<thinking>…</thinking>`) is dropped before the reply is read (`chat_wire.without_thinking`); cached prompt tokens are
cache reads, nothing writes a cache, and the output is `completion_tokens`, or `total_tokens` less `prompt_tokens`
where that is more (Gemini leaves a thinking model's thinking out of `completion_tokens`). An attempt waits up to 120
seconds, and 300 for a model on this Mac or any URL on this Mac, where a model loads on its first use (`Engine.timeout`).
On this wire, on both servers, that limit is a deadline for the whole attempt, connecting, sending and reading the
whole answer, and an answer is read to 32 MiB at most.

Each provider's key is `assistant.config_key(provider)`: the top-level `"<provider>_api_key"` in
`mailmind.config.json`, any credential style but the Bridge password, which there means no key. `uv run python -m
mailmind ai key [--provider P]`, or `POST /api/ai/key` `{provider, key}` from Settings, keeps it in the login Keychain
as `<provider>-api-key` and names that item there; `POST /api/ai/key/clear` `{provider}` takes both out. Each answers
`{"ok": true, "kept": [...]}`, the providers whose key the file names, as `GET /api/ai/key` does. `ai key` and
`POST /api/ai/key` trim a key's ends and refuse one with whitespace or a control character inside it: "An API key
can't hold spaces or line breaks.", a 400 from the route; any other key they keep. A key is sent only if every
character in it is printable ASCII without a space (`!` to `~`). Any other key, wherever it came from, a kept one too,
is a refused key: never sent, its error `<provider> API 401: the key was refused` ("Claude API 401: the key was refused")
with status 401, and its text is in no error. Only Claude's falls back to `ANTHROPIC_API_KEY`; a config key that won't
resolve means no key, never a fall-through to the environment's. A model on this Mac and a compatible service are
sent a key only where one is kept (a model on this Mac's is `local_api_key`, in the Keychain as `local-api-key`).
`uv run python -m mailmind ai on|off` turns AI mode on or off as Settings does.

`GET /api/ai/models?provider=P` (`application.catalog`) lists the models a provider offers, for Settings: at the URL
chosen while P is the provider in force, else P's own, with the key kept for it. It answers `{ok, models, default}`,
sorted, with Gemini's `models/` dropped and, in every list but Claude's, the ids that can't chat left out (embeddings,
speech, images and the like: `nomic-embed-text` goes). It spends nothing, so it goes with AI mode off too; no key, no URL
or a key the provider refuses is a 400 in words, and a provider that doesn't answer is a 502.

`GET /api/ai/local` finds the model servers on this Mac, for a person choosing a model on this Mac: it asks each that
`providers.LOCAL_SERVERS` knows (Ollama on 11434, LM Studio on 1234, llama.cpp on 8080, vLLM on 8000, Jan on 1337 and
GPT4All on 4891, each `http://127.0.0.1:<port>/v1`) for `GET {url}/models`, all at once and each whole exchange a
second at most, with no key, and answers `{ok, servers: [{name, url, models}]}` for those that answered a list, in that
order, with the models that can chat. It reaches only this Mac, goes with AI mode off, and nothing answering is
`servers: []`, never an error (`adapters.local_servers`, `Catalog.local_servers`).
`MAILMIND_NO_SCHEMA=1` falls back to free-form text. AI actions never
fire on their own — drafts wait, bulk commands require a preview and confirm. The three that do are opt-in and off by default: the morning's brief,
written ahead of the first open, the week's review, written ahead on Monday morning and kept in the database, never sent as mail, and Tidy's
daily run, which files only what the brief's Tidy offers and reports it for one Undo.

An assessment keeps how it came to be (migration 43, `kernel.assessment`): `served_by`, the model `Reply.model` names, which
a server-side fallback can make another; `prompt_version`; and `outcome`, which is `assessed`, or `unusable` or `refused` for
the stand-in stored so a message doesn't go round for ever. `assessments.model` stays the label shown beside a summary, the
model asked for or `parse-error`. `Calls.reply_to` is the whole `Reply` for a caller that keeps these. A batch's answers take
the version its row was sent with, since they can come in after an upgrade. A row from before has all three NULL, and
`answers.assessment` reads them as None: unknown, never worked out. So far nothing but that read looks at them.

`domain.prompts` is every word the model is told. `SYSTEM` goes on every call, a batch's requests included, and is the
one statement that the mail is untrusted data. Each kind of call has a `Template` and a version, which says what to do and
leaves that to `SYSTEM`; only the plan's adds a word about its command, which is typed and not mail. `Template.fill` fills the
slots in one pass, so a subject holding `{blocks}` stays those words, and a template's own braces need no doubling. Words
that change raise the version (all of them, for `SYSTEM`), and `tests/unit/assistant/test_prompts.py` holds the two together.

`schema.structured` drops the keywords structured output rejects (`minimum`, `maximum`,
`maxLength`) and closes every object, so callers must keep clamping what they read. The
adapter sends `fallbacks: "default"` with its beta on the models that accept it, reads a
refusal as an empty reply, and retries 408/409/429/5xx twice. It sends the key as
`Authorization: Bearer`; the fake API takes that or `x-api-key`, as the real one does. An error carries what the API
said with the key taken out of the whole of it before it is cut to 200 characters, as on the Chat Completions wire.

One contract: a failed call raises `RuntimeError` (the caller retries
later, nothing is cached); a successful but unusable reply returns `{}` (the caller
stores its fallback). No key degrades to no summaries, with nothing sent, rather than
failing; keep that property. A call that was never sent raises a `NotSent`: `AIOff`, or a
`HeldBack` from the cost governor (`OverLimit`, `Deferred`). A caller lets `NotSent` through
(`except NotSent: raise`) ahead of its own `except Exception`, and so does a route's own
catch for `OverLimit` and `AIOff`, so `shared.failure_answer` can answer
them: `OverLimit` as a 402 (below), `AIOff` as a 409 with `ai_off: true`, which the page shows as
"AI is off" rather than an error to retry. `call_claude`, `call_claude_json` and `reply_to` go through `Status.noting`,
which records each call's outcome for `assistant.status.report()` (not an `AIOff`: no call was made) — served on `/api/digest` as
`ai`, and the source of the brief's "AI is off" card and the sync menu's AI line. A new
entry point that bypasses them won't show up there.

The `Gateway` counts every call it sends in the usage ledger, migration 21's `ai_usage` (the
`UsageLedger` port, `adapters.sqlite_ledger`). There is one row per local day, feature, model
asked, model that served it and outcome: `ok`, `refused`, `rejected` (a permanent 4xx) or
`failed`. Each row holds the calls, their tokens and `cost_micros`, whichever provider served them. `domain.pricing`
computes the cost from each provider's list prices (one table: Claude, OpenAI and Gemini) in integers, rounded up, with
cache writes at 1.25 times input for Claude only, and a cost never past 2^63-1 micro-dollars. On both servers token
counts and costs are held at 2^63-1 wherever they are added: a call's counts, a batch's tallies, the ledger's upsert
(`MIN(a + b, 9223372036854775807)` for the cost and each token column), the day's totals and the governor's room. A
name is looked up after its last `/` (so `openai/gpt-6.1-sol` and
Gemini's `models/...` find their rows). An unknown model is free on a model on this Mac or at any URL on this Mac, and otherwise the
dearest of its family (`claude-`, `gpt-` or `o<digit>`, `gemini-`) or of the whole table; a server-side fallback costs
the dearer of the two models. `Gateway.ask` requires a
`feature`: `triage`, `triage-batch`, `thread`, `brief`, `delta`, `review`, `draft`, `draft-ahead`, `rewrite`, `extract`,
`screen`, `rationale`, `sender`, `brief-ahead`, `review-ahead`, `auto-tidy`, `ask` and `plan` so far, and a new AI call names its own. `call_claude` and `call_claude_json`
pass it on, `other` when the caller names none. With AI off nothing is counted. A ledger write
that fails is logged by class name and never fails the call.

What the ledger holds is read back in three places:
- `assistant.status.report()` carries `spent_today_micros`, which the sync menu's AI line shows.
- `/api/debug/ai?days=N` (30 by default) lists each day by feature.
- `uv run python -m mailmind ai usage` prints the same. It reads the ledger only and never migrates.

The `Gateway` asks the cost governor (`application.governor.CostGovernor`,
`assistant.governor()`) before every call it sends. `domain.features.kind` sorts each feature:
`triage` is background, `draft-ahead` speculative, `brief-ahead`, `review-ahead` and `auto-tidy` scheduled (held to the background's bars, and what a person turned on), and every other feature interactive.
- Slots. Two interactive calls at a time, and two background or speculative ones beside them.
- Yielding. Background and speculative calls don't start while an interactive one runs, or for
  45 seconds after one ends (`YIELD_SECONDS`).
- Presence. A speculative call also needs someone to have used the app in the last 45 minutes.
  `shared.touched` calls `saw_someone()` for every `/api/` request a person makes, except the
  polling routes in `UI_QUIET`. A start counts as someone here.
- The daily limit is `assistant.limit_micros()`: the top-level `"ai_daily_limit_usd"`, a
  positive number, or $15 (decision D7). It counts everything the ledger holds for today, and a
  ledger that can't be read counts as over. At the limit, background and speculative calls raise
  `Deferred("limit")`. An interactive call raises `OverLimit`, which `shared.failure_answer`
  answers as a 402 with `over_limit: true`. `request` in `lib/api/client.ts` asks (the `askOverLimit` a call passes it), then sends
  the request again with `X-MailMind-AI-Over-Limit: allow` (`shared.AI_OVER_HEADER`), which
  runs it inside `assistant.allowing_over()`. The Mac app asks the same question with an alert
  (`shells/apple/Sources/Mac/Shared/OverLimit.swift`) and sends again through the host's
  `request_over_limit`, the one header a shell can add.
- The breaker. A 401 or 403 stops every background and speculative call, and three permanent
  4xx in a row for one feature and model stop that pair. It stays open until the model in use
  changes, which a new key or base URL does. Interactive calls still go.

Background work asks `gateway().held_back(feature)` before a batch, and a call that was held
back anyway raises `Deferred(reason)`. Nothing is counted for it, and `Status.noting` doesn't record it
as a failure. Nothing in the governor is durable: a restart closes the breaker, and the limit
holds across one because the ledger does. `/api/debug/ai` shows its `snapshot()`, and the
tests start each one afresh with `forget()`.

A backlog goes through the Message Batches API at half price (`pricing.batch_micros`, decision D7), only while Claude
is the provider (`LanguageModel.takes_batches`): with another, a backlog goes synchronously, a batch already out waits
until Claude is chosen again, and `assess --batch` says "Batches need Claude as the AI provider." `BATCH_MIN`
messages waiting is one, whether a first import or after an outage, and new mail stays synchronous.
`assistant.assessments.batch_submit` sends it and `batch_collect` brings the answers in, both from its `round`, which
collects first since that sends no mail and the answers are paid for. `uv run python -m mailmind assess --batch` does
the same for any size. `MAILMIND_NO_BATCHES=1` keeps the job from sending one, so a backlog goes synchronously as it
did; a batch already out is still brought in. The Rust server's assess job does the same (`mm_assistant::batches`,
the calls in `mm-claude`'s `batches`) on the same rows, so either server brings in a batch the other sent. It reads a
batch's answers from the API it sent the batch to, never from the `results_url` the API names, so the key goes nowhere
else.
- One at a time (migration 36: `ai_batches`, and `ai_batch_items` by message key). `needs_summary` leaves out what a
  batch holds, and while one is out everything older than the newest it took (`cutoff_ts`), which waits for the next
  batch rather than going at full price.
- The limit. A batch's cost is only known when its answers are in, so it takes what fits in `1/BATCH_SHARE` of what
  today leaves by `pricing.batch_estimate`, which errs high. `CostGovernor.room` counts the estimates of batches
  still out (`UsageLedger.reserved`) against background work, not against someone asking, so a day with a batch out
  and heavy use can pass the limit by up to that share. Answers count on the day they are collected.
- Sending is never retried: the API has no idempotency key, so another try could pay twice, and the next round tries
  anyway. A row is committed as `submitting` first. One that fails is dropped, and one a crash left is dropped after
  `BATCH_SEND_GIVE_UP`; a batch that reached the API in that window is paid for and never collected.
- A batch holds mail, so `batch_collect` deletes it from the API once its answers are stored, trying again each
  round until it goes. A message with no answer (errored, expired, canceled) waits again.

The brief, the delta and the review each have a table (migration 37): `briefs` and `reviews` by account set
and local day, `deltas` by account set, watermark and fingerprint, so a window that changes is written once
more however large it is. A review's lists and a brief's groups are stored by message key and read back as
the copies those messages have now, by key, folder and uid, and a message that has gone leaves them. A delta's items are the model's own
words, with no message behind them. Each table keeps a week, by `created_at`, and forgets what is older when it is next written. They are read
and written apart:
- A GET of `/api/briefing`, `/api/briefing/since` or `/api/review` reads only what is stored. When
  there is none it answers `missing: true` and calls nothing (the builders' `generate=False`).
- A POST to the same path writes one, reading first, and its `/refresh` POST writes it again.
- A review is read on the day it was written, unless the `weekly_review` preference is on. Then the `review-ahead` job
  (`background.review_ahead_round`, `Reviews.write_ahead`) writes one at its first round on Monday from five until noon local time, once
  triage has read the last day's mail and only if none is stored this week, and a read takes the newest stored since Monday
  (`review.week_so_far`), so the one written ahead shows all week for nothing. One from an earlier day keeps what it said was filed away,
  and its `week_start` is the week it looked back on. A review written on Monday morning is under a week old until the next Monday's.
- The page GETs, and POSTs only on `missing`. What it writes on its own (the brief as the Inbox
  opens, the delta for the new-mail pill) passes no `askOverLimit`. At the limit it
  then gets the 402 as an `ApiError` (`overLimit`), and the brief offers "Write it" instead of asking
  unprompted.

A new AI route spends on a POST. The one GET that still starts a call is drafting ahead's kick,
which needs the CSRF token.
