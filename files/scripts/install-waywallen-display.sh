#!/usr/bin/env bash
set -euo pipefail

root="${1:-}"
version=0.4.0
revision=4fc25d632125967de0be34d095206ca9420432e8
license_checksum=2d1edaf74e77c63e166ed948c2fee17a50dd15f73d8e9db249117e5fde8bef24
architecture="$(uname -m)"
case "$architecture" in
  x86_64) checksum=081193a269fb9d0f4d43fee683e9be4bbd31f03cbfee29940616144f649578d6 ;;
  aarch64) checksum=b208b8d684cba6954591e2f819bd8e6be370b47ded07852b15da61078d4b6df5 ;;
  *) printf 'Unsupported Waywallen KDE architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac

package_root="$root/usr/share/plasma/wallpapers"
license_dir="$root/usr/share/licenses/waywallen-display"
temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

# The Flatpak cannot install host Plasma plugins. Use upstream's embedded
# package so its native QML module stays beside the wallpaper that imports it.
archive="$temporary_dir/waywallen-kde.zip"
url="https://github.com/waywallen/waywallen-display/releases/download/v$version/waywallen-kde-$version-$architecture-embed.zip"
curl --fail --location --retry 3 --output "$archive" "$url"
printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
curl --fail --location --retry 3 --output "$temporary_dir/LICENSE" \
  "https://raw.githubusercontent.com/waywallen/waywallen-display/$revision/LICENSE"
printf '%s  %s\n' "$license_checksum" "$temporary_dir/LICENSE" | sha256sum --check --status

# Install only the available wallpaper type; never change users' selections.
install -d "$package_root" "$license_dir"
kpackagetool6 --type Plasma/Wallpaper \
  --packageroot "$package_root" --install "$archive"
install -m0644 "$temporary_dir/LICENSE" "$license_dir/LICENSE"
printf '%s\n' \
  'Waywallen KDE companion plugin (upstream embedded package)' \
  "Release: https://github.com/waywallen/waywallen-display/releases/tag/v$version" \
  "Source: https://github.com/waywallen/waywallen-display/tree/$revision" \
  "Archive: $url" \
  "Archive SHA256: $checksum" \
  > "$license_dir/SOURCE"
