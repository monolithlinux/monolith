#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

# Replace only the installed launcher's path; run the actual option parser.
sed "s|/usr/bin/monolith-ghostty|$test_root/ghostty|g" \
    "$repo_root/files/kde/usr/bin/ptyxis" > "$test_root/ptyxis"
chmod +x "$test_root/ptyxis"
cat > "$test_root/ghostty" <<'EOF'
#!/usr/bin/env bash
printf '%s\0' "$@" > "$PTYXIS_TEST_ARGS"
exit "${PTYXIS_TEST_EXIT:-0}"
EOF
chmod +x "$test_root/ghostty"
export PTYXIS_TEST_ARGS="$test_root/args"
export PTYXIS_TEST_MARKER="$test_root/must-not-exist"
launcher="$test_root/ptyxis"

expect_args() {
    printf '%s\0' "$@" > "$test_root/expected"
    cmp "$test_root/expected" "$PTYXIS_TEST_ARGS"
}

expect_failure() {
    rm -f -- "$PTYXIS_TEST_ARGS"
    status=0
    "$launcher" "$@" 2> "$test_root/error" || status="$?"
    [[ "$status" == 2 && ! -e "$PTYXIS_TEST_ARGS" ]]
    grep -q '^Ptyxis compatibility launcher:' "$test_root/error"
}

"$launcher"
expect_args --gtk-single-instance=false
"$launcher" --new-window
expect_args --gtk-single-instance=false
for option in -s --standalone; do
    "$launcher" "$option" --new-window
    expect_args --gtk-single-instance=false
done

# A single-instance Ghostty would ignore some options. Explicit per-window
# settings must start a separate instance, preserving every value literally.
directory='/tmp/folder with spaces/$(touch "$PTYXIS_TEST_MARKER")'
title='literal "title"; $HOME * [x]'
"$launcher" --new-window -d "$directory" --title "$title" --maximize --fullscreen
expect_args --gtk-single-instance=false "--working-directory=$directory" \
    "--title=$title" --maximize=true --fullscreen=true
"$launcher" "--working-directory=$directory" "--title=$title"
expect_args --gtk-single-instance=false "--working-directory=$directory" "--title=$title"
"$launcher" --working-directory "$directory"
expect_args --gtk-single-instance=false "--working-directory=$directory"
"$launcher" -T "$title"
expect_args --gtk-single-instance=false "--title=$title"
[[ ! -e "$PTYXIS_TEST_MARKER" ]]

# Command argv is never re-parsed, including empty arguments and options that
# belong to the launched program. --execute uses GLib's quoting parser with
# no variable expansion, command substitution, or shell operator processing.
arguments=(fish -c 'printf "%s" "$HOME;$(touch "$PTYXIS_TEST_MARKER")"' '' --title=program-option)
"$launcher" --new-window -- "${arguments[@]}"
expect_args --gtk-single-instance=false -e "${arguments[@]}"
shell_command="$(cat <<'EOF'
printf '%s' '$HOME' ; '$(touch "$PTYXIS_TEST_MARKER")' '`touch "$PTYXIS_TEST_MARKER"`' '' 'two words' *.txt
EOF
)"
parsed=(printf '%s' '$HOME' ';' '$(touch "$PTYXIS_TEST_MARKER")' \
    '`touch "$PTYXIS_TEST_MARKER"`' '' 'two words' '*.txt')
for option in -x --execute; do
    "$launcher" "$option" "$shell_command" -d "$directory"
    expect_args --gtk-single-instance=false "--working-directory=$directory" \
        -e "${parsed[@]}"
done
"$launcher" "--execute=$shell_command"
expect_args --gtk-single-instance=false -e "${parsed[@]}"
"$launcher" --execute 'printf two\ words # GLib strips this comment'
expect_args --gtk-single-instance=false -e printf 'two words'
[[ ! -e "$PTYXIS_TEST_MARKER" ]]

# Unsupported Ptyxis-specific options must never silently open a host shell
# where a profile or container environment was requested.
for option in --tab --preferences --tab-with-profile=abc --profile=abc \
    --container=work --gapplication-app-id=other --unknown; do
    expect_failure "$option"
    grep -q 'unsupported Ptyxis option' "$test_root/error"
done
for option in -d --working-directory -T --title -x --execute --; do
    expect_failure "$option"
done
expect_failure --execute true -- false
expect_failure -x true -x false
expect_failure fish -c true
for invalid in '' '   ' '# comment only' 'printf "unterminated' 'printf trailing\'; do
    expect_failure --execute "$invalid"
    grep -q 'cannot parse --execute' "$test_root/error"
done

"$launcher" --version
expect_args --version
status=0
PTYXIS_TEST_EXIT=42 "$launcher" --new-window || status="$?"
[[ "$status" == 42 ]]
status=0
PTYXIS_TEST_EXIT=42 "$launcher" --execute true || status="$?"
[[ "$status" == 42 ]]

printf 'Ptyxis compatibility tests passed\n'
