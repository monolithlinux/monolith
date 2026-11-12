#!/usr/bin/env bash
# One app's install, by the catalog's own code, up to the step that would
# install or run something. check-app-downloads.py runs this for every app the
# catalog installs with bash, in a home directory made for the check:
#
#   check-app-downloads.sh CATALOG install APP
#   check-app-downloads.sh CATALOG newest APP
#
# CATALOG is the catalog without its last line, `main "$@"`: sourcing it
# defines its functions and runs nothing. `install` then does what
# `monolith install APP` does for one app, so the catalog itself picks the
# release, downloads it and checks it. The commands below take the place of
# the programs that would install it after that. Each writes what the catalog
# asked of it to $MONOLITH_CHECK_DIR/record, one line with tabs between the
# fields, and the one that would install something keeps the file it was given
# as $MONOLITH_CHECK_DIR/kept and ends the run with status 120. `newest`
# prints the version the catalog's update would compare an installer's app to.
#
# Nothing that was downloaded is run: not a program, not an install script.

usage="usage: check-app-downloads.sh CATALOG install|newest APP"
catalog="${1:?$usage}" action="${2:?$usage}" app="${3:?$usage}"
: "${MONOLITH_CHECK_DIR:?}"

# Only requests to GitHub's API are signed in, so nothing else gets the token.
github_token="${GITHUB_TOKEN-}"
unset GITHUB_TOKEN
# What the environment had before the catalog set anything for an installer.
mapfile -t exported < <(compgen -e | sort)

# shellcheck source=/dev/null
. "$catalog"

record() {
    local IFS=$'\t'
    printf '%s\n' "$*" >>"$MONOLITH_CHECK_DIR/record"
}

# stop [FILE]: the catalog got as far as installing. Keep FILE for the check.
stop() {
    if [ "$#" -gt 0 ]; then
        mv -- "$1" "$MONOLITH_CHECK_DIR/kept"
    fi
    record stopped
    exit 120
}

no_stand_in() {
    echo "The check has no stand-in for: $*" >&2
    exit 121
}

# The real curl. It records the address, which the catalog gives last, and
# where the answer goes, and names the address when it can't be had.
curl() {
    local url="${*: -1}" output="" header=() status=0 index
    for ((index = 1; index < $#; index++)); do
        if [ "${!index}" = --output ]; then
            index=$((index + 1))
            output="${!index}"
        fi
    done
    record curl "$url" "$output"
    if [[ $url == https://api.github.com/* && -n $github_token ]]; then
        header=(--header "Authorization: Bearer $github_token")
    fi
    command curl "${header[@]}" "$@" 2>"$MONOLITH_CHECK_DIR/curl-error" || status=$?
    if [ "$status" -ne 0 ]; then
        echo "Could not get $url: $(tail -n 1 "$MONOLITH_CHECK_DIR/curl-error")" >&2
    fi
    return "$status"
}

# Flatpak on a computer that has none of the catalog's apps and no remote of
# the user's.
flatpak() {
    record flatpak "$@"
    case "${1-}" in
        info) return 1 ;;
        remotes | remote-add) return 0 ;;
        install)
            if [ -f "${*: -1}" ]; then stop "${*: -1}"; else stop; fi
            ;;
        *) no_stand_in flatpak "$@" ;;
    esac
}

# install -m0755 PROGRAM DESTINATION: the catalog downloaded a program and its
# checksum matched. It would run the program next.
install() {
    local program="${*: -2:1}"
    record install "$@"
    if [ ! -f "$program" ]; then
        echo "install: cannot stat '$program': No such file or directory" >&2
        return 1
    fi
    stop "$program"
}

# sh SCRIPT [ARGUMENT...] or bash SCRIPT [ARGUMENT...]: a publisher's install
# script. Its settings are the variables the catalog exported for it.
installer() {
    [ -f "${2-}" ] || no_stand_in "$@"
    record installer "$1" "${@:3}"
    # shellcheck disable=SC2046  # one field for each name
    record settings $(comm -13 <(printf '%s\n' "${exported[@]}") <(compgen -e | sort))
    stop "$2"
}

sh() { installer sh "$@"; }
bash() { installer bash "$@"; }

# env [-u NAME]... [NAME=VALUE]... sh|bash SCRIPT [ARGUMENT...]
env() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -u) shift 2 ;;
            *=*) export "${1?}"; shift ;;
            *) break ;;
        esac
    done
    case "${1-}" in
        sh | bash) installer "$@" ;;
        *) no_stand_in env "$@" ;;
    esac
}

# The catalog asks for a password only after an install, which the check
# never gets to.
sudo() { no_stand_in sudo "$@"; }

case "$action" in
    install) main __action install "$app" ;;
    newest) latest_installer_version "$app" ;;
    *) echo "$usage" >&2; exit 2 ;;
esac
