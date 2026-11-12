#!/usr/bin/env bash
set -euo pipefail

version=2.14.2
revision=3ddecc1ce8952a423916cf066724356267b97410
license_checksum=cc5470feed66192387f06ae93aed0bd4fefae5a5fd7c1e54a4acf4ea64f1028b
architecture="$(uname -m)"
case "$architecture" in
  x86_64) checksum=cbe867f9f235c96259cc241546b4a9e9bc7d2d77b106e76e4a273de7c471ad6e ;;
  aarch64) checksum=f495b487662d482c5ab9f96039904e9e3498feb28eeecad1e3e55d38e00dc3b8 ;;
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

# OpenDeck links against OpenSSL 3, but its RPM doesn't say so, and Fedora 45
# moved to OpenSSL 4. Fedora's openssl3-libs keeps the OpenSSL 3 libraries.
dnf5 -y install "$package" openssl3-libs
test "$(rpm -q --queryformat '%{VERSION}' opendeck)" = "$version"
# Fail the build, not OpenDeck's first start, if a library it needs is missing.
if ldd /usr/bin/opendeck | grep -F 'not found'; then exit 1; fi

# The package installs its copy of the Stream Deck rules in /etc, where it
# replaces Monolith's copy of the same name in /usr/lib (see udev(7)). The
# package's copy has been behind before: 2.14.1's lacked the Stream Deck MK.2
# Module with scissor keys (00e4). 2.14.2's is the same file as Monolith's.
# Keep Monolith's, and fail once the package lists a device it does not.
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
