# shells/apple/: the MailMind app for iPhone, iPad and Mac

One SwiftUI app over the Rust core (`../../core`), with an iOS target (iPhone and iPad) and a macOS target that share
every source file. The core decides everything about mail; this shell only shows what it sends.

On the Mac the app runs the whole of MailMind in its own process: the core's host (`mm-host`, through `mm-ffi`'s
`CoreHost`) opens a data folder, starts the background work, and answers the page's routes with no port open. The
window calls those routes through `Core` and their generated Swift types. iPhone and iPad, and the Mac's step 0
measure modes, still show step 0: one message list over a fictional mailbox of 100,000 messages, made on first
launch in a folder of its own.

## Commands

From the repo root (`just apple` lists them; recipes run from here whatever the working directory):

```bash
just apple build        # the core as build/MailMindCore.xcframework, the Xcode project, then the iOS simulator and macOS apps
just apple test         # every area's tests on macOS: the list models', the client's, the mailbox's, the reader's, the
                        # composer's, Settings', the status's and the desk's (the assistant, curation, attention and the
                        # calendar); pictures in build/screens and build/screenshots
TEST_RUNNER_SCREENS=1 just apple test   # the same, and the screens tests draw the reader's and composer's views, light
                                        # and dark, into build/screens/*.png (read them after a change to a view)
TEST_RUNNER_MEADOW_PICTURES=DIR just apple test   # the same, and Meadow's frame and top bar drawn in each light,
                                                  # the rail open, narrower, in focus and with the drawer, the
                                                  # rail's places and the list's rows (rows-*.png), what floats
                                                  # over the window: the palette, an answer, a bulk preview, the
                                                  # popovers, the toasts and the keys (overlay-*.png), and the
                                                  # compose window, Settings, the first run, a sender's card and
                                                  # Help (compose-*, settings-*, first-run, contact-card, help-*),
                                                  # the welcome and Help in a window under its traffic lights
                                                  # (window-*), the side cards' facts and the segmented strips
                                                  # (blocks-*.png), and the reading pane's document (reader-*.png),
                                                  # into DIR; the brief, its top with mail and with none
                                                  # (brief-hero-*), Today & next with an invite answered, the gate,
                                                  # the paper trail and an answer into DIR/desk
just apple demo         # the Mac app on fictional mail: fake servers on loopback, a temp home, a keychain stand-in
just apple demo smoke   # the same until the inbox shows rows (exit 0), or not; `demo measure`: first rows and memory
just apple demo picture # the app draws its own window on fictional mail, light and dark: build/screenshots/window
                        # (no screen capture, so a table's rows and the sidebar's material come out blank)
just apple run          # the Mac app as a person runs it: MailMind's own data folder and the login Keychain
just apple gen-api      # after a route or schema changes: rewrites Sources/Client/API.gen.swift (commit it on its own)
just apple smoke        # the Mac app, then the iPhone app in a simulator: each jumps nine tenths down and waits for mail
just apple measure      # cold start, memory, main-thread time, crossing cost, sizes, a web view's memory: build/measure/results.txt
just apple budgets      # before a release: measure, then demo measure, each number judged against budgets.toml's [apple]
just gen-bindings       # after a change to the core's contract: rewrites Generated/ (commit that diff on its own)
just apple icon         # the Mac app's icon, drawn by Icon/icon.swift into Icon/Assets.xcassets (commit the images on their own)
just apple fonts        # Meadow's typefaces, joined from the page's by Fonts/fonts.py into Fonts/*.ttf (commit them on their own)
just apple release      # the Mac app universal, signed ad hoc, in a .dmg with its appcast: build/release (docs/releasing.md)
just apple release-check  # the release recipe end to end with a throwaway update key, then checks on what it made,
                          # and opens its app on fictional mail, on Apple silicon (the Intel half is never run)
```

