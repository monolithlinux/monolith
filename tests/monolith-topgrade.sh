#!/usr/bin/env bash
# The topgrade drop-in must parse, and the login rule must link it without
# replacing a file the user put there.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dropin="$repo_root/files/system/usr/share/monolith/topgrade.d/monolith.toml"
rule="$repo_root/files/system/usr/share/user-tmpfiles.d/monolith-topgrade.conf"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# A malformed drop-in would stop every topgrade run.
python3 -c 'import sys, tomllib; tomllib.load(open(sys.argv[1], "rb"))' "$dropin" ||
    fail "$dropin is not valid TOML"

# Apply the rule the way the user session does at login.
login() {
    mkdir -p "$1" "$test_root/runtime"
    HOME="$1" XDG_RUNTIME_DIR="$test_root/runtime" systemd-tmpfiles --user --create "$rule"
}

home="$test_root/new"
login "$home"
target="$(readlink -- "$home/.config/topgrade.d/monolith.toml")" || fail 'login did not link the drop-in'
[ -f "$repo_root/files/system$target" ] || fail "the link points to $target, which the image does not ship"

# A file already there, such as an empty one to opt out, is kept.
home="$test_root/opted-out"
mkdir -p "$home/.config/topgrade.d"
: >"$home/.config/topgrade.d/monolith.toml"
login "$home"
path="$home/.config/topgrade.d/monolith.toml"
[ -f "$path" ] && [ ! -L "$path" ] && [ ! -s "$path" ] || fail 'login replaced the user file'

printf 'topgrade integration tests passed\n'
