#!/usr/bin/env bash
# Sign the CachyOS kernel with the Monolith MOK used by NVIDIA modules.
#
# Inputs (provided by the recipe's `script` module):
#   PUBLIC_KEY_DER_PATH  - the cert baked into the image (env)
#   /tmp/certs/private_key.priv - MOK private key (mounted build secret)

set -oue pipefail

KERNEL_VERSION="$(ls /usr/lib/modules)"
if [ "$(printf '%s\n' "$KERNEL_VERSION" | wc -l)" -ne 1 ]; then
  echo "expected exactly one kernel in /usr/lib/modules, found: $KERNEL_VERSION" >&2
  exit 1
fi

PRIVATE_KEY_PATH="/tmp/certs/private_key.priv"
PUBLIC_KEY_CRT_PATH="/tmp/certs/public_key.crt"
openssl x509 -inform DER -in "$PUBLIC_KEY_DER_PATH" -out "$PUBLIC_KEY_CRT_PATH"

# Install signing tools only for this build step.
dnf install -y --setopt=install_weak_deps=False sbsigntools libfaketime

vmlinuz="/usr/lib/modules/${KERNEL_VERSION}/vmlinuz"

# Pin PKCS#7 signingTime to the RPM mtime for reproducible layers.
sign_epoch="$(stat -c %Y "$vmlinuz")"
sign_date="$(date -u -d "@${sign_epoch}" '+%Y-%m-%d %H:%M:%S')"
faketime "$sign_date" sbsign --key "$PRIVATE_KEY_PATH" --cert "$PUBLIC_KEY_CRT_PATH" \
  "$vmlinuz" --output "${vmlinuz}.signed"
mv "${vmlinuz}.signed" "$vmlinuz"
# sbsign creates a new inode, so restore its original mtime too.
touch -d "@${sign_epoch}" "$vmlinuz"

sbverify --cert "$PUBLIC_KEY_CRT_PATH" "$vmlinuz"

dnf remove -y sbsigntools libfaketime || true
