#!/usr/bin/env bash
# The catalog's look in fzf: the home screen's header and icons, and the
# details beside each category's list. A stand-in fzf records what the catalog
# shows and picks the options a test asks for.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
has() { grep -qF -- "$2" <<<"$1" || fail "expected '$2' in:"$'\n'"$1"; }
lacks() { ! grep -qF -- "$2" <<<"$1" || fail "did not expect '$2' in:"$'\n'"$1"; }

command -v script >/dev/null || { printf 'skipped catalog look tests: script is not installed\n'; exit 0; }

usr="$test_root/usr"
mkdir -p "$usr/bin" "$usr/libexec/monolith" "$usr/share/fastfetch" "$test_root/bin" "$test_root/log"
cp "$repo_root/files/system/usr/bin/monolith" "$usr/bin/"
cp "$repo_root/files/system/usr/libexec/monolith/"{software,common.sh,gear-lever-app,ghostty-themes} \
    "$usr/libexec/monolith/"
cp -r "$repo_root/files/system/usr/share/monolith" "$usr/share/"
cp -r "$repo_root/files/system/usr/share/fastfetch/logos" "$usr/share/fastfetch/"

cat >"$test_root/os-release" <<'EOF'
NAME="Monolith"
PRETTY_NAME="Monolith KDE"
OSTREE_VERSION='44.20261030.0'
EOF

# fzf stand-in: saves its arguments (one per record) and options, then prints
# the option whose action, the last tab-separated field, is the next answer.
cat >"$test_root/bin/fzf" <<'EOF'
#!/usr/bin/env bash
log="$FAKE_FZF_LOG"
n=$(($(cat "$log/count" 2>/dev/null || echo 0) + 1))
echo "$n" >"$log/count"
printf '%s\x1e' "$@" >"$log/args.$n"
cat >"$log/input.$n"
answer="$(sed -n "${n}p" "$log/answers")"
[ -n "$answer" ] || exit 130
while IFS= read -r line; do
    if [ "${line##*$'\t'}" = "$answer" ]; then
        printf '%s\n' "$line"
        exit 0
    fi
done <"$log/input.$n"
exit 1
EOF
printf '#!/bin/sh\nexit 0\n' >"$test_root/bin/systemctl"
chmod +x "$test_root/bin/fzf" "$test_root/bin/systemctl"

# arg N OPTION: the value fzf call N got for --OPTION.
arg() {
    local record
    while IFS= read -r -d $'\x1e' record; do
        if [[ $record == "--$2="* ]]; then
            printf '%s\n' "${record#--"$2"=}"
            return 0
        fi
    done <"$test_root/log/args.$1"
    return 1
}
# shellcheck disable=SC2001 # a regular expression
plain() { sed 's/\x1b\[[0-9;]*m//g' <<<"$1"; }
has_arg() { grep -qF -- "$2"$'\x1e' "$test_root/log/args.$1" || fail "fzf call $1 lacks $2"; }

# catalog ANSWER...: open the catalog on a terminal and pick ANSWERs in turn.
# EXTRA_ENV adds space-separated variables.
# shellcheck disable=SC2086
catalog() {
    rm -f "$test_root/log/"*
    printf '%s\n' "$@" >"$test_root/log/answers"
    env -i HOME="$test_root/home" PATH="$test_root/bin:/usr/bin:/bin" LANG=C.UTF-8 \
        TERM="${TERM_OVERRIDE:-xterm-256color}" COLUMNS="${COLUMNS_OVERRIDE:-100}" LINES="${LINES_OVERRIDE:-40}" \
        ${EXTRA_ENV:+$EXTRA_ENV} \
        XDG_STATE_HOME="$test_root/home/.local/state" MONOLITH_OS_RELEASE="$test_root/os-release" \
        MONOLITH_STAGED="$test_root/staged" FAKE_FZF_LOG="$test_root/log" \
        timeout 120 script -qec "$usr/bin/monolith" /dev/null >"$test_root/out" 2>&1 ||
        fail "the catalog did not finish: $(tail -n 5 "$test_root/out")"
}

# Tea is installed, Superfile lost its files, and the last full update was two
# days ago.
home="$test_root/home"
state="$home/.local/state/monolith/software"
mkdir -p "$state" "$home/.local/share/monolith/software/tea/bin" "$home/.local/bin" \
    "$home/.local/state/monolith/update-reminder"
printf 'version=0.11.1\nsource=test\n' >"$state/tea.managed"
printf 'version=1.4.0\nsource=test\n' >"$state/superfile.managed"
printf '#!/bin/sh\necho "tea version 0.11.1"\n' >"$home/.local/share/monolith/software/tea/bin/tea"
chmod +x "$home/.local/share/monolith/software/tea/bin/tea"
ln -s "$home/.local/share/monolith/software/tea/bin/tea" "$home/.local/bin/tea"
touch -d '2 days ago' "$home/.local/state/monolith/update-reminder/last-update"

