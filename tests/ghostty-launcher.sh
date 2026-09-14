#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

# Substitute only the two installed paths, so the real launcher can run against
# fixtures without Ghostty, a display server, or changes to the test user's home.
sed \
    -e "s|/usr/share/monolith/ghostty/config|$test_root/defaults|g" \
    -e "s|/usr/bin/ghostty.real|$test_root/ghostty|g" \
    "$repo_root/files/kde/usr/bin/monolith-ghostty" > "$test_root/launcher"
chmod +x "$test_root/launcher"
printf 'command = /usr/bin/fish\nfont-size = 12\n' > "$test_root/defaults"
cat > "$test_root/ghostty" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\0' "$@" > "$GHOSTTY_TEST_ARGS"
umask > "$GHOSTTY_TEST_UMASK"
exit "${GHOSTTY_TEST_EXIT:-0}"
EOF
chmod +x "$test_root/ghostty"

export HOME="$test_root/home with spaces"
export XDG_CONFIG_HOME="$test_root/config with spaces"
export GHOSTTY_TEST_ARGS="$test_root/args"
export GHOSTTY_TEST_UMASK="$test_root/umask"
mkdir -p "$HOME"
launcher="$test_root/launcher"
config_dir="$XDG_CONFIG_HOME/ghostty"

# First launch installs complete private defaults, keeps the caller's umask,
# and preserves argument boundaries and shell metacharacters verbatim.
umask 022
arguments=(--working-directory="/tmp/folder with spaces" -e fish -c 'printf "%s" "$HOME;$(false)"')
"$launcher" "${arguments[@]}"
printf '%s\0' "${arguments[@]}" > "$test_root/expected-args"
cmp "$test_root/expected-args" "$GHOSTTY_TEST_ARGS"
cmp "$test_root/defaults" "$config_dir/config.ghostty"
[[ "$(stat -c '%a' "$config_dir/config.ghostty")" == 600 ]]
[[ "$(stat -c '%a' "$config_dir")" == 700 ]]
[[ "$(cat "$GHOSTTY_TEST_UMASK")" == 0022 ]]

# A subsequent launch does not refresh or replace user settings, including an
# intentionally empty file. Both Ghostty config filenames are respected.
for name in config config.ghostty; do
    rm -rf -- "$config_dir"
    mkdir -p "$config_dir"
    printf 'font-size = 18\n' > "$config_dir/$name"
    cp "$config_dir/$name" "$test_root/expected-config"
    "$launcher"
    cmp "$test_root/expected-config" "$config_dir/$name"
    [[ "$(find "$config_dir" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]]
    : > "$config_dir/$name"
    "$launcher"
    [[ ! -s "$config_dir/$name" ]]
done

# Dangling symlinks and directories are also existing user configurations;
# leave any resulting diagnostic to Ghostty, without writing through them.
for name in config config.ghostty; do
    rm -rf -- "$config_dir"
    mkdir -p "$config_dir"
    ln -s "$test_root/missing-target" "$config_dir/$name"
    "$launcher"
    [[ -L "$config_dir/$name" && ! -e "$test_root/missing-target" ]]
    [[ "$(find "$config_dir" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]]
    rm -- "$config_dir/$name"
    mkdir "$config_dir/$name"
    "$launcher"
    [[ -d "$config_dir/$name" ]]
    [[ -z "$(find "$config_dir/$name" -mindepth 1 -print -quit)" ]]
done

# Without XDG_CONFIG_HOME, use HOME/.config. Concurrent first launches must
# publish one complete file, clean up temporary files, and all reach Ghostty.
unset XDG_CONFIG_HOME
config_dir="$HOME/.config/ghostty"
pids=()
for index in {1..12}; do
    GHOSTTY_TEST_ARGS="$test_root/args-$index" "$launcher" "$index" &
    pids+=("$!")
done
for pid in "${pids[@]}"; do
    wait "$pid"
done
cmp "$test_root/defaults" "$config_dir/config.ghostty"
[[ "$(find "$config_dir" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]]
for index in {1..12}; do
    printf '%s\0' "$index" > "$test_root/expected-args"
    cmp "$test_root/expected-args" "$test_root/args-$index"
done

# A failing config directory must report an error instead of launching with
# missing defaults; Ghostty's exit status must also survive the exec.
export XDG_CONFIG_HOME="$test_root/blocked-config"
touch "$XDG_CONFIG_HOME"
if "$launcher" 2> "$test_root/error"; then
    printf 'Expected invalid config directory to fail\n' >&2
    exit 1
fi
[[ -s "$test_root/error" ]]
unset XDG_CONFIG_HOME
status=0
GHOSTTY_TEST_EXIT=42 "$launcher" || status="$?"
[[ "$status" == 42 ]]

printf 'Ghostty launcher tests passed\n'
