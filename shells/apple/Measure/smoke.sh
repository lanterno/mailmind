#!/usr/bin/env bash
# The Apple shell's smoke check, as `just apple smoke [mac|ios|all]` runs it after a release build: each app opens its
# fictional mailbox, jumps nine tenths of the way down the list and waits for mail in the rows there (measure mode's
# MAILMIND_MEASURE=scroll). The Mac app runs here; the iPhone app in the newest iPhone simulator Xcode has.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
which="${1:-all}"
mkdir -p "$here/build/smoke"

# Five minutes for each run: making the fictional mailbox on a first launch, and a simulator's boot, are slow on CI.
limit() {
    if command -v timeout >/dev/null; then timeout 300 "$@"; else perl -e 'alarm shift; exec @ARGV' 300 "$@"; fi
}

if [[ "$which" == all || "$which" == mac ]]; then
    echo "## the Mac app"
    MAILMIND_MEASURE=scroll limit "$here/build/DerivedData/Build/Products/Release/MailMind.app/Contents/MacOS/MailMind"
fi

if [[ "$which" == all || "$which" == ios ]]; then
    echo "## the iPhone app, in a simulator"
    # The first iPhone of the newest iOS runtime installed.
    udid=$(xcrun simctl list devices available --json | python3 -I -c '
import json, sys
runtimes = json.load(sys.stdin)["devices"]
version = lambda key: [int(part) for part in key.rsplit(".", 1)[-1].split("-")[1:]]
for key in sorted((key for key in runtimes if ".iOS-" in key), key=version, reverse=True):
    phones = [device["udid"] for device in runtimes[key] if device["name"].startswith("iPhone")]
    if phones:
        print(phones[0])
        break
')
    if [[ -z "$udid" ]]; then
        echo "No iPhone simulator. Install iOS's with: xcodebuild -downloadPlatform iOS" >&2
        exit 1
    fi
    booted=$(xcrun simctl list devices booted | grep -c "$udid" || true)
    xcrun simctl bootstatus "$udid" -b >/dev/null
    app="$here/build/ios/Release-iphonesimulator/MailMind.app"
    # Built with signing off; the simulator runs only signed code, and an ad-hoc signature is enough.
    codesign --force --sign - "$app"
    xcrun simctl install "$udid" "$app"
    log="$here/build/smoke/ios.log"
    # simctl's own exit status does not carry the app's, so the app's last line says how the check went.
    SIMCTL_CHILD_MAILMIND_MEASURE=scroll limit xcrun simctl launch --console --terminate-running-process \
        "$udid" io.github.lanterno.mailmind 2>&1 | tee "$log" || true
    if [[ "$booted" == 0 ]]; then xcrun simctl shutdown "$udid" || true; fi
    grep -q '^scrolled_ms=' "$log" || { echo "The iPhone app's list never showed mail far down." >&2; exit 1; }
fi
