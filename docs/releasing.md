# Releasing the Mac app

The Mac app (`shells/apple`) ships as a .dmg on a GitHub release of this repo, and updates itself through
[Sparkle](https://sparkle-project.org). This page says how a release is made, what the owner does once, and what waits
on a Developer ID ([#42](https://github.com/lanterno/mailmind/issues/42)).

## How it fits together

- **The app's identity.** `io.github.lanterno.mailmind`, named MailMind. Its version is `MARKETING_VERSION` in
  `shells/apple/project.yml`, the one place it is set; the build number is the same string, so it rises with every
  release, as Sparkle's comparison needs.
- **The build.** `just apple release` (`shells/apple/Release/release.sh`) builds the core for Apple silicon and Intel
  and joins them into the XCFramework's Mac slice, builds the app in Release for both, signs it ad hoc
  (`codesign -s -`) under the hardened runtime, inside out (Sparkle's helpers, Sparkle, then the app), and puts it
  in `MailMind-<version>.dmg` beside a link to /Applications. Everything lands in `shells/apple/build/release`,
  which git ignores.
- **Which Macs.** macOS 15 or later, Apple silicon or Intel. The floor is the `macOS` of `deploymentTarget` in
  `shells/apple/project.yml`; the core is built for the same (the recipe reads it there), the app's Info.plist says
  it (`LSMinimumSystemVersion`), and the appcast copies it from there, so Sparkle offers an update only to a Mac that
  can run it. The build fails on a Swift or Objective-C API newer than macOS 15, and such an API goes behind
  `if #available` with a fallback in Meadow's look. That is all a build shows. The core's calls into the system (its
  Rust and the C it compiles in, which dyld binds as the app starts, with no fallback) and how macOS 15 draws the
  window are covered only by opening the app on macOS 15: CI's `mac-floor` job opens the release check's .dmg there,
  on Apple silicon (`macos-15`). The tests and the rest of CI run on macOS 26.
- **Tested on Apple silicon only.** A release holds an Intel half, built, signed and checked to be universal like the
  rest, but nothing runs it: neither CI nor the release check opens the app on an Intel Mac or under Rosetta.
- **The .dmg's window.** MailMind on the left, an arrow, Applications on the right, over a picture that says to drag
  one onto the other and how to open the app the first time. `Release/dmg-background.swift` draws the picture, and
  `Release/dmg.py` has [dmgbuild](https://github.com/dmgbuild/dmgbuild) (pyproject.toml's `release` group) write the
  layout into the image, so nothing drives Finder. Finder draws the icons' names black or white as the person's
  appearance is, so the band under them is a blue both read on.
- **The acknowledgements.** The licences of what the app is built from ask for their notices to go with every copy.
  `Release/acknowledgements.py` collects them as the release is built: MailMind's, Sparkle's (with bsdiff's), the
  SIL Open Font License of each typeface the app carries (Nunito Sans, Shippori Mincho and Klee One, with their
  copyrights, from `web/assets/fonts/LICENSES`), and every licence file of every crate the core links for either
  architecture, the C sources they compile in among them (SQLCipher and libtomcrypt, through `sqlcipher-src`). A
  crate that ships no licence file gets its licence's standard text from `Release/licences`, and the release stops
  when there is none, rather than ship the crate unacknowledged. They go in the app as `Acknowledgements.txt`, which
  **MailMind › Acknowledgements** shows.
- **Updates.** Each copy of the app reads `https://github.com/lanterno/mailmind/releases/latest/download/appcast.xml`,
  the appcast on the latest release. The appcast names the .dmg and carries its EdDSA (Ed25519) signature, made with
  the owner's private key; the app carries the public key and refuses an update whose signature doesn't verify, before
  it unpacks it (`SUVerifyUpdateBeforeExtraction`). The appcast itself is signed too, by the same key
  (`SURequireSignedFeed`), so whoever could change the feed can't change what it says either: which version is
  newest, what macOS it needs, where its notes are. Sparkle asks the person before its first automatic check, then
  checks once a day if they agree, and **MailMind › Check for Updates…** checks at once.
- **Only a release checks.** `MAILMIND_CHECKS_FOR_UPDATES` is `NO` in `shells/apple/Release/mac.yml` and only the
  release recipe turns it on, so a debug build, `just apple build`'s, a test and a demo never ask the network, nor
  does a build whose key is the placeholder (`Sources/Updates.swift`).
- **The settings.** `Release/mac.yml` holds the Mac app's packaging (the icon, Sparkle, the update key, the hardened
  runtime) and `project.yml` includes it; `Release/Info-macOS.plist` holds Sparkle's Info.plist keys and
  `Release/MailMind.entitlements` the app's entitlements.

## Once: the update-signing key

Every update must be signed with one EdDSA key pair. The owner makes it once, on their own Mac, and keeps the private
half in their login Keychain; it never goes in the repo, a file, CI or anyone else's hands.

1. Build once, so Swift Package Manager fetches Sparkle and its tools: `just apple build`.
2. Make the key, under its own Keychain account:

   ```bash
   shells/apple/build/DerivedData/SourcePackages/artifacts/sparkle/Sparkle/bin/generate_keys --account mailmind
   ```

   macOS may ask to let `generate_keys` save to the login Keychain: allow it. It prints the public key.
3. Put the public key in `shells/apple/Release/mac.yml`, as `SPARKLE_PUBLIC_KEY`, in place of the placeholder, and
   commit it. It is a public key: committing it is safe. Until then `just apple release` stops at once, because a copy
   built without it could never be updated.
4. Back up the private key somewhere safe and offline, such as a password manager:

   ```bash
   shells/apple/build/DerivedData/SourcePackages/artifacts/sparkle/Sparkle/bin/generate_keys --account mailmind -x mailmind-update-key
   ```

   then move that file into the password manager and delete it. Lose the key and the copies people have can never be
   offered another update: each person would have to download the next version by hand.
   `generate_keys --account mailmind -f FILE` puts a backed-up key into a new Mac's Keychain.

## Each release

1. **The version.** Raise `MARKETING_VERSION` in `shells/apple/project.yml` (0.1.0, 0.2.0, ...), and commit it.
   A version lower than or equal to the last release's is never offered as an update.
2. **CI on that commit.** The clients workflow's run on it is green, its `mac-floor` jobs among them: they are the
   only run of the app on the macOS it says it needs, so a release waits for them.
3. **Its budgets.** `just apple budgets`, on a quiet Mac: the shell's measures, then the demo home's, each judged
   against `budgets.toml`'s `[apple]`. A number at or under its limit passes. A wall time over its limit by no more
   than its tolerance (10% unless the entry says otherwise) lets the release go, and an issue is opened for that
   budget. Past the tolerance, or with a number not measured, it exits 1 and the release waits until the number is
   back or a commit raises the limit and says why. Sizes and memory have no tolerance.
4. **Build it.** `just apple release`. It builds and signs everything, writes the appcast with Sparkle's
   `generate_appcast`, which signs the .dmg with the key in the login Keychain (macOS asks once to let it use the
   key: **Always Allow** saves asking again), and checks that signature against the public key inside the app. It
   prints the .dmg's size and a `gh release create` command, and says so if the tree has changes not committed.
5. **Try it.** Open the .dmg, drag MailMind to a folder of your own, open it, and look around.
6. **The notes.** The template (`shells/apple/Release/notes.md`) opens with one release's news under that release's
   own version. Before each release's build, that news is this release's: write it over the last one's with the
   version's commit (step 1), so the build copies it into `shells/apple/build/release/notes.md`, where it can still be
   touched up before publishing. The rest of the template says how to
   install and how to open the app the first time, and names the .dmg by `{{version}}`, which the build fills in.
7. **Publish.** Run the printed command. It tags the commit the build came from, makes the release the latest, and
   uploads the .dmg and `appcast.xml`. The recipe never publishes by itself.
8. **Check the update.** Open an older copy and choose **MailMind › Check for Updates…**: it should offer the new
   one.

`just apple release-check` runs the whole recipe with a throwaway key made in a temporary folder, never the login
Keychain, and checks the result: the .dmg mounts with the app and an Applications link, the app is universal, its
signature verifies, its Info.plist names the identifier, the version, the key, the feed and the lowest macOS, both its
halves are built for that macOS, a program signed as the app is loads its Sparkle, the app carries the
acknowledgements, the .dmg's window is laid out, and the .dmg's signature and the appcast's own both verify against the
app's key and no other, a feed changed after signing doesn't, and the appcast names the same lowest macOS. Then
the app in the .dmg opens on the demo's fictional mail (`Measure/demo_home.py`: fake servers on loopback, a home of its
own and a keychain stand-in, where a release's copy starts no update check and keeps its preferences apart from the
person's MailMind, in that home) until its inbox shows rows, on Apple silicon: the check runs on an Apple silicon Mac,
and never opens the Intel half.
Run it after changing anything in `shells/apple/Release`; CI runs it on every change to the clients, even when an
earlier step failed. `MAILMIND_RELEASE_CHECK_KEEP=DIR` keeps a copy of the .dmg it made: CI keeps it as an artifact
for a few days, and `mac-floor` opens it on macOS 15 the same way, each half natively on a Mac of its kind.

## Symbols, for crash reports

The core's release builds keep line tables (`debug = "line-tables-only"` in `core/Cargo.toml`), and Xcode's dsymutil
gathers them from the core's library into `MailMind.app.dSYM` as it links; none of it goes in the app, whose binary is
stripped. `just apple release` stops if the dSYM holds none of the core's, and keeps it, with Sparkle's, in
`shells/apple/build/release/symbols/<version>/`, which git ignores and the next release leaves be. A crash report from a
copy of that version reads against that folder: macOS's own reports name each frame's address, and
`xcrun atos -o <version>/MailMind.app.dSYM/Contents/Resources/DWARF/MailMind -arch arm64 -l <load address> <address>`
turns one into a function, file and line. Each dSYM matches only the build it came from, so keep the folder of every
version people may still run. The release check holds that the dSYM is kept, matches the app's binary and holds the
core's line tables.

## What people see the first time

The app is signed ad hoc, not with a Developer ID, and not notarized, so Gatekeeper won't open it until the person
says so, once. Since macOS 15, the oldest the app runs on, Control-click › Open no longer does it: they open the app,
click **Done** on *"MailMind" Not Opened*, then in **System Settings › Privacy & Security** click **Open Anyway**
beside *"MailMind" was blocked to protect your Mac*, and confirm with their password. The release notes template and
the README say this.

Updates Sparkle installs are downloaded by the app itself, which marks nothing as quarantined, so they should open
without asking again; check this on the first real update.

## What waits on a Developer ID (#42)

With a paid Apple Developer account:

- **Sign with the Developer ID** in place of `-` in `release.sh`'s `sign`, with `--timestamp`.
- **Notarize and staple** the .dmg (`xcrun notarytool submit --wait`, then `xcrun stapler staple`), so it opens with no
  Open Anyway. The hardened runtime the app already has is what notarization requires.
- **Drop the entitlement** in `Release/MailMind.entitlements`. Ad-hoc code has no Team ID, so library validation
  would refuse to load Sparkle.framework and the app would abort at launch; with a Developer ID the app and Sparkle
  share one, and validation can stay on.
- **A stable identity.** To macOS each ad-hoc build is a different program, so anything that trusts the app by its
  signature asks again after an update. The app's Keychain items are added through `/usr/bin/security`, which they
  trust in place of the app, so those don't; but a privacy permission granted to the app may be asked for again.
- **Key rotation.** Sparkle accepts an update signed with a new EdDSA key only when the Developer ID signature matches,
  so until then the one key must not change.
