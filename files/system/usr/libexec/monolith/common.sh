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
# ostree keeps this file while a downloaded update waits for the next restart.
MONOLITH_STAGED="${MONOLITH_STAGED:-/run/ostree/staged-deployment}"
# monolith update touches this after a full run, for the login reminder.
MONOLITH_UPDATE_RECORD="${XDG_STATE_HOME:-$HOME/.local/state}/monolith/update-reminder/last-update"
# Keep in sync with ALL in .github/workflows/pick-editions.sh.
MONOLITH_EDITIONS=(kde kde-nvidia)
MONOLITH_IMAGE_PREFIX=ghcr.io/monolithlinux

BOLD="" DIM="" RESET="" ACCENT="" GOOD="" WARN=""
if [[ -t 1 && -z ${NO_COLOR-} && ${TERM:-dumb} != dumb ]]; then
    BOLD=$'\e[1m' DIM=$'\e[2m' RESET=$'\e[0m' ACCENT=$'\e[94m' GOOD=$'\e[32m' WARN=$'\e[33m'
fi

# Symbols and the Monolith mark need a UTF-8 terminal other than the Linux
# console, whose font lacks most of them. Elsewhere they fall back to ASCII.
MONOLITH_UNICODE=0
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *[Uu][Tt][Ff]-8* | *[Uu][Tt][Ff]8*)
        case "${TERM:-dumb}" in
            dumb | linux | vt[0-9]*) ;;
            *) MONOLITH_UNICODE=1 ;;
        esac
        ;;
esac
if ((MONOLITH_UNICODE)); then
    SYM_OK="✓" SYM_WARN="!" SYM_UP="↑" SYM_NOTE="•" SYM_TO="→" SYM_DOT="·"
else
    SYM_OK="+" SYM_WARN="!" SYM_UP="^" SYM_NOTE="-" SYM_TO="->" SYM_DOT="-"
fi

# The Monolith mark, in fastfetch's logo format: $1 starts the outer slabs and
# $2 the middle one. monolith-tiny.txt, -small and -medium are 2, 3 and 6 rows
# tall; monolith.txt is the large one fastfetch shows.
MONOLITH_LOGOS="${MONOLITH_LOGOS:-${BASH_SOURCE[0]%/*}/../../share/fastfetch/logos}"
MARK_OUTER="" MARK_MIDDLE=""
if [[ $RESET ]]; then
    # The terminal's own text color keeps the mark visible on light themes too.
    MARK_OUTER=$'\e[39m' MARK_MIDDLE=$'\e[94m'
fi

# monolith_columns: set MONOLITH_COLUMNS and MONOLITH_LINES to the terminal's
# size, or 80 by 24.
monolith_columns() {
    local rows="" cols=""
    read -r rows cols < <({ stty size </dev/tty; } 2>/dev/null) || true
    MONOLITH_COLUMNS=80 MONOLITH_LINES=24
    if [[ ${COLUMNS-} =~ ^[1-9][0-9]*$ ]]; then
        MONOLITH_COLUMNS=$COLUMNS
    elif [[ $cols =~ ^[1-9][0-9]*$ ]]; then
        MONOLITH_COLUMNS=$cols
    fi
    if [[ ${LINES-} =~ ^[1-9][0-9]*$ ]]; then
        MONOLITH_LINES=$LINES
    elif [[ $rows =~ ^[1-9][0-9]*$ ]]; then
        MONOLITH_LINES=$rows
    fi
}

