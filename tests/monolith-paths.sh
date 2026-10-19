#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
manager="$repo_root/files/system/usr/bin/monolith"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

# Reproduce Fedora Atomic's /home -> /var/home alias in an isolated tree.
mkdir -p "$test_root/var/home/tester"
ln -s var/home "$test_root/home"
test_home="$test_root/home/tester"
managed_root="$test_home/.local/share/monolith/software/tea"
state_root="$test_home/.local/state/monolith/software"

mkdir -p "$test_home/.local/bin" "$managed_root/bin" "$state_root"
printf '#!/usr/bin/env sh\nexit 0\n' > "$managed_root/bin/tea"
chmod 0755 "$managed_root/bin/tea"
ln -s "$managed_root/bin/tea" "$test_home/.local/bin/tea"
printf 'version=test\nsource=test\n' > "$state_root/tea.managed"

status="$(HOME="$test_home" PATH=/usr/bin:/bin "$manager" list)"
grep -Eq '^Developer CLIs[[:space:]]+Tea[[:space:]]+installed' <<< "$status"

# Canonicalization must not weaken the check for a launcher outside its root.
mkdir -p "$test_home/.local/share/unmanaged"
printf '#!/usr/bin/env sh\nexit 0\n' > "$test_home/.local/share/unmanaged/tea"
chmod 0755 "$test_home/.local/share/unmanaged/tea"
ln -sfn "$test_home/.local/share/unmanaged/tea" "$test_home/.local/bin/tea"

status="$(HOME="$test_home" PATH=/usr/bin:/bin "$manager" list)"
grep -Eq '^Developer CLIs[[:space:]]+Tea[[:space:]]+needs repair' <<< "$status"
