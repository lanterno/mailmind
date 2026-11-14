#!/usr/bin/env bash
# The Mac app as something to hand to someone, as `just apple release` makes it (docs/releasing.md): the core for
# Apple silicon and Intel, the app in its Release configuration with update checks on, signed ad hoc under the
# hardened runtime, a .dmg with the app and an Applications link, and the appcast Sparkle reads, signed with the
# owner's update key. It prints the `gh release create` command for the owner to run, and never publishes.
#
# Everything goes to build/release (ignored by git). The release check (check.sh) points it elsewhere and signs with a
# throwaway key through three variables, which nothing else sets:
#   MAILMIND_RELEASE_OUT          the folder for the .dmg, the appcast and the notes
#   MAILMIND_RELEASE_PUBLIC_KEY   the public key to build in, in place of Release/mac.yml's
#   MAILMIND_RELEASE_KEY_FILE     a private key file to sign with, in place of the owner's login Keychain
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
core="$(cd "$here/../.." && pwd)/core"
out="${MAILMIND_RELEASE_OUT:-$here/build/release}"
# Kept between runs, so a release rebuilds only what changed. Never `just apple build`'s, which checks for no updates.
derived="$here/build/release-DerivedData"
repo="lanterno/mailmind"

stop() {
    echo "release: $*" >&2
    exit 1
}

# The public key first, before anything slow: a build with the placeholder could never be updated.
key="${MAILMIND_RELEASE_PUBLIC_KEY:-$(sed -n 's/^ *SPARKLE_PUBLIC_KEY: *"\(.*\)"$/\1/p' "$here/Release/mac.yml")}"
if [[ "$(printf %s "$key" | base64 -d 2>/dev/null | wc -c | tr -d ' ')" != 32 ]]; then
    stop "the update-signing public key in shells/apple/Release/mac.yml (SPARKLE_PUBLIC_KEY) is still the placeholder.
The owner makes the key once with Sparkle's generate_keys, puts its public half there and commits it:
docs/releasing.md, \"Once: the update-signing key\". Copies built without it could never be updated."
fi

for tool in xcodegen xcodebuild hdiutil codesign lipo; do
    command -v "$tool" >/dev/null || stop "$tool is missing (shells/apple/CLAUDE.md says what the build needs)"
done
# rustup's env file, where rustup put one. Sourcing a missing file ends bash at once, `|| true` or not.
if [ -f "$HOME/.cargo/env" ]; then . "$HOME/.cargo/env"; fi

# Only what an earlier run left, never the folder itself: MAILMIND_RELEASE_OUT may name one that holds more.
mkdir -p "$out"
/bin/rm -rf "$out/MailMind.app" "$out/core" "$out/dmg" "$out/updates" "$out/appcast.xml" "$out/notes.md" \
    "$out"/MailMind-*.dmg

echo "## the core, for Apple silicon and Intel"
# The same build as the xcframework recipe's, with the Mac's slice for both architectures joined by lipo:
# an XCFramework holds one library for each platform.
export IPHONEOS_DEPLOYMENT_TARGET=26.0 MACOSX_DEPLOYMENT_TARGET=26.0
(
    cd "$core"
    for target in aarch64-apple-ios aarch64-apple-ios-sim aarch64-apple-darwin x86_64-apple-darwin; do
        cargo rustc -q --release -p mm-ffi --lib --crate-type staticlib --target "$target"
    done
)
lib() { echo "$core/target/$1/release/libmailmind_core.a"; }
mkdir -p "$out/core/headers"
lipo -create "$(lib aarch64-apple-darwin)" "$(lib x86_64-apple-darwin)" -output "$out/core/libmailmind_core.a"
cp "$here/Generated/MailMindCoreFFI.h" "$out/core/headers/"
cp "$here/Generated/MailMindCoreFFI.modulemap" "$out/core/headers/module.modulemap"
# Where the project looks for it. `just apple build` makes it again for itself.
/bin/rm -rf "$here/build/MailMindCore.xcframework"
xcodebuild -create-xcframework \
    -library "$(lib aarch64-apple-ios)" -headers "$out/core/headers" \
    -library "$(lib aarch64-apple-ios-sim)" -headers "$out/core/headers" \
    -library "$out/core/libmailmind_core.a" -headers "$out/core/headers" \
    -output "$here/build/MailMindCore.xcframework" >/dev/null

echo "## the app, Release, for arm64 and x86_64"
(cd "$here" && xcodegen generate --quiet)
xcodebuild -quiet -project "$here/MailMind.xcodeproj" -scheme MailMind-macOS -configuration Release \
    -destination 'generic/platform=macOS' -derivedDataPath "$derived" \
    ARCHS="arm64 x86_64" ONLY_ACTIVE_ARCH=NO CODE_SIGNING_ALLOWED=NO \
    SPARKLE_PUBLIC_KEY="$key" MAILMIND_CHECKS_FOR_UPDATES=YES build
