#!/usr/bin/env bash
# The release recipe end to end, as `just apple release-check` runs it: release.sh signs with a throwaway update key
# made here, in a temporary folder, so neither this nor the recipe touches the login Keychain or the owner's key.
# Then the checks a person's Mac and Sparkle make: the .dmg mounts and holds the app and an Applications link, the app
# is universal, its signature verifies, its Info.plist names the app, its version and the key, and the appcast names
# the .dmg with a signature that verifies against the key the app carries. Nothing is published, and nothing here
# opens MailMind itself, which would read and write its data folder: a probe signed as the app is loads its Sparkle.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/mailmind-release-check.XXXXXX")"
mounted=""
cleanup() {
    if [[ -n "$mounted" ]]; then hdiutil detach -quiet "$mounted" || true; fi
    /bin/rm -rf "$work"
}
trap cleanup EXIT

failed=0
check() {
    local what="$1"
    shift
    if "$@" >"$work/said" 2>&1; then
        echo "ok: $what"
    else
        echo "FAILED: $what" >&2
        sed 's/^/    /' "$work/said" >&2
        failed=$((failed + 1))
    fi
}
plist() { /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist"; }
equal() { [[ "$1" == "$2" ]] || { echo "expected '$2', got '$1'"; return 1; }; }

xcrun swiftc -O "$here/Release/eddsa.swift" -o "$work/eddsa"
mkdir -p "$work/key" "$work/other"
public="$("$work/eddsa" throwaway-key "$work/key")"
other="$("$work/eddsa" throwaway-key "$work/other")"
release() {
    MAILMIND_RELEASE_OUT="$work/out" MAILMIND_RELEASE_KEY_FILE="$work/key/private-key" "$here/Release/release.sh"
}

echo "## the recipe stops while the key is the placeholder"
stops() {
    ! MAILMIND_RELEASE_PUBLIC_KEY="REPLACE-WITH-THE-OWNERS-PUBLIC-KEY" release 2>"$work/stopped" \
        && grep -q "still the placeholder" "$work/stopped"
}
check "the release stops at once, with words saying why" stops

echo "## the release, with a throwaway key"
MAILMIND_RELEASE_PUBLIC_KEY="$public" release

version="$(sed -n 's/^ *MARKETING_VERSION: *"\(.*\)"$/\1/p' "$here/project.yml")"
dmg="$work/out/MailMind-$version.dmg"
appcast="$work/out/appcast.xml"

echo "## the disk image"
check "MailMind-$version.dmg is made" test -f "$dmg"
check "its checksum verifies" hdiutil verify -quiet "$dmg"
mkdir "$work/mnt"
hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "$work/mnt" "$dmg"
mounted="$work/mnt"
app="$mounted/MailMind.app"
check "it mounts and holds MailMind.app" test -x "$app/Contents/MacOS/MailMind"
check "beside a link to /Applications" equal "$(readlink "$mounted/Applications")" /Applications

echo "## the app in it"
universal() {
    for binary in "$app/Contents/MacOS/MailMind" "$app/Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle"; do
        lipo -info "$binary"
        lipo "$binary" -verify_arch arm64 x86_64 || return 1
    done
}
check "it is universal: arm64 and x86_64, the app and Sparkle" universal
check "its signature verifies" codesign --verify --deep --strict --verbose=2 "$app"
signed() {
    codesign -dv "$app" 2>&1 | tee "$work/signature"
    grep -q '^Signature=adhoc$' "$work/signature" && grep -q '^CodeDirectory .*flags=.*(adhoc,runtime)' "$work/signature"
}
check "it is signed ad hoc, under the hardened runtime" signed
entitled() {
    codesign -d --entitlements - --xml "$app" 2>/dev/null >"$work/entitlements"
    grep -q '<key>com.apple.security.cs.disable-library-validation</key><true/>' "$work/entitlements"
}
check "with the entitlement that lets it load Sparkle" entitled
check "its identifier is io.github.lanterno.mailmind" equal "$(plist "$app" CFBundleIdentifier)" io.github.lanterno.mailmind
check "its version is project.yml's, $version" equal "$(plist "$app" CFBundleShortVersionString)" "$version"
check "and so is its build number" equal "$(plist "$app" CFBundleVersion)" "$version"
check "it carries the update key it was signed for" equal "$(plist "$app" SUPublicEDKey)" "$public"
check "it checks for updates" equal "$(plist "$app" MailMindChecksForUpdates)" YES
check "from the latest release's appcast" equal "$(plist "$app" SUFeedURL)" \
    "https://github.com/lanterno/mailmind/releases/latest/download/appcast.xml"

echo "## Sparkle.framework loads under the app's signature"
# Library validation is checked when dyld maps a framework, before any of the app's code runs. A program signed as the
# app is (ad hoc, hardened runtime, the app's entitlements) loads the app's own copy of Sparkle.
loads() {
    local probe="$work/probe/Probe.app/Contents"
    mkdir -p "$probe/MacOS" "$probe/Frameworks"
    ditto "$app/Contents/Frameworks/Sparkle.framework" "$probe/Frameworks/Sparkle.framework"
    printf 'import Sparkle\nprint("loaded", SPUUpdater.self)\n' >"$work/probe.swift"
    xcrun swiftc "$work/probe.swift" -o "$probe/MacOS/Probe" -framework Sparkle \
        -F "$here/build/release-DerivedData/SourcePackages/artifacts/sparkle/Sparkle/Sparkle.xcframework/macos-arm64_x86_64" \
        -Xlinker -rpath -Xlinker @executable_path/../Frameworks
    codesign --force --sign - --options runtime --entitlements "$here/Release/MailMind.entitlements" "$probe/MacOS/Probe"
    "$probe/MacOS/Probe" | grep -q '^loaded SPUUpdater$'
}
check "a program signed as the app is loads the app's Sparkle.framework" loads

echo "## the appcast"
check "it names the .dmg on the release's download URL" grep -q \
    "url=\"https://github.com/lanterno/mailmind/releases/download/v$version/MailMind-$version.dmg\"" "$appcast"
check "its version is $version" grep -q "<sparkle:version>$version</sparkle:version>" "$appcast"
check "the .dmg's signature verifies against the key the app carries" \
    "$work/eddsa" verify-appcast "$appcast" "$dmg" "$public"
signature="$(sed -n 's/.*sparkle:edSignature="\([^"]*\)".*/\1/p' "$appcast")"
check "and Sparkle's own sign_update agrees" \
    "$here/build/release-DerivedData/SourcePackages/artifacts/sparkle/Sparkle/bin/sign_update" \
    --verify --ed-key-file "$work/key/private-key" "$dmg" "$signature"
check "while another key refuses it" bash -c '! "$0" verify-appcast "$1" "$2" "$3"' \
    "$work/eddsa" "$appcast" "$dmg" "$other"
check "the release's notes name the version" grep -q "MailMind-$version.dmg" "$work/out/notes.md"

hdiutil detach -quiet "$mounted"
mounted=""
echo "## the .dmg is $(stat -f %z "$dmg") bytes ($(($(stat -f %z "$dmg") / 1024)) KB)"
if ((failed > 0)); then
    echo "release check: $failed failed" >&2
    exit 1
fi
echo "release check: every check passed"