Needs Xcode 26 with the iOS and macOS 26 SDKs, XcodeGen (`brew install xcodegen`), and the core's Rust toolchain
with the `aarch64-apple-ios`, `aarch64-apple-ios-sim` and `aarch64-apple-darwin` targets, and `x86_64-apple-darwin` for
a release (`core/rust-toolchain.toml` names them all). The release check runs on an Apple silicon Mac. iOS builds compile without a simulator runtime; running one needs a simulator or
a device (`just apple smoke ios` uses the newest iPhone simulator). The first build fetches Sparkle through Swift
Package Manager.

The Mac app runs from macOS 15, the iPhone and iPad app from iOS 26: `deploymentTarget` in `project.yml`, which the
core is built for too. A Swift or Objective-C API newer than that fails the build; use it behind
`if #available(macOS 26, *)` with a fallback that keeps Meadow's look. The tests run on the Mac they're built on, so a
test of such an API is marked `@available` too. Nothing checks the core's calls into the system that way: only CI's
`mac-floor` job, which opens the release check's .dmg on macOS 15 on Apple silicon, and a release waits for it.
MailMind is tested on Apple silicon only: a release's Intel half is built and checked to be there, never run.

The Mac app is `io.github.lanterno.mailmind`, version `MARKETING_VERSION` in `project.yml`;
[docs/releasing.md](../../docs/releasing.md) says how a release is cut and what the owner does once.

## What is where