built="$derived/Build/Products/Release/MailMind.app"
app="$out/MailMind.app"
ditto "$built" "$app"

info() { /usr/libexec/PlistBuddy -c "Print :$1" "$app/Contents/Info.plist"; }
version="$(info CFBundleShortVersionString)"
[[ "$(info CFBundleIdentifier)" == io.github.lanterno.mailmind ]] || stop "the app's identifier is $(info CFBundleIdentifier)"
[[ "$(info SUPublicEDKey)" == "$key" ]] || stop "the app doesn't carry the update key it was built with"
[[ "$(info MailMindChecksForUpdates)" == YES ]] || stop "the app was built with update checks off"
for binary in "$app/Contents/MacOS/MailMind" "$app/Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle"; do
    archs="$(lipo -archs "$binary")"
    [[ "$archs" == *arm64* && "$archs" == *x86_64* ]] || stop "$(basename "$binary") is $archs, not universal"
done

echo "## signed ad hoc, under the hardened runtime"
# Inside out, as Sparkle's documentation orders it: each helper, then the framework, then the app, which seals them.
# A Developer ID (#42) replaces '-' here, and adds a secure timestamp.
sign() {
    local said
    said="$(codesign --force --sign - --options runtime "$@" 2>&1)" || stop "codesign refused: $said"
}
sparkle="$app/Contents/Frameworks/Sparkle.framework"
sign "$sparkle/Versions/B/XPCServices/Installer.xpc"
sign --preserve-metadata=entitlements "$sparkle/Versions/B/XPCServices/Downloader.xpc"
sign "$sparkle/Versions/B/Autoupdate"
sign "$sparkle/Versions/B/Updater.app"
sign "$sparkle"
sign --entitlements "$here/Release/MailMind.entitlements" "$app"
codesign --verify --deep --strict "$app" || stop "the app's signature doesn't verify"

echo "## the disk image"
dmg="$out/MailMind-$version.dmg"
stage="$out/dmg"
mkdir -p "$stage"
ditto "$app" "$stage/MailMind.app"
ln -s /Applications "$stage/Applications"
# LZMA, the smallest of hdiutil's formats; every Mac MailMind runs on opens it.
hdiutil create -quiet -volname MailMind -srcfolder "$stage" -fs HFS+ -format ULMO -ov "$dmg"
/bin/rm -rf "$stage"

echo "## the appcast"
bin="$derived/SourcePackages/artifacts/sparkle/Sparkle/bin"
updates="$out/updates"
mkdir -p "$updates"
cp "$dmg" "$updates/"
if [[ -n "${MAILMIND_RELEASE_KEY_FILE:-}" ]]; then
    signing=(--ed-key-file "$MAILMIND_RELEASE_KEY_FILE")
else
    # The owner's key, in their login Keychain under this account. macOS asks once to let generate_appcast use it.
    signing=(--account mailmind)
fi
"$bin/generate_appcast" "${signing[@]}" \
    --download-url-prefix "https://github.com/$repo/releases/download/v$version/" \
    --full-release-notes-url "https://github.com/$repo/releases/tag/v$version" \
    --link "https://github.com/$repo" \
    -o "$out/appcast.xml" "$updates"
/bin/rm -rf "$updates"
# generate_appcast keeps the app it unpacked from the .dmg, by the .dmg's hash, for making delta updates from older
# archives put beside it. There are none, so it would only pile up.
/bin/rm -rf "$HOME/Library/Caches/Sparkle_generate_appcast/$(shasum -a 256 "$dmg" | cut -d ' ' -f 1)"
# As a person's copy will check it: against the key the app carries, not the one that signed.
eddsa="$out/core/eddsa"
xcrun swiftc -O "$here/Release/eddsa.swift" -o "$eddsa"
"$eddsa" verify-appcast "$out/appcast.xml" "$dmg" "$key"

sed "s/{{version}}/$version/g" "$here/Release/notes.md" >"$out/notes.md"
/bin/rm -rf "$out/core"

echo "## done"
echo "MailMind $version: $dmg ($(($(stat -f %z "$dmg") / 1024)) KB), with $out/appcast.xml"
commit="$(git -C "$here" rev-parse HEAD)"
if [[ -n "$(git -C "$here" status --porcelain)" ]]; then
    echo "warning: the tree has changes not committed, so this build isn't the commit the release will name." >&2
fi
cat <<EOF

To publish: open the .dmg and try the app, write what changed in $out/notes.md, then run

    gh release create v$version --repo $repo --target $commit --latest \\
        --title "MailMind $version" --notes-file "$out/notes.md" \\
        "$dmg" "$out/appcast.xml"

Every copy of MailMind checks the latest release's appcast.xml, so this release is what they will be offered.
EOF