# mark_fits SIZE: can the mark of SIZE go beside text here? Only on a UTF-8
# terminal with room for it. MONOLITH_MARK=1 or 0 overrides that.
MARK_ROWS=()
MARK_WIDTH=0
mark_fits() {
    local file="$MONOLITH_LOGOS/monolith-$1.txt" line plain
    MARK_ROWS=()
    MARK_WIDTH=0
    case "${MONOLITH_MARK-}" in
        0) return 1 ;;
        1) ((MONOLITH_UNICODE)) || return 1 ;;
        *) [[ -t 1 ]] && ((MONOLITH_UNICODE)) || return 1 ;;
    esac
    [[ -r $file ]] || return 1
    mapfile -t MARK_ROWS <"$file"
    for line in "${MARK_ROWS[@]}"; do
        plain="${line//\$[12]/}"
        ((${#plain} > MARK_WIDTH)) && MARK_WIDTH=${#plain}
    done
    monolith_columns
    ((MONOLITH_COLUMNS >= MARK_WIDTH + 50)) || { MARK_ROWS=(); return 1; }
}

# beside SIZE LINE...: print the LINEs with the mark of SIZE on their left when
# it fits, and on their own otherwise.
beside() {
    local size="$1" i count line plain
    shift
    local lines=("$@")
    mark_fits "$size" || MARK_ROWS=()
    count=${#lines[@]}
    ((${#MARK_ROWS[@]} > count)) && count=${#MARK_ROWS[@]}
    for ((i = 0; i < count; i++)); do
        if ((${#MARK_ROWS[@]})); then
            line="${MARK_ROWS[i]-}"
            plain="${line//\$[12]/}"
            line="${line//\$1/$MARK_OUTER}"
            line="${line//\$2/$MARK_MIDDLE}"
            if [[ -z ${lines[i]-} ]]; then
                printf ' %s%s\n' "$line" "$RESET"
                continue
            fi
            printf ' %s%s%*s   ' "$line" "$RESET" $((MARK_WIDTH - ${#plain})) ''
        fi
        printf '%s\n' "${lines[i]-}"
    done
}

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

# os_release: set OS_NAME and OS_VERSION, the build, from os-release. Quick
# enough for every new terminal, unlike rpm-ostree.
os_release() {
    local PRETTY_NAME="" OSTREE_VERSION=""
    # shellcheck disable=SC1090
    [[ -r $MONOLITH_OS_RELEASE ]] && . "$MONOLITH_OS_RELEASE"
    OS_NAME="${PRETTY_NAME:-Monolith}"
    OS_VERSION="$OSTREE_VERSION"
}

# full_update_days: how many days ago `monolith update` last finished. Fails
# when it hasn't run yet.
full_update_days() {
    [[ -e $MONOLITH_UPDATE_RECORD ]] || return 1
    printf '%s\n' $((($(date +%s) - $(stat -c %Y -- "$MONOLITH_UPDATE_RECORD")) / 86400))
}

# days_ago DAYS: today, yesterday or "3 days ago".
days_ago() {
    case "$1" in
        0) printf 'today\n' ;;
        1) printf 'yesterday\n' ;;
        *) printf '%s days ago\n' "$1" ;;
    esac
}

# load_image_info: read the deployments from rpm-ostree. Sets
#   IMAGE_REF       container reference of the running build, such as
#                   ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest
#   IMAGE_VERSION   its version, such as 44.20261029.0
#   IMAGE_SIGNED    yes, no, or unknown
#   IMAGE_NAME      ghcr.io/monolithlinux/kde
#   IMAGE_EDITION   kde (empty when the image isn't Monolith's)
#   IMAGE_TAG       latest, a date such as 20261029, or pr-52-44
#   PENDING         yes when another build waits for the next restart: a
#                   downloaded update, or a switch that monolith rollback,
#                   monolith edition or rpm-ostree rebase set up
#   NEXT_REF, NEXT_VERSION, NEXT_SIGNED, NEXT_NAME, NEXT_EDITION, NEXT_TAG
#                   the same about that waiting build; empty when PENDING is no
#   STAGED          yes when an update is downloaded and waits for a restart
#   PREVIOUS_VERSION  the build that ran before this one, if still installed
#   LAYERED         packages added with rpm-ostree install, space-separated
# rpm-ostree lists the deployment the next restart starts first, so anything
# listed before the running one is waiting.
load_image_info() {
    local json fields sep=$'\x1f'
    json="$(timeout 30 rpm-ostree status --json 2>/dev/null)" || return 1
    # A unit separator keeps empty fields, which a tab would merge.
    fields="$(jq -r '
        def ref: .["container-image-reference"] // "";
        ([.deployments[] | select(.booted)][0] // {}) as $booted
        | (.deployments | map(.booted) | index(true)) as $at
        | (.deployments[0] // {booted: true}) as $first
        | (if $first.booted then null else $first end) as $next
        | [ ($booted | ref),
            ($booted.version // ""),
            ($next.version // ""),
            (if any(.deployments[]; .staged) then "yes" else "no" end),
            (if $at == null then "" else (.deployments[$at + 1].version // "") end),
            (($booted["requested-packages"] // []) + ($booted["requested-local-packages"] // []) | join(" ")),
            (if $next == null then "no" else "yes" end),
            (if $next == null then "" else ($next | ref) end)
          ] | join("\u001f")' <<<"$json")" || return 1
    IFS="$sep" read -r IMAGE_REF IMAGE_VERSION NEXT_VERSION STAGED PREVIOUS_VERSION LAYERED PENDING NEXT_REF <<<"$fields"
    parse_image_ref "$IMAGE_REF" IMAGE
    parse_image_ref "$NEXT_REF" NEXT
}

# parse_image_ref REF PREFIX: split a container reference into PREFIX_SIGNED,
# PREFIX_NAME, PREFIX_EDITION and PREFIX_TAG.
parse_image_ref() {
    local ref="$1" prefix="$2" image signed=unknown name tag="" edition=""
    case "$ref" in
        ostree-image-signed:*) signed=yes ;;
        ostree-unverified-*) signed=no ;;
    esac
    image="${ref##*docker://}"
    image="${image#ostree-unverified-registry:}"
    image="${image%%@*}"
    name="${image%:*}"
    if [[ $image == *:* && ${image##*:} != */* ]]; then
        tag="${image##*:}"
    else
        name="$image"
    fi
    if [[ $name == "$MONOLITH_IMAGE_PREFIX"/* ]]; then
        edition="${name##*/}"
    fi
    printf -v "${prefix}_SIGNED" '%s' "$signed"
    printf -v "${prefix}_NAME" '%s' "$name"
    printf -v "${prefix}_EDITION" '%s' "$edition"
    printf -v "${prefix}_TAG" '%s' "$tag"
}

# describe_build TAG: a build of an edition in plain words.
describe_build() {
    if [[ $1 == latest ]]; then
        printf 'the newest build\n'
    elif [[ $1 =~ ^[0-9]{8}$ ]]; then
        printf 'the build from %s\n' "$(nice_date "$1")"
    elif [[ $1 =~ ^pr-([0-9]+)(-[0-9]+)?$ ]]; then
        printf 'the test build of pull request #%s\n' "${BASH_REMATCH[1]}"
    else
        printf 'the build tagged %s\n' "${1:-?}"
    fi
}

# next_build: the build that waits for the next restart, in plain words.
next_build() {
    if [[ -z $NEXT_EDITION ]]; then
        printf '%s\n' "${NEXT_NAME:-another system}${NEXT_TAG:+:$NEXT_TAG}"
    elif [[ $NEXT_EDITION != "$IMAGE_EDITION" ]]; then
        printf '%s of the %s edition\n' "$(describe_build "$NEXT_TAG")" "$NEXT_EDITION"
    else
        describe_build "$NEXT_TAG"
    fi
}

# switch_plan EDITION TAG: set SWITCH_PLAN to what it takes for this computer
# to run EDITION:TAG after its next restart.
#   same     it runs that build, and nothing else waits for a restart
#   waiting  that build already waits for the next restart
#   cancel   it runs that build, but another one waits: cancel that one
#   switch   switch to it
# A build that waits decides where the next restart lands, so it comes first.
switch_plan() {
    local running=no
    if [[ $IMAGE_EDITION == "$1" && $IMAGE_TAG == "$2" ]]; then
        running=yes
    fi
    if [[ $PENDING == yes && $NEXT_EDITION == "$1" && $NEXT_TAG == "$2" ]]; then
        SWITCH_PLAN=waiting
        [[ $running == no ]] || SWITCH_PLAN=same
    elif [[ $running == no ]]; then
        SWITCH_PLAN=switch
    elif [[ $PENDING == yes ]]; then
        SWITCH_PLAN=cancel
    else
        SWITCH_PLAN=same
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
# until the next restart. rpm-ostree builds on the build that waits for a
# restart when there is one, and refuses a switch to the image that build
# already follows ("Old and new refs are equal"), so check switch_plan first.
rebase_to() {
    rpm-ostree rebase "ostree-image-signed:docker://$MONOLITH_IMAGE_PREFIX/$1:$2"
}

# cancel_pending: drop the build that waits for the next restart, so this
# computer keeps the one it runs. rpm-ostree cleanup -p is rpm-ostree's own
# undo for that: it removes only the build that waits and downloads nothing.
cancel_pending() {
    local failed=0
    rpm-ostree cleanup -p || failed=1
    # What waits afterwards decides: rpm-ostree leaves a pinned build in place
    # without failing, and can fail while tidying up after it dropped one.
    load_image_info || return "$failed"
    [[ $PENDING == no ]]
}

# return_to_latest EDITION: back to the newest build of EDITION and its daily
# updates, from an older build, a test build, or a switch to one of those that
# still waits for a restart. monolith rollback latest and monolith pr latest
# both end up here.
return_to_latest() {
    local edition="$1" waiting
    switch_plan "$edition" latest
    case "$SWITCH_PLAN" in
        same)
            echo "You're already on the newest build, and updates are on."
            ;;
        waiting)
            echo "The newest build already waits for your next restart, and daily updates"
            echo "come back with it. Restart to start it."
            ;;
        cancel)
            waiting="$(next_build)"
            printf 'Cancelling the switch to %s.\n' "$waiting"
            cancel_pending || die "That didn't work: your next restart still starts $waiting."
            printf '\nDone. You stay on the newest build, and daily updates continue.\n'
            ;;
        switch)
            ask "Return to the newest $edition build and daily updates?" || { echo "Nothing changed."; return 0; }
            rebase_to "$edition" latest
            printf '\nDone. Restart to start the newest build.\n'
            ;;
    esac
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