| Path | What it is |
| --- | --- |
| `project.yml` | The Xcode project for XcodeGen. `MailMind.xcodeproj` is generated from it and never committed. |
| `Release/mac.yml` | What only the Mac target has, which `project.yml` includes: the icon, Sparkle (pinned), the update key and the switch for update checks, the hardened runtime. |
| `Release/` | Besides `mac.yml`: Sparkle's Info.plist keys, the entitlements, `release.sh` and `check.sh` (`just apple release` and `release-check`), `eddsa.swift` (an appcast's signatures checked as Sparkle will), `acknowledgements.py` and `licences/` (every licence the app carries), `dmg-background.swift` and `dmg.py` (the .dmg's window), and the release notes' template. |
| `Icon/` | `icon.swift`, which draws the Mac app's icon, and the asset catalog it renders into. iOS has no icon yet: compiling one needs an iOS simulator runtime installed. |
| `Fonts/` | Meadow's typefaces, Nunito Sans (variable, upright and italic), Shippori Mincho (600, 700) and Klee One (600), one TrueType file a face, which `fonts.py` joins from the page's per-script WOFF2 files (`web/assets/fonts`, whose `LICENSES` the acknowledgements carry). The app registers them for its own process as it starts (`MeadowFonts.register()`). |
| `Generated/` | The UniFFI bindings `just gen-bindings` writes. Never edit them by hand. |
| `Sources/main.swift` | Where the app starts: a run that isn't a person's keeps its preferences apart first (`Mac/Shared/RunPreferences`), then `MailMindApp`, the scenes and the menu bar's commands. |
| `Sources/ListModel.swift` | `HeldRows`, `ListModel`, `OncePerFrame` and `Relay`: the rows near the screen, the window asked for at most once a frame, and the core's batches handed to the main actor. |
| `Sources/Demo.swift` | Makes the fictional mailbox on first launch, then opens the session and the list off the main thread. |
| `Sources/InboxView.swift` | The inbox screen, the list in it, and how a row's date is shown. |
| `Sources/MessageTable+macOS.swift` | The Mac's list: an `NSTableView` with one row height, its rows and placeholders drawn with Core Text. |
| `Sources/MessageTable+iOS.swift` | The iPhone and iPad list: a `UICollectionView` over a fixed-row layout, its rows and placeholders. |
| `Sources/Measure.swift` | Measure mode, run only when `MAILMIND_MEASURE` is set. |
| `Sources/Acknowledgements.swift` | **MailMind › Acknowledgements**: the licences a release put in the app, in a window of their own. |
| `Sources/Updates.swift` | Sparkle's updater and **Check for Updates…** in the app menu (`UpdateCommands`), on the Mac only. |
| `Sources/Client/Core.swift` | `Core`: the host opened over a home, a route's GET or POST as its generated types, a refusal thrown as its `Problem`, the event stream as an `AsyncStream`. A request may carry a person's leave to go over the day's AI limit (`postOverLimit`, `send(…, overLimit: true)`), the one header the host lets a shell add. `CoreHome.ofThisProcess` picks the home. |
| `Sources/Client/APISupport.swift` | What the generated types lean on: `JSONValue`, the `GetRoute` and `PostRoute` protocols, `NotJSON`, a union's decoding. |
| `Sources/Client/API.gen.swift` | The routes' answers and bodies as Swift types, all inside `API`, each route as `API.Get.X` or `API.Post.X`. Written by `just apple gen-api`; never edit it. |
| `Sources/Window/` | The Mac app's window: `Root` (the window or step 0), `MailApp` (the process's one, `MailApp.shared`, so a window opened again from the Dock shows the core already open: the core, its data folder, where the window is, the list there; on the Mac the mailbox reads the sidebar, and `Waking` tells the core of a wake or a changed network), `MailList` (`Place`, `Selection`, the list a page at a time), `Reader` (the message chosen, its frame's URL), `MailWindow+macOS` (Meadow's frame around the mailbox's rail and list and `Mac/Reader`'s pane), `HomeMeasure` (`MAILMIND_MEASURE=home`, and `MAILMIND_PICTURE`'s pictures). |
| `Sources/Mac/Settings/` | Settings (⌘,), drawn as Meadow's dialog (`SettingsLayout`: its places down the left, each place's settings in side cards as `SettingLine`s and `SettingSwitch`es): Accounts (each connection, its health, a sync error in words with what to do, Test, Trust It, + with the page's presets, Edit, Remove), General (with the light, Automatic, Day, Dusk or Night, and `OpenAtLogin`, through macOS's login items), Notifications and AI; the Claude key and the Bridge's password (`KeptSecretView`, `SecretPost`), and the first run's `Welcome`, in the brief's manner, whose account sheet hangs on the window (`FirstAccountSheet`) so it outlives the welcome. An account added or removed reads the window's rail and list again at once (`MailApp.accountsChanged`). |
| `Sources/Mac/Status/` | `AppStatus` (what the status, Settings and the first run show, heard on an event stream of its own), the status pill and its popover (with background work that keeps failing), the account alert, the meeting reminder, new mail's notifications and Dock badge (`NewMailAlerts`; one message named by its sender and subject), what a click on a notification opens (`NotificationOpener`: the message, or a meeting's invite, the mail window opened again if it was closed), Help, the guide and the keyboard's legend (`KeysLegend`), Help › Copy Diagnostics and Report a Problem (`Diagnostics`: no address, subject, name or secret in what it copies), the window when MailMind didn't open (`LaunchFailure`, which offers Start Fresh only where a new copy fixes what went wrong and Try Again where the core's words say to open MailMind again, and `StartFresh`, which moves the database into `backups/`), and `StatusHooks`, the one modifier the window applies. |
| `Sources/Mac/Mailbox/` | The Mac window's mailbox: `Mailbox` (the rail's accounts, folders, labels, views and counts, each place's list, the rows chosen), its verbs (`Verbs`) and ways about (`Going`), the lists' reads (`Lists`), the rail (`Sidebar`; its places in order, open and folded, `Rail`; the accounts' button and menu, `RailMenu`), the list's rows (`RowLook`: a row's measures in each density, its words, chips and day heads, the brief's line; `RowCell`: the rows drawn with Core Text; `Table`: the native table, its heights, hover and checks), what stands around them (`ListAround`: the offline note, the bulk bar, the empty list), its column (`ListColumn`), the lanes, the paper trail's page and the Document Hub (`Hub`; a month's sums, `Ledger`), Move To and the palette (`Sheets`), a folder made, renamed or deleted through the server's folder routes (`FolderChanges`: Move To's New Folder…, the rail's Rename Folder… and Delete Folder… on the folders the server says are `changeable`, and their dialog, which names a folder by its `own_name`), a sender's card, and the menu bar's commands (`Commands`). Rows dragged from the table onto the rail's folders move there (`Mailbox.drop`). Its hooks (`asks`, `plans`, `translates`, `keepsLiteral`, `composeTo`, `opensWindow`, `more`, `keys`, `rowMenu`, `snoozes`) are where the other areas join in, each once a window through `join`: the reader's pane for writing and a message's own window, `StatusHooks` for help, Settings and the sync, Meadow's frame for the shell's keys, `Desk` for the rest. The process has one mailbox and a window may close, so the window lets go of them as it closes (`leave`, from `WindowAreas`): the window opened again from the Dock joins afresh, and a window still open joins again (`joinRound`). |
| `Sources/Mac/Shared/` | What the areas share: `PreviewFiles`, Quick Look's copies of attachments (this user's alone, removed as their preview closes, swept at launch and quit), and `OverLimit`: the day's AI limit as a refusal says it, `OverLimitRoutes.post(_:_:asking:)`, which an AI call a person started goes through, and `OverLimitQuestion`, the alert that asks; `Waking`, which tells the core of a wake from sleep and of a network that really changed (`CoreHost.woke`); and `Quitting`, which saves each open composer's draft, sends what is in its Undo window before the app quits and asks about what waits for later; and `WindowAreas`, what the areas add around the mail window (the overlays, `StatusHooks`, `DeskSurfaces`), which lets go of the mailbox's hooks as the window closes; and `ReadOnlyWords`, read only's note and the one sentence every place says when the server turns a change down because read only is on (a 409 with `read_only`, asked before any other 409's words); and `RunPreferences`, which keeps a demo's, a test's or a measure run's preferences apart from the person's (`PreferencesFile`: the run's own, in its home); and `SingleKey`, the key a single-key press stands for, which every single key is matched by: what it types where that is ASCII, else what the key types in the ASCII-capable layout (Arabic's ي is d), so the keys work under any layout and a field still gets what was typed, and `BeforeTheWindow`, where a window's catcher of single keys stands: last before the window, whose own `keyDown` would hand e and d to Edit's Emoji & Symbols and Start Dictation. |
| `Sources/Mac/Reader/` | The reader, as the page's `features/reader` and `mailbox/MailFrame` show it and Meadow's document lays it out: `ReaderPane` (a message or a conversation under Meadow's pane bar, the empty pane, the brief beside the Inbox, and what the menu bar's Reply acts on), `Document` (the chips and subject, one column or two from 1100 points with the aside held as it scrolls, the measure), `Aside` (the summary and the side cards), `Message` (a message's head, its trust line, an earlier message a line), `Reading` (the readers and the conversation's model), `ReaderViews` (a message's content: images on request, kept for the session in `ShownImages`, the notes above a body, quick replies), `InlineReply` (the reply box under a message, closed and open, over the composer's model), `UpNext` (the next message, and File, Delete or Keep & next), `MailFrame` (the body's web view and its walls), `MailLook` (the frame's prose and the plain-text quote fold), `FrameFont` (the reading face, written into the document as a data: URL its policy allows), `LinkRisk` and `LinkOpener` (a link's check, then the browser or the composer), `Attachments` (the files' cards, Quick Look and a save panel), `ReadingSettings` (Aa), `Printing` (File › Print: the message's document asked of the core again, off the window and with the frame's walls, in the Day light under a head of its own, with the images a person asked to see, `ShownImages`), `ReaderFind` (Find in Message's bar, over each frame the reader shows), `ReaderCommands` (Print… and Find in Message… in the menu bar, for the reader in front), `MessageWindow` (a message in a window of its own, as a double-click opens it, speaking the core the mail window opened, `MailApp.shared`'s; the Message menu and the reader's single keys act there on its message alone, `MessageWindowVerbs`, and the window closes once the message has left), and `ReaderCards`, the slot where the desk puts its cards: the summary and what a message proposes in the aside, an invite into each message. |
| `Sources/Mac/Compose/` | Writing, as the page's `features/compose`: `Composer` (one message's model: its saves, the send guard, its fixes), `Composing` (the composers, Undo send's float, a reply drafted ahead), `ComposeWindow` (the composer's window and its parts), `ComposeLook` (Meadow's compose dialog: its paper, a field's row, the popovers that hold a field, the message's text), `ComposeEditors` (the body and the recipients' AppKit fields), `ComposeCommands` (File's New Message, and Reply, Reply All, Forward and Edit Draft at the top of the mailbox's Message menu), `SendGuard`, `LinkClean` and `ComposeModel` (the rules the page's tests hold), `Outbox`, `QuoteFrame` (the Markdown preview), `Mailto` (a mailto: link's message, and the app's delegate that takes the links macOS hands it and asks `Quitting` before the app quits), and `Routes`, the seam the tests answer in place of the core. |
| `Sources/Mac/Assistant/` | The desk: `Desk` and `DeskSurfaces` (`Desk+Window`: the models over the window's core, their sheets, what the rail shows of the desk (`rail`), the cards in the reader's slot, the Message menu's items and the Assistant menu, and the mailbox's hooks), `DeskPlace` (the brief in the reading pane, an answer in the list's place, a Screener across both), today's brief (`Brief`, `BriefModel`, `BriefWords`; its cards for what needs you, worth a read and what can wait, `BriefCards`; the column beside it, `BriefSide`), Ask, a bulk action said in words (`Plan`), the palette's `?` and `!` (`PaletteModes`: an answer and a bulk action's preview where the palette lists), the prose search (`Prose`), the pill for mail that landed away from the list on screen and its line on what came in since (`SinceLooked`), a summary and what a message proposes (`Summary`, `Suggestions`), tasks, Your Week and commitments (`Panels`), the notices (`Notices`), the cards' look, an empty place and a field (`CardStyle`), and `RouteCaller`, the seam the tests answer in place of the core. |
| `Sources/Mac/Meadow/` | Meadow's look (#44), as the mockup draws it: `Palette` and `Look` (each light's colours, read from the environment), `Fonts`, `Icons` and `SVGPath`, `Sky`, `Blocks` (the panel, the buttons, the keycap, the chips, the tabs, the focus ring), `Segmented` (the `.seg` strip), `Fact` (a side card's `.fact` line), `Avatar` and `Hue` (a name's colour, as the page's `hue()`), `AccountPlaces` (which of the palette's colours each account wears: the place the core keeps with each account, the digest's `colours`, so the page colours it the same and removing one leaves the others' as they were; kept in the Mac's preferences too for what reads them before the digest), `Light` (the light a person chose and its round, Automatic's from Dark Mode and the hour, `meadowLit()` at each window's root), `Shell` (where the rail, list and pane stand, the splitter's limits, focus reading, the drawer; the pane's bar's folds), `WindowChrome` (no title or toolbar, the traffic lights on the top bar), `TopBar` (the search, the status, the outbox, the light, Compose), `Frame` (the window's panels, the splitter, the drawer, `MeadowCommands`; the rail's content reads `\.meadowRailOpen`), `Heads` (the list's head and the pane's bar), `Overlays` (what floats over the window: the dialog on its scrim and the palette's parts, the popover, `MeadowPopoverAnchor`, a child panel every popover opens in, and its menu, the toast) and `WindowOverlays` (the one modifier that puts the palette, Move To, the folder dialog, Save View, the keys, the snooze chooser and a time of one's own, a sender's card and the toasts over the window, and hands the list its keyboard back as a dialog goes). |
| `Sources/Mac/Curation/` | Curate's sheet (senders, inboxes, rules, filters, activity) and its model, and each account's Screener, the gate across the window. |
| `Sources/Mac/Attention/` | Snooze (its presets and a time of a person's own), mute, Reply Later and follow-ups: the model, the commands for the mailbox's keys, palette and row menu, and the menu items. |
| `Sources/Mac/Calendar/` | An invite's RSVP and its Undo, adding it to the calendar, and how an event's time is said (`Upcoming`). |
| `TestKit/` | `MailMindTestKit`, compiled into `MailMindTests` and kept free of XCTest so the in-app driver can reuse it: keys pressed as a person presses them (`RealKeys`, posted into the app's queue, a key code read under US, Arabic, Swiss French or German from that layout's own table, never changing the Mac's), the mouse (`RealMouse`: clicks, drags, the wheel, the pointer's moves), finding things by role and label within a region (`Find`), `keyboardIsSomewhere(_:)` and the menu bar's items as AppKit validates them (`menuItem(_:)`, `chooseMenuItem(_:)`). It throws `KitError`s rather than asserting. |
| `Generator/swift_api.py` | Writes `API.gen.swift` from the OpenAPI document `tests/support/openapi_doc.py` makes. `just apple check-api`, in `just verify`, fails when the two differ. |
| `Measure/` | `measure.sh` and the probes it compiles (the crossing cost, a web view's memory), `smoke.sh`, which CI runs, and `demo_home.py`, which `just apple demo` runs (and its `smoke`, `measure` and `picture`), and the release check on each half of the release's app (`--arch`). |
| `build/` | The XCFramework, derived data, measurements and releases. Ignored by git. |

## Rules

- **The shell decides nothing about mail.** Order, which rows to keep, a sender's name, a snippet's length: the core
  sends them. The shell formats a date and draws. Anything two platforms would have to agree on goes in the core.
- **The core's thread is never the main thread.** Observer calls arrive on the core's thread and hop to the main
  actor in order (`DispatchQueue.main.async`). Opening the database and ordering the list run in a detached task.
  `Core` makes every call to the host on a queue of its own, and a request blocks that queue, never the main thread.
- **A list is a native table, drawn with Core Text.** The table asks only for the rows on screen, and a batch redraws
  only the rows it changed. SwiftUI's `List` spent up to 20 ms a frame deep in the list and kept memory for every row
  it had shown. Step 0's list keeps one row height, so row n is at n times it. The window's rows take the height their
  content needs (a summary's one line or two, chips, a day's head), worked out by arithmetic from a row's shape, kept
  a byte a row, and its summary's width on one line, set once as its page comes. A new width works the lines out
  again from those widths, so the table never lays out text to measure a row as it scrolls or as the splitter moves.
- **The shell holds no list of its own.** It keeps only the rows in the range the latest batch says to hold, and drops
  the rest when a batch arrives.
- **Generated files are never edited by hand.** Change the core, run `just gen-bindings`, commit the diff on its own.
  The XCFramework is rebuilt by every `just apple build`, so bindings and library always come from the same core.
  A route or schema change runs `just apple gen-api` the same way.
- **Never run the Mac app on a person's data by accident.** With no `MAILMIND_HOME` it opens MailMind's own folder in
  Application Support and the login Keychain: that is `just apple run`. A demo, a measure run or a test names a
  temporary home with `MAILMIND_HOME`, a key with `MAILMIND_DB_KEY` and the keychain's stand-in with
  `MAILMIND_TEST_KEYCHAIN` (as `just apple demo` does), and one that names no home opens nothing. Step 0's mailbox
  lives in a folder of its own, never inside the app's.
- **A run that isn't a person's never touches their preferences,** however it is started. Every build is
  `io.github.lanterno.mailmind`, so macOS would hand a demo, a measure run or one on a home of its own the person's
  defaults. `Sources/main.swift` calls `RunPreferences.keepApart()` before anything reads one: the run's user defaults
  are volatile, so nothing of the person's MailMind is read or written (its settings, read only, the window frames,
  a window's saved state), the Mac's own settings are carried in, and the run's own are kept in its home,
  `Library/Preferences/io.github.lanterno.mailmind.plist`, which `demo_home.py` checks after each run. Read a run's
  preferences there, never with `defaults` on the app's domain, which is the person's. A new preference goes through
  `UserDefaults.standard` (or a suite a test names), never a file of its own. `CFFIXED_USER_HOME` moves the home folder
  on macOS 26, but not the defaults. Nor does such a run reach the person's Mac otherwise: it never asks to notify or
  posts a notification (`SystemNotifier.forThisProcess` hands it `Silent`, which keeps what it would have posted for
  a test), and its Open at login is off and says why, never asking `SMAppService` (`OpenAtLogin`).
- **The window speaks the page's routes,** in-process, through the same perimeter as the served app, and keeps no
  closed set or limit the server owns: those come with the generated types.
- **A mail body's frame has walls of its own.** The mail's content world runs no JavaScript; the web view keeps
  nothing on disk, follows no link or redirect, and its scheme (`mailmind-frame:`) answers only
  `/api/message/body.html` through the core. The document's own sandbox policy stays the boundary, as in the page,
  and under it WebKit runs no event listener or timer in any of the document's worlds, the app's own too, so the app
  puts no script in the frame. Its Swift asks the document short questions from outside (`FrameAsk`), in an isolated
  world of its own (`mailmind-frame`) that the mail's page can't see or reach: how tall the content is (as it loads,
  for a few seconds after, at a new width, after a click or a key in it, as Find in Message finds), the link at a
  click (a left click that didn't drag, a middle click, Return on a focused link), the href under the pointer (asked
  at most every few frames, through a tracking area of the frame's own, and cleared as the pointer leaves), where
  Find's match and the focus Tab moved stand, and whether the focus is in a field. What comes back is the mail's
  data, read only as `FrameMessage` reads it: a number for a height, four finite numbers for where something stands,
  an href of at most 8192 characters. The shell checks a link with `LinkRisk` and opens it in the default browser,
  never in the frame. The frame is as tall as its content and keeps no scroll of its own: each measure puts the
  document's scroll back to its top (a fold opened or a match shown before the frame grew scrolls it inside itself),
  and the reader scrolls to Find's match and Tab's focus. A key the frame's document gives back, as WebKit does one
  it had nothing to scroll for (Space, the arrows, Home, End), pages or scrolls the reader as from the list, and so
  do Page Up and Down, which WebKit keeps; while the focus is in a field, its keys are the mail's and move nothing.
  The frame's look, the plain-text quote fold and the words on the core's quote toggle are written into the
  document's bytes before WebKit reads them, never by a script; a printed copy's look opens every fold.
  `MailFrameTests` load the document under the core's own headers, so what the sandbox stops shows there.
- **Links, images and files leave the frame only on a person's say.** A link opens after `LinkRisk`'s check (a
  warning asks first); remote images load only when asked, as the page's do; an attachment is saved through a save
  panel or shown in Quick Look, never opened by the app.
- **Fixtures are fictional.** Every address is at example.test, and the fixture key guards only fictional mail.
- **Only a release checks for updates.** `MAILMIND_CHECKS_FOR_UPDATES` stays `NO` in `Release/mac.yml`; only
  `just apple release` turns it on. A debug build, `just apple build`'s, a test or a demo never asks the network, and
  a release's copy starts Sparkle only in a person's run (`CoreHome.isAPersonsRun`): the release check opens it on a
  demo's home, which has no person to ask.
- **The update key's private half is the owner's alone,** in their login Keychain. Only its public half is in the
  repo (`SPARKLE_PUBLIC_KEY`). A test signs with a throwaway key made for it, never the Keychain's.
- **A release is published by the owner,** with the `gh release create` command `just apple release` prints.
- **A secret a person types goes straight to its route** (`SecretPost`), and its field is emptied once it is sent to
  be kept, or its form is closed. Settings shows only whether one is kept and where the one in use comes from, never
  the secret, and nothing that holds one describes it.
- **An AI call a person started asks at the day's limit,** as the page's do: it goes through `post(_:_:asking:)` with
  the window's question (`CoreLink`'s, `ComposeHost`'s or `Wire`'s `asksOverLimit`), and on yes the same call goes
  again once with the leave. A call the app makes on its own (the brief written on opening, a sentence searched for,
  a quick reply's draft, a reply written ahead) passes no question, and says the limit is reached.
- **Comments say why,** in short plain sentences.
