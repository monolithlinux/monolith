#!/usr/bin/env bash
# The greeting in new fish terminals, and the Monolith mark that it, fastfetch,
# monolith help and monolith status share.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
greeting="$repo_root/files/system/usr/libexec/monolith/greeting"
function_file="$repo_root/files/system/usr/share/fish/vendor_functions.d/fish_greeting.fish"
logos="$repo_root/files/system/usr/share/fastfetch/logos"
fastfetch_config="$repo_root/files/system/etc/xdg/fastfetch/config.jsonc"

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
has() { grep -qF -- "$2" <<<"$1" || fail "expected '$2' in:"$'\n'"$1"; }
lacks() { ! grep -qF -- "$2" <<<"$1" || fail "did not expect '$2' in:"$'\n'"$1"; }

# The marks ------------------------------------------------------------------
# Each size fits the layouts that use it, and uses only the two colors that
# fastfetch's config and common.sh fill in.
for entry in monolith:34 monolith-medium:16 monolith-small:10 monolith-tiny:8; do
    file="$logos/${entry%%:*}.txt" width="${entry#*:}"
    [ -s "$file" ] || fail "missing $file"
    LC_ALL=C.UTF-8 python3 - "$file" "$width" <<'EOF' || fail "bad logo: $file"
import re, sys
path, width = sys.argv[1], int(sys.argv[2])
rows = open(path, encoding="utf-8").read().splitlines()
blocks = set(" █▀▄▌▐▖▗▘▙▚▛▜▝▞▟")
for row in rows:
    plain = re.sub(r"\$[12]", "", row)
    assert "$" not in plain, f"only $1 and $2 are filled in: {row!r}"
    assert set(plain) <= blocks, f"not a block character: {row!r}"
    assert len(plain) <= width, f"wider than {width}: {row!r}"
    assert row.lstrip(" ").startswith(("$1", "$2")), f"no color at the start: {row!r}"
EOF
done
has "$(cat "$fastfetch_config")" '"source": "/usr/share/fastfetch/logos/monolith.txt"'
has "$(cat "$fastfetch_config")" '"1": "default"'
has "$(cat "$fastfetch_config")" '"2": "light_blue"'

# fastfetch draws the mark in the two colors when it's installed.
if command -v fastfetch >/dev/null; then
    out="$(fastfetch --logo "$logos/monolith.txt" --logo-type file --logo-color-1 default \
        --logo-color-2 light_blue --structure Title --pipe false 2>&1)"
    has "$out" $'\e[39m'
    has "$out" $'\e[94m'
    has "$out" '█'
fi

# The greeting -----------------------------------------------------------------
cat >"$test_root/os-release" <<'EOF'
NAME="Monolith"
PRETTY_NAME="Monolith KDE"
OSTREE_VERSION='44.20261030.0'
EOF
mkdir -p "$test_root/bin" "$test_root/state/monolith/update-reminder"
# The greeting runs in every new terminal, so it never waits for rpm-ostree.
printf '#!/bin/sh\ntouch "%s/rpm-ostree-ran"\nexit 1\n' "$test_root" >"$test_root/bin/rpm-ostree"
chmod +x "$test_root/bin/rpm-ostree"
record="$test_root/state/monolith/update-reminder/last-update"

greet() {
    env PATH="$test_root/bin:$PATH" LC_ALL=C.UTF-8 TERM=xterm-256color \
        MONOLITH_OS_RELEASE="$test_root/os-release" MONOLITH_STAGED="$test_root/staged" \
        XDG_STATE_HOME="$test_root/state" "$@" "$greeting" </dev/null 2>&1
}

touch "$record"
out="$(greet)"
[ "$(sed -n 1p <<<"$out")" = 'Monolith KDE · 44.20261030.0' ] || fail "first line: $out"
has "$out" 'Tip: '
lacks "$out" '█'
[ ! -e "$test_root/rpm-ostree-ran" ] || fail 'the greeting ran rpm-ostree'

# Every tip fits beside the mark in an 80-column terminal.
tips="$(sed -n '/^tips=(/,/^)/p' "$greeting" | sed '1d;$d')"
[ "$(wc -l <<<"$tips")" -ge 5 ] || fail "tips: $tips"
while IFS= read -r tip; do
    tip="${tip#*\"}" tip="${tip%\"}"
    line="Tip: ${tip%%|*} ${tip#*|}"
    [ "${#line}" -le 66 ] || fail "tip too long for 80 columns (${#line}): $line"
done <<<"$tips"

out="$(greet MONOLITH_MARK=1 COLUMNS=80)"
[ "$(sed -n 1p <<<"$out")" = ' ▙▖  ▗▟   Monolith KDE · 44.20261030.0' ] || fail "no mark: $out"
has "$(sed -n 2p <<<"$out")" ' █ ▟▙ █   Tip: '
out="$(greet MONOLITH_MARK=1 COLUMNS=50)"
lacks "$out" '▙'

touch "$test_root/staged"
out="$(greet)"
has "$out" '↑ An update is ready. Restart to use it.'
rm "$test_root/staged"

touch -d '9 days ago' "$record"
out="$(greet)"
has "$out" '! Last full update 9 days ago: monolith update'
touch "$record"

# Plain text where the terminal can't show the symbols.
out="$(greet TERM=linux MONOLITH_MARK=1)"
has "$out" 'Monolith KDE - 44.20261030.0'
lacks "$out" '·'
lacks "$out" '▙'

# Without OSTREE_VERSION there's just the name.
printf 'PRETTY_NAME="Monolith KDE (NVIDIA)"\n' >"$test_root/os-release"
[ "$(greet | sed -n 1p)" = 'Monolith KDE (NVIDIA)' ] || fail "$(greet)"

"$greeting" --help | grep -q 'set -U fish_greeting' || fail 'greeting --help'

# The fish function: Monolith's greeting unless you set fish_greeting.
if command -v fish >/dev/null; then
    mkdir -p "$test_root/functions"
    sed "s|/usr/libexec/monolith/greeting|$greeting|g" "$function_file" \
        >"$test_root/functions/fish_greeting.fish"
    fish_run() {
        env PATH="$test_root/bin:$PATH" LC_ALL=C.UTF-8 TERM=xterm-256color \
            MONOLITH_OS_RELEASE="$test_root/os-release" MONOLITH_STAGED="$test_root/staged" \
            XDG_STATE_HOME="$test_root/state" HOME="$test_root" \
            fish --no-config -c "set -p fish_function_path $test_root/functions; $1" 2>&1
    }
    has "$(fish_run fish_greeting)" 'Monolith KDE (NVIDIA)'
    [ -z "$(fish_run 'set -g fish_greeting; fish_greeting')" ] || fail 'an empty fish_greeting still greets'
    [ "$(fish_run "set -g fish_greeting 'Hello there'; fish_greeting")" = 'Hello there' ] ||
        fail "your own greeting: $(fish_run "set -g fish_greeting 'Hello there'; fish_greeting")"
    # Inside a container without Monolith's files, there's no greeting and no error.
    sed -i "s|$greeting|$test_root/missing|g" "$test_root/functions/fish_greeting.fish"
    [ -z "$(fish_run fish_greeting)" ] || fail "missing script: $(fish_run fish_greeting)"
fi

printf 'greeting tests passed\n'
