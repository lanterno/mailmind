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
- **Updates.** Each copy of the app reads `https://github.com/lanterno/mailmind/releases/latest/download/appcast.xml`,
  the appcast on the latest release. The appcast names the .dmg and carries its EdDSA (Ed25519) signature, made with
  the owner's private key; the app carries the public key and refuses an update whose signature doesn't verify, before
  it unpacks it (`SUVerifyUpdateBeforeExtraction`). Sparkle asks the person before its first automatic check, then
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
2. **Build it.** `just apple release`. It builds and signs everything, writes the appcast with Sparkle's
   `generate_appcast`, which signs the .dmg with the key in the login Keychain (macOS asks once to let it use the
   key: **Always Allow** saves asking again), and checks that signature against the public key inside the app. It
   prints the .dmg's size and a `gh release create` command, and says so if the tree has changes not committed.
3. **Try it.** Open the .dmg, drag MailMind to a folder of your own, open it, and look around.
4. **The notes.** Write what changed at the top of `shells/apple/build/release/notes.md`. The rest of the template
   (`shells/apple/Release/notes.md`) says how to install and how to open the app the first time.
5. **Publish.** Run the printed command. It tags the commit the build came from, makes the release the latest, and
   uploads the .dmg and `appcast.xml`. The recipe never publishes by itself.
6. **Check the update.** Open an older copy and choose **MailMind › Check for Updates…**: it should offer the new
   one.

`just apple release-check` runs the whole recipe with a throwaway key made in a temporary folder, never the login
Keychain, and checks the result: the .dmg mounts with the app and an Applications link, the app is universal, its
signature verifies, its Info.plist names the identifier, the version, the key and the feed, a program signed as the
app is loads its Sparkle, and the appcast's signature verifies against the app's key and no other. Run it after
changing anything in `shells/apple/Release`.

## What people see the first time

The app is signed ad hoc, not with a Developer ID, and not notarized, so Gatekeeper won't open it until the person
says so, once. On macOS 15 and later Control-click › Open no longer does it: they open the app, click **Done** on
*"MailMind" Not Opened*, then in **System Settings › Privacy & Security** click **Open Anyway** beside *"MailMind" was
blocked to protect your Mac*, and confirm with their password. The release notes template and the README say this.

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
