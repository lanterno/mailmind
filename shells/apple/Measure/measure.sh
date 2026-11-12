#!/usr/bin/env bash
# The Apple shell's measurements on this Mac: cold start to first rows, memory idle and after scrolling and jumping
# through 100,000 fictional messages, the main thread's time per pass while scrolling, the cost of crossing into the
# core, the core's size, and a system web view's memory showing one fictional email. `just apple measure` runs it
# after a release build. Results go to stdout and build/measure/results.txt.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="$here/build/measure"
app="$here/build/DerivedData/Build/Products/Release/MailMind.app/Contents/MacOS/MailMind"
xcf="$here/build/MailMindCore.xcframework"
mkdir -p "$out"
exec > >(tee "$out/results.txt") 2>&1

echo "# $(sw_vers -productName) $(sw_vers -productVersion), $(sysctl -n machdep.cpu.brand_string), $(($(sysctl -n hw.memsize) / 1073741824)) GB"
echo "# load averages at start: $(sysctl -n vm.loadavg)"

# The memory the system counts against each process (phys_footprint), as top's MEM reports it.
mem() { top -l 1 $(printf -- '-pid %s ' "$@") -stats pid,command,mem | awk 'f{print} /^PID/{f=1}'; }

echo "## cold start: process start to the first batch applied on the main actor"
# The first launch after a build pays one-off costs (and makes the fictional mailbox if there is none): not counted.
MAILMIND_MEASURE=start "$app" 2>/dev/null | sed 's/^/first launch: /'
starts=()
for _ in 1 2 3 4 5 6 7; do
    starts+=("$(MAILMIND_MEASURE=start "$app" 2>/dev/null | sed -n 's/^first_rows_ms=//p')")
done
echo "first_rows_ms runs: ${starts[*]}"
printf '%s\n' "${starts[@]}" | sort -n | awk '{a[NR]=$1} END {print "first_rows_ms median: " a[int((NR+1)/2)]}'

echo "## memory and main-thread time: idle, a ten-second fast scroll, the whole list, then jumps"
log="$out/app.log"
tops="$out/top.log"
: >"$tops"
MAILMIND_MEASURE=all "$app" >"$log" 2>/dev/null &
# The app writes its log; top's readings go to a file of their own, as two writers to one file overwrite each other.
while ! grep -q "phase=done" "$log" 2>/dev/null; do
    if grep -q "phase=idle" "$log" && ! grep -q "top_idle" "$tops"; then
        pid=$(sed -n 's/^phase=idle pid=//p' "$log")
        echo "top_idle_mem $(mem "$pid" | awk '{print $NF}')" >>"$tops"
    fi
    kill -0 $! 2>/dev/null || break
    sleep 0.5
done
if grep -q "phase=done" "$log"; then
    pid=$(sed -n 's/^phase=done pid=//p' "$log")
    echo "top_done_mem $(mem "$pid" | awk '{print $NF}')" >>"$tops"
fi
wait || true
grep -v '^phase=' "$log"
cat "$tops"

echo "## crossing into the core from Swift (release, -O)"
xcrun swiftc -O -parse-as-library -module-name Probe -I "$xcf/macos-arm64/Headers" \
    "$here/Generated/MailMindCore.swift" "$here/Measure/probe.swift" "$xcf/macos-arm64/libmailmind_core.a" \
    -framework Security -o "$out/probe"
for _ in 1 2 3; do "$out/probe"; done

echo "## the core's size"
for slice in ios-arm64 ios-arm64-simulator macos-arm64; do
    echo "static library $slice: $(stat -f %z "$xcf/$slice/libmailmind_core.a") bytes"
done
echo "stripped app executable, core inside: macOS $(stat -f %z "$app") bytes," \
    "iOS device $(stat -f %z "$here/build/ios/Release-iphoneos/MailMind.app/MailMind") bytes"

echo "## a system web view showing one fictional email, JavaScript off"
xcrun swiftc -O -parse-as-library "$here/Measure/webview.swift" -o "$out/webview-probe"
before=$(pgrep -f 'com.apple.WebKit' | sort || true)
web="$out/webview.log"
"$out/webview-probe" >"$web" 2>/dev/null &
while ! grep -q '^loaded' "$web" 2>/dev/null; do kill -0 $! 2>/dev/null || break; sleep 0.5; done
cat "$web"
after=$(pgrep -f 'com.apple.WebKit' | sort || true)
started=$(comm -13 <(echo "$before") <(echo "$after") | tr '\n' ' ')
ui=$(sed -n 's/^loaded ui_pid=\([0-9]*\).*/\1/p' "$web")
# top cuts a command's name short, so ps names each process.
for pid in $ui $started; do
    echo "$(ps -o comm= -p "$pid" | xargs basename): $(mem "$pid" | awk '{print $NF}')"
done
wait || true
