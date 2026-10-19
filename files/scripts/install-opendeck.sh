#!/usr/bin/env bash
set -euo pipefail

version=2.14.1
revision=66db15d9d5d41597772a4f209cd6a93b498fc7df
license_checksum=cc5470feed66192387f06ae93aed0bd4fefae5a5fd7c1e54a4acf4ea64f1028b
architecture="$(uname -m)"
case "$architecture" in
  x86_64) checksum=bb1861b71d6ce97b498dd22a7a1793077c6fc8019cffff09fb672dc0d4336142 ;;
  aarch64) checksum=eb40b0bea7adbaf796157ee56cbb6b42abad26237e6fd4adf901b7875a226b37 ;;
  *) printf 'Unsupported OpenDeck architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac

license_dir=/usr/share/licenses/opendeck
monolith_rules=/usr/lib/udev/rules.d/40-streamdeck.rules
package_rules=/etc/udev/rules.d/40-streamdeck.rules
temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "$temporary_dir"' EXIT

# Upstream's RPM rather than its Flatpak: outside the sandbox, Start at login
# works and plugins start node and wine directly. The RPM is unsigned, so the
# pinned digest is what vouches for it.
package="$temporary_dir/opendeck-$version-1.$architecture.rpm"
url="https://github.com/nekename/OpenDeck/releases/download/v$version/opendeck-$version-1.$architecture.rpm"
curl --fail --location --retry 3 --output "$package" "$url"
printf '%s  %s\n' "$checksum" "$package" | sha256sum --check --status
curl --fail --location --retry 3 --output "$temporary_dir/LICENSE.md" \
  "https://raw.githubusercontent.com/nekename/OpenDeck/$revision/LICENSE.md"
printf '%s  %s\n' "$license_checksum" "$temporary_dir/LICENSE.md" | sha256sum --check --status

dnf5 -y install "$package"
test "$(rpm -q --queryformat '%{VERSION}' opendeck)" = "$version"
# Fail the build, not OpenDeck's first start, if a library it needs is missing.
if ldd /usr/bin/opendeck | grep -F 'not found'; then exit 1; fi

# The package installs its copy of the Stream Deck rules in /etc, where it
# replaces Monolith's copy of the same name in /usr/lib (see udev(7)). OpenDeck
# 2.14.1's copy lacks the Stream Deck MK.2 Module with scissor keys (00e4), so
# keep Monolith's, and fail once the package lists a device it does not.
product_ids() { sed -n 's/.*idProduct}=="\([0-9a-f]*\)".*/\1/p' "$1" | sort -u; }
if [ ! -f "$package_rules" ]; then
  printf 'OpenDeck no longer installs %s; check where its Stream Deck rules went\n' "$package_rules" >&2
  exit 1
fi
missing="$(comm -13 <(product_ids "$monolith_rules") <(product_ids "$package_rules"))"
if [ -n "$missing" ]; then
  printf 'Update %s from upstream; OpenDeck also lists:\n%s\n' "$monolith_rules" "$missing" >&2
  exit 1
fi
rm -f -- "$package_rules"

install -d "$license_dir"
install -m0644 "$temporary_dir/LICENSE.md" "$license_dir/LICENSE.md"
printf '%s\n' \
  'OpenDeck (upstream RPM)' \
  "Release: https://github.com/nekename/OpenDeck/releases/tag/v$version" \
  "Source: https://github.com/nekename/OpenDeck/tree/$revision" \
  "Package: $url" \
  "Package SHA256: $checksum" \
  > "$license_dir/SOURCE"
