#!/usr/bin/env bash
set -euo pipefail

root="${1:-}"
applications="$root/usr/share/applications"
icon="$root/usr/share/icons/hicolor/scalable/apps/winetricks.svg"

test -f "$icon"

# Fedora's Protontricks entries reference `wine`, but wine-core does not ship
# that icon. Winetricks is already a Protontricks dependency and supplies this
# scalable icon in every Monolith edition.
for name in protontricks.desktop protontricks-launch.desktop; do
  desktop="$applications/$name"
  test -f "$desktop"
  grep -q '^Icon=' "$desktop"
  sed -i 's/^Icon=.*/Icon=winetricks/' "$desktop"
  test "$(grep -c '^Icon=winetricks$' "$desktop")" -eq 1
done
