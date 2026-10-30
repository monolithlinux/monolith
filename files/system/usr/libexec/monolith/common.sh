# shellcheck shell=bash
# The scripts that source this use its variables.
# shellcheck disable=SC2034

# Shared helpers for Monolith's system commands (status, scheduler, rollback,
# edition, report-bug). Sourced, not run.
#
# Tests point these at stand-ins:
#   MONOLITH_SYSFS       /sys
#   MONOLITH_OS_RELEASE  /usr/lib/os-release
#   MONOLITH_REGISTRY    https://ghcr.io
#   MONOLITH_MOK_KEY     Monolith's Secure Boot key (DER)

MONOLITH_SYSFS="${MONOLITH_SYSFS:-/sys}"
MONOLITH_OS_RELEASE="${MONOLITH_OS_RELEASE:-/usr/lib/os-release}"
MONOLITH_REGISTRY="${MONOLITH_REGISTRY:-https://ghcr.io}"
MONOLITH_MOK_KEY="${MONOLITH_MOK_KEY:-/etc/pki/akmods/certs/akmods-monolith.der}"
# Keep in sync with ALL in .github/workflows/pick-editions.sh.
MONOLITH_EDITIONS=(kde kde-nvidia)
MONOLITH_IMAGE_PREFIX=ghcr.io/monolithlinux

BOLD="" DIM="" RESET="" ACCENT="" GOOD="" WARN=""
if [[ -t 1 && -z ${NO_COLOR-} && ${TERM:-dumb} != dumb ]]; then
    BOLD=$'\e[1m' DIM=$'\e[2m' RESET=$'\e[0m' ACCENT=$'\e[94m' GOOD=$'\e[32m' WARN=$'\e[33m'
fi

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

# ask QUESTION: yes or no on the terminal. Without a terminal the answer is no,
# unless MONOLITH_YES=1 (set by --yes).
ask() {
    local answer
    [[ ${MONOLITH_YES-} == 1 ]] && return 0
    [[ -t 0 ]] || return 1
    read -r -p "$1 [y/N] " answer || return 1
    [[ $answer == [yY] || $answer == [yY][eE][sS] ]]
}

os_pretty_name() {
    local PRETTY_NAME=""
    # shellcheck disable=SC1090
    [[ -r $MONOLITH_OS_RELEASE ]] && . "$MONOLITH_OS_RELEASE"
    printf '%s\n' "${PRETTY_NAME:-Monolith}"
}

# load_image_info: read the deployments from rpm-ostree. Sets
#   IMAGE_REF       container reference of the running build, such as
#                   ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest
#   IMAGE_VERSION   its version, such as 44.20261029.0
#   IMAGE_SIGNED    yes, no, or unknown
#   IMAGE_NAME      ghcr.io/monolithlinux/kde
#   IMAGE_EDITION   kde (empty when the image isn't Monolith's)
#   IMAGE_TAG       latest, a date such as 20261029, or pr-52-44
#   NEXT_VERSION    the build the next restart starts, when that isn't this one
#   STAGED          yes when an update is downloaded and waits for a restart
#   PREVIOUS_VERSION  the build that ran before this one, if still installed
#   LAYERED         packages added with rpm-ostree install, space-separated
load_image_info() {
    local json fields sep=$'\x1f'
    json="$(timeout 30 rpm-ostree status --json 2>/dev/null)" || return 1
    # A unit separator keeps empty fields, which a tab would merge.
    fields="$(jq -r '
        def ref: .["container-image-reference"] // "";
        ([.deployments[] | select(.booted)][0] // {}) as $booted
        | (.deployments | map(.booted) | index(true)) as $at
        | .deployments[0] as $next
        | [ ($booted | ref),
            ($booted.version // ""),
            (if $next.booted then "" else ($next.version // "") end),
            (if any(.deployments[]; .staged) then "yes" else "no" end),
            (if $at == null then "" else (.deployments[$at + 1].version // "") end),
            (($booted["requested-packages"] // []) + ($booted["requested-local-packages"] // []) | join(" "))
          ] | join("\u001f")' <<<"$json")" || return 1
    IFS="$sep" read -r IMAGE_REF IMAGE_VERSION NEXT_VERSION STAGED PREVIOUS_VERSION LAYERED <<<"$fields"
    parse_image_ref "$IMAGE_REF"
}

parse_image_ref() {
    local ref="$1" image
    IMAGE_SIGNED=unknown
    case "$ref" in
        ostree-image-signed:*) IMAGE_SIGNED=yes ;;
        ostree-unverified-*) IMAGE_SIGNED=no ;;
    esac
    image="${ref##*docker://}"
    image="${image#ostree-unverified-registry:}"
    image="${image%%@*}"
    IMAGE_NAME="${image%:*}"
    IMAGE_TAG=""
    [[ $image == *:* && ${image##*:} != */* ]] && IMAGE_TAG="${image##*:}"
    [[ $IMAGE_TAG ]] || IMAGE_NAME="$image"
    IMAGE_EDITION=""
    if [[ $IMAGE_NAME == "$MONOLITH_IMAGE_PREFIX"/* ]]; then
        IMAGE_EDITION="${IMAGE_NAME##*/}"
    fi
}

# build_date VERSION: 44.20261029.0 -> 20261029
build_date() {
    local date="${1#*.}"
    date="${date%%.*}"
    [[ $date =~ ^[0-9]{8}$ ]] && printf '%s\n' "$date"
}

# nice_date YYYYMMDD -> Thu, Oct 29, 2026
nice_date() {
    date -d "$1" '+%a, %b %-d, %Y' 2>/dev/null || printf '%s\n' "$1"
}

# rebase_to EDITION TAG: switch to a signed Monolith image. Nothing changes
# until the next restart.
rebase_to() {
    rpm-ostree rebase "ostree-image-signed:docker://$MONOLITH_IMAGE_PREFIX/$1:$2"
}

# The registry API needs a token even for public images.
registry_token() {
    curl -fsS --max-time 20 "$MONOLITH_REGISTRY/token?scope=repository:monolithlinux/$1:pull" |
        jq -er .token
}

# registry_dates EDITION: the dated builds, newest first.
registry_dates() {
    local token
    token="$(registry_token "$1")" || return 1
    curl -fsS --max-time 30 -H "Authorization: Bearer $token" \
        "$MONOLITH_REGISTRY/v2/monolithlinux/$1/tags/list?n=10000" |
        jq -r '.tags[] | select(test("^[0-9]{8}$"))' | sort -r
}

# has_nvidia_gpu: is there an NVIDIA display controller?
has_nvidia_gpu() {
    local device class
    for device in "$MONOLITH_SYSFS"/bus/pci/devices/*; do
        [[ -r $device/vendor && -r $device/class ]] || continue
        [[ $(<"$device/vendor") == 0x10de ]] || continue
        class="$(<"$device/class")"
        [[ $class == 0x03* ]] && return 0
    done
    return 1
}
