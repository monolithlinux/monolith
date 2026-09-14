#!/usr/bin/env bash
set -euo pipefail

root="${1:-}"
revision=7da7cf8362299c64cf228654453affbcd1e67706
checksum=34b3b57c049eae43e52c8d31f6cf4bebabe484dfebe05a466ab2436b93bfdaf1
expected_themes=550
theme_dir="$root/usr/share/ghostty/themes"
license_dir="$root/usr/share/licenses/monolith-ghostty-themes"
temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

# Terra omits Ghostty's bundled theme collection. Install Tinted Terminal's
# complete generated Ghostty catalog with its license and upstream attribution.
archive="$temporary_dir/themes.tar.gz"
curl --fail --location --retry 3 --output "$archive" \
  "https://codeload.github.com/tinted-theming/tinted-terminal/tar.gz/$revision"
printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
tar --extract --gzip --file="$archive" --directory="$temporary_dir" \
  --strip-components=1 --no-same-owner \
  "tinted-terminal-$revision/themes/ghostty" "tinted-terminal-$revision/LICENSE"

themes=("$temporary_dir"/themes/ghostty/*)
test "${#themes[@]}" -eq "$expected_themes"
test -f "$temporary_dir/themes/ghostty/base16-gruvbox-dark-medium"
install -d "$theme_dir" "$license_dir"
install -m0644 -- "${themes[@]}" "$theme_dir/"
install -m0644 "$temporary_dir/LICENSE" "$license_dir/Tinted-Terminal-LICENSE"
printf '%s\n' \
  'Tinted Terminal — complete generated Ghostty theme catalog' \
  'https://github.com/tinted-theming/tinted-terminal' \
  "Commit: $revision" \
  "Archive SHA256: $checksum" \
  "Theme count: $expected_themes" \
  'License: MIT; see Tinted-Terminal-LICENSE.' \
  > "$license_dir/Tinted-Terminal-SOURCE"
