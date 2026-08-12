#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

applications="$test_root/usr/share/applications"
icon_dir="$test_root/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$applications" "$icon_dir"
touch "$icon_dir/winetricks.svg"

for name in protontricks.desktop protontricks-launch.desktop; do
  printf '%s\n' \
    '[Desktop Entry]' \
    'Name=Protontricks' \
    'Icon=wine' > "$applications/$name"
done

"$repo_root/files/scripts/fix-protontricks-icon.sh" "$test_root"

for name in protontricks.desktop protontricks-launch.desktop; do
  grep -qx 'Icon=winetricks' "$applications/$name"
  ! grep -qx 'Icon=wine' "$applications/$name"
done