# The home screen in Ghostty -------------------------------------------------
EXTRA_ENV="TERM_PROGRAM=ghostty" catalog exit
has "$(arg 1 header)" $'\e[32m✓\e[0m System up to date'
header="$(plain "$(arg 1 header)")"
has "$header" 'Monolith KDE'
has "$header" '44.20261030.0 · Oct 30'
has "$header" '✓ System up to date'
has "$header" '! 1 installed, 1 needs repair'
has "$header" '✓ Last full update 2 days ago'
has "$header" '███   ████   ███'
[ "$(arg 1 border-label)" = ' Monolith Software ' ] || fail "label: $(arg 1 border-label)"
[ "$(arg 1 ghost)" = 'Search apps and tools' ] || fail "ghost: $(arg 1 ghost)"
has "$(arg 1 footer)" 'open'
has "$(arg 1 prompt)" $'\xf3\xb0\x8d\x89'
rows="$(cat "$test_root/log/input.1")"
has "$rows" $'\xf3\xb0\x86\x8d'
has "$rows" 'Developer CLIs'
has "$rows" '1 installed, 1 needs repair'
[ "$(tail -n 1 <<<"$rows")" = $'\e[94m\xf3\xb0\x88\x86\e[0m  \e[2mExit\e[0m\texit' ] || fail "last row: $(tail -n 1 <<<"$rows" | od -c)"
# Only the terminal's 16 colors, so menus follow its theme.
if grep -qE $'\e\\[[0-9;]*(38|48);[25];' "$test_root/log/args.1" "$test_root/log/input.1"; then
    fail 'the home screen uses colors beyond the 16 terminal colors'
fi

# A downloaded update, apps without updates, and a short window.
touch "$test_root/staged"
touch -d '9 days ago' "$home/.local/state/monolith/update-reminder/last-update"
LINES_OVERRIDE=22 catalog exit
header="$(plain "$(arg 1 header)")"
has "$header" '↑ Restart to finish the system update'
has "$header" ' █▄      ▄█   Monolith KDE · 44.20261030.0'
lacks "$header" '███   ████   ███'
lacks "$header" 'Last full update'
LINES_OVERRIDE=40 catalog exit
has "$(plain "$(arg 1 header)")" '! Last full update 9 days ago'
rm "$test_root/staged"
touch -d '2 days ago' "$home/.local/state/monolith/update-reminder/last-update"

# Icons only in Ghostty unless MONOLITH_ICONS asks.
catalog exit
lacks "$(cat "$test_root/log/input.1")" $'\xf3\xb0'
[ "$(arg 1 prompt)" = '> ' ] || fail "prompt: $(arg 1 prompt)"
EXTRA_ENV="MONOLITH_ICONS=1" catalog exit
has "$(cat "$test_root/log/input.1")" $'\xf3\xb0\x86\x8d'
EXTRA_ENV="TERM_PROGRAM=ghostty MONOLITH_ICONS=0" catalog exit
lacks "$(cat "$test_root/log/input.1")" $'\xf3\xb0'

# NO_COLOR: black and white, no color codes in the options.
EXTRA_ENV="NO_COLOR=1" catalog exit
has_arg 1 '--color=bw'
lacks "$(cat "$test_root/log/input.1")" $'\e'

# A category with details beside the list ----------------------------------
catalog cli back exit
[ "$(arg 2 border-label)" = ' Monolith Software › Developer CLIs ' ] || fail "label: $(arg 2 border-label)"
has "$(arg 2 preview)" "printf '%b' {2}"
has "$(arg 2 with-shell)" 'bash -c'
has "$(arg 2 header)" '1 installed'
has "$(arg 2 header)" '1 needs repair'
rows="$(cat "$test_root/log/input.2")"
tea_row="$(grep -F 'tool:tea' <<<"$rows")"
superfile_row="$(grep -F 'tool:superfile' <<<"$rows")"
has "$tea_row" $'\e[32m●\e[0m \e[1mTea'
has "$tea_row" '0.11.1'
has "$superfile_row" $'\e[33m!\e[0m'
has "$superfile_row" 'needs repair'
# The preview prints the second field, and the catalog reads the action from
# the last.
IFS=$'\t' read -r _ preview action <<<"$tea_row"
[ "$action" = tool:tea ] || fail "action: $action"
details="$(printf '%b' "$preview" | sed 's/\x1b\[[0-9;]*m//g')"
has "$details" 'Tea (tea)'
has "$details" 'Gitea and Forgejo CLI'
has "$details" 'Status    ● installed'
has "$details" 'Version   0.11.1'
has "$details" 'Location  ~/.local/share/monolith/software/tea'
has "$details" 'Command   ~/.local/bin/tea'
has "$details" 'Removing  keeps your settings, authentication,'
# Every line fits beside the list in a 100-column terminal.
while IFS= read -r line; do
    [ "${#line}" -le 46 ] || fail "preview line too wide (${#line}): $line"
done <<<"$details"
[ "$(arg 3 border-label)" = ' Monolith Software ' ] || fail 'Back did not return home'

# Numbered menus keep their lines, with the descriptions and no symbols.
rm -f "$test_root/log/"*
printf '1\n0\n11\n' | env -i HOME="$home" PATH="$test_root/bin:/usr/bin:/bin" LANG=C.UTF-8 TERM=dumb \
    XDG_STATE_HOME="$home/.local/state" MONOLITH_OS_RELEASE="$test_root/os-release" \
    MONOLITH_SELECTOR=numbered timeout 120 script -qec "$usr/bin/monolith" /dev/null >"$test_root/out" 2>&1 ||
    fail "the numbered catalog did not finish: $(tail -n 5 "$test_root/out")"
out="$(tr -d '\r' <"$test_root/out")"
grep -Eq '^   1\) Tea +installed +0\.11\.1 +Gitea and Forgejo CLI$' <<<"$out" || fail "numbered rows: $out"
lacks "$out" '●'
[ ! -e "$test_root/log/count" ] || fail 'numbered menus ran fzf'

printf 'catalog look tests passed\n'
