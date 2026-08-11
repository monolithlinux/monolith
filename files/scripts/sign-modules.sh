#!/usr/bin/env bash
# Sign out-of-tree modules with the Monolith MOK.
# Adapted from BlueBuild's signmodules.sh with zstd support.
#
# Inputs (provided by the recipe's `script` module):
#   MODULE_NAME          - module subdir under .../extra to sign (env, e.g. nvidia)
#   PUBLIC_KEY_DER_PATH  - the cert baked into the image (env)
#   /tmp/certs/private_key.priv - MOK private key (mounted build secret)
#
set -oue pipefail

MODULE_NAME="${MODULE_NAME:-${1-}}"
if [ -z "$MODULE_NAME" ]; then
  echo "MODULE_NAME is empty. Exiting..." >&2
  exit 1
fi

KERNEL_VERSION="$(ls /usr/lib/modules)"

PUBLIC_KEY_CRT_PATH="/tmp/certs/public_key.crt"
PRIVATE_KEY_PATH="/tmp/certs/private_key.priv"
SIGNING_KEY="/tmp/certs/signing_key.pem"
openssl x509 -inform DER -in "$PUBLIC_KEY_DER_PATH" -out "$PUBLIC_KEY_CRT_PATH"
cat "$PRIVATE_KEY_PATH" <(echo) "$PUBLIC_KEY_CRT_PATH" >> "$SIGNING_KEY"

SIGN_FILE="/usr/src/kernels/${KERNEL_VERSION}/scripts/sign-file"

sign_one() {
  local module="$1"
  openssl cms -sign -signer "$SIGNING_KEY" -binary -in "$module" -outform DER \
    -out "${module}.cms" -nocerts -noattr -nosmimecap
  "$SIGN_FILE" -s "${module}.cms" sha256 "$PUBLIC_KEY_CRT_PATH" "$module"
  rm -f "${module}.cms"
  /bin/bash ./sign-check.sh "$KERNEL_VERSION" "$module" "$PUBLIC_KEY_CRT_PATH"
}

signed_any=0
shopt -s nullglob
for module in /usr/lib/modules/"${KERNEL_VERSION}"/extra/"${MODULE_NAME}"/*.ko*; do
  signed_any=1
  case "$module" in
    *.ko.xz)
      xz -d "$module";          sign_one "${module%.xz}";  xz -C crc32 -f "${module%.xz}" ;;
    *.ko.zst)
      zstd -q -d --rm "$module"; sign_one "${module%.zst}"; zstd -q --rm -f "${module%.zst}" ;;
    *.ko.gz)
      gzip -d "$module";        sign_one "${module%.gz}";  gzip -9f "${module%.gz}" ;;
    *.ko)
      sign_one "$module" ;;
    *)
      echo "skipping unrecognized module file: $module" >&2 ;;
  esac
done

if [ "$signed_any" -eq 0 ]; then
  echo "no $MODULE_NAME modules found under /usr/lib/modules/${KERNEL_VERSION}/extra/${MODULE_NAME}" >&2
  exit 1
fi

depmod -a "$KERNEL_VERSION"
