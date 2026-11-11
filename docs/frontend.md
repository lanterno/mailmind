# The page

`web/index.html` and `web/src/main.tsx` are the React app (React 19 with the React Compiler, TanStack Query,
React Aria Components), `web/assets/styles/app.css` its CSS, a list of `@import`s whose order is the cascade (what is shared stands beside it, a feature's rules beside the feature, and `fonts.css` is the self-hosted fonts, Latin, Cyrillic and Greek; Japanese glyphs come from
system fonts), and `web/assets/theme.js` the one classic script, a file since the page's policy forbids an inline one. A new top-level script
joins `CLASSIC` in `web/vite.config.js`. `just build-web` (Vite, Node) writes it all, named by content, to `mailmind/http/dist` (gitignored),
and `just run` builds it first. `mailmind.platform.webroot` serves only that folder, with gzip and an ETag, so an edit reaches the app through a
build. `/assets/` serves only `.css`, `.js` and `.woff2`, each kept for good. With nothing built `/` and each place in the app (`routers/static._is_place`, as
`app/route.ts` writes one) answer 404 and say how to build, and `uv run python -m mailmind serve` logs a line saying so. `/next` redirects, to the app's own
places only. `/api/events` opens with `hello{build}` (`sse.HELLO`, no id), naming `webroot.edition`: the files, and a hash of the token a
restart changes. A tab that loaded another offers a reload (`newBuild` in `app/realtime.ts`). `web/src` is strict TypeScript, and its API client
is `lib/api/client.ts`, typed by `lib/api/types.ts` from the routes' schemas. Where you are lives in the URL
(`app/route.ts`: a `ListSource` and what is open), and code outside React reads it through `currentRoute()`, never from
`location` itself. A URL names nothing of a person's ([At rest](security.md#at-rest)): `pathOf` writes a route that holds an
address, a folder, a search, a topic or an opened message as a place, `/p/<id>`, with a random id made in the page and kept
for that route as long as the page lasts, so a navigation waits for nothing and the same route is the same URL. A place
reaches the server only when it is about to be used: `go()` sends the one it goes to, and a capturing listener on the
document sends a link's (`a[href^="/p/"]`) on `pointerover`, `pointerdown`, `focusin`, `contextmenu` or `dragstart`, one of
which comes before the link can be opened in a new tab, so a link drawn sends nothing and the rail's links add no rows. Each
goes once, with the others sent in the same turn of the page, to `POST /api/places` once that turn is over, in batches of
`LIMITS.places_at_once`, as keepalive so a batch on its way when the page goes still arrives, and again a little later if the
server couldn't be reached; one longer than `LIMITS.place_bytes` stays in the page. A page opened at
`/p/<id>` reads the route from its `mailmind-place` meta, which the server fills in, held to a route's shape (`routeFrom`),
and one it doesn't know is home. Back or Forward to a place the page doesn't know, an entry from before a reload, loads the
page from the server again. The rest (`/`, `/all`,
`/all/__snoozed__` and the other views over every account, `/documents`, `/brief/needs` and `tidy`) are written as
themselves, and `parseRoute` still reads the readable forms a person may type (`/m/<account>/<folder>`, `/search?q=`,
`/brief/topic/<label>`, `?key=&folder=&uid=`). Nothing goes into `history.state` but Back's own mark (`lib/backTrap.ts`).
The event stream invalidates queries (`app/realtime.ts`), `app/FolderRail.tsx` is the rail, `features/reader` the reader, `features/mailbox` holds the list, the mail frame
and filing, and `app/commands.ts` the one list of commands that the keys, the palette (⌘K) and the keyboard help all read. `features/compose` is the composer: `machine.ts` holds its rules with no DOM (`createComposing(env)`, what
`machine.test.ts` drives), `compose.ts` binds that to the page, and `Composer.tsx` shows the copy the machine publishes. `features/assistant` is today's brief, the lists the digest answers (`/brief/needs`, `tidy`, `topic/<label>`),
Commitments, Your week, the new-mail pill, and Ask and Do from the search box, `features/status` the sync pill, the account alert, the meeting reminder, the keyboard help and the first-run guide, and `features/settings` the connections and the Preferences tab, which reads and writes `/api/preferences` (`accounts.domain.preferences`, a `"preferences"` block in `mailmind.config.json`; the page's `lib/prefs.ts` holds them and brings in once what a browser kept under its old keys).
`features/discovery` is the Feed and the Paper Trail, the contact card and the Document Hub, `features/curation` the Screener, the drawer, a sender's cleanup and the filters, and `features/attention` the snooze menu. `lib/backTrap.ts` makes Back close the top layer (`useLayer`), and `features/mailbox/swipe.ts` is a phone's swipe. `tests/e2e/browser/test_details.py` holds what an audit found the app has to do, and ROADMAP says what is left. A modal makes everything outside it inert, and a click goes straight through that, so what must stay in reach over one carries `data-react-aria-top-layer`, as the floats do. The
search box is a combobox that acts on Enter, and the new-mail pill names only mail that landed outside the list on screen, since a list refreshes in place, and snoozed mail that woke out of sight (a notice is persistent if and only if it happened while the person wasn't looking; `lib/toast.tsx` states the rule). A value hooks read outside React is a `lib/store.ts` store, whose hook is `useValue`, since the compiler
doesn't take a method named `use` for a hook. The compiler also keeps what a call with no reactive input gave, so a component reads the time or storage in a child mounted when it's needed, or through a store.

## Types and limits from the server

`just check-web` (part of `just verify`) type-checks `web/src` with `tsc`, lints it with ESLint and runs its Vitest suites; it installs `web/node_modules` from the lockfile if they
are missing. First it writes the API's types and the page's limits afresh and fails if they differ from `web/src/lib/api/schema.gen.ts`
and `limits.gen.ts`, saying to run `just gen-api`. That takes a second or two: `tests/support/openapi_doc.py` writes the OpenAPI document of the
app the routers make (no data directory read, nothing sent, and out of the command line's reach), and openapi-typescript writes
`schema.gen.ts` from it. The same program writes `limits.gen.ts`, `LIMITS`, from `mailmind/http/page_limits.py`'s `PAGE_LIMITS`: each
limit the page holds a person to before it asks (what one message's files may come to, the rows one `/api/bulk/file` names, the copies one
`/api/snooze` names, the length of an instruction for a drafted reply, the shortest and longest Undo Send's window may be set to, the most the AI's daily limit may be set to), as the constant its use case enforces. A send's own window comes on its answer (`undo_seconds`), and what each preference is with nothing said is `PAGE_PREFERENCE_DEFAULTS`. They are a build's, as the types are, so nothing serves them
and nothing waits for them to load. Run it when a schema in `mailmind/http/schemas/`, a route or a limit changes, and review the diff, in a
commit of its own. `lib/api/types.ts` reads the types by path: `GetAnswers` and `PostAnswers`, each a route's JSON answer at 200 (a
file, a document or a stream has none), `PostBodies`, and `AnyOf`, a union read field by field for code that takes each of its shapes as
one, as a list's rows do. It also names each closed set the server takes (`SnoozePreset`, `DraftTone`, `RewriteOp`, `PlanVerb`,
`FilingVerb`, `RuleChange`, `BlockScope`, `RsvpDecision`, `ComposeMode`, `DocumentKind` and the like). The page never keeps its own copy of a closed set or
a limit the server owns: its words for a set are a `Record<Set, string>` in its menu's order (`lib/entries.ts` walks one with its keys'
type), so tsc fails when the server adds or drops a member, and a limit is `LIMITS`'s. A choice of the page's alone (the send-later
times, a page's size) stays the page's.

## ESLint

`web/eslint.config.js` covers `web/src` only: typescript-eslint, `react-hooks` and `jsx-a11y`, each finding an error. A disable sits on its
line with a reason. Four rules are our own:

- Only `features/mailbox/MailFrame.tsx` may write HTML into the DOM (`innerHTML`, `srcDoc`, `dangerouslySetInnerHTML` and their like).
- A feature imports another only through its `index.ts`, which holds what the others use (`lint/slices.js`). `app/` and `main.tsx` compose the features and may import any file. A barrel that grows can close a cycle, which the next rule stops: nothing at a module's top level may read another feature's binding.
- The features stand in one order, `ORDER` in `lint/slices.js`, and a feature imports only those before it, types included. A new feature has to be placed there. `settingsOpen` is in `lib/`, since features on both sides of Settings read it.
- `machine.ts`, `model.ts`, `sendGuard.ts`, `draftResume.ts`, `linkClean.ts`, `linkRisk.ts` and any `*.machine.ts` are pure: no DOM, no fetch, and only each other and types as imports. The machine gets its stores through `Env`.

Never disable `react-hooks/exhaustive-deps` or `rules-of-hooks`: the React Compiler quietly skips a component that does (`react-hooks/rule-suppression` refuses it). What an effect reads but doesn't run for goes through `useEffectEvent`. typescript-eslint needs a TypeScript with a JavaScript API and `tsc` is on 7, which has none, so it and a TypeScript 6 live in the `web/lint` workspace until it supports 7. openapi-typescript needs one too, and takes only a TypeScript 5, which npm can't give it beside that 6, so it and a TypeScript 5 live in the `web/gen-api` workspace.

## The frontend check

`just check-frontend` parses `web/assets/theme.js` with `node --check`, checks CSS brace balance, and rejects
an inline `<script>` in `web/index.html` or raw control bytes in the app's source (see Conventions).
