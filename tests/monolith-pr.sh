#!/usr/bin/env bash
# monolith pr: the list of open pull requests and their test builds, switching
# to one and back, and what it says when GitHub or the registry can't answer.
# It runs against stand-ins for rpm-ostree, GitHub's API, the registry and the
# clock, and sees no other programs than those and a few basics.
# Code for jq and for stand-in programs is single-quoted on purpose.
# shellcheck disable=SC2016
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
libexec="$repo_root/files/system/usr/libexec/monolith"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
# These take text of several lines too, which grep would read as a list of
# patterns that each match on their own.
has() { [[ $1 == *"$2"* ]] || fail "expected '$2' in:"$'\n'"$1"; }
lacks() { [[ $1 != *"$2"* ]] || fail "did not expect '$2' in:"$'\n'"$1"; }

bin="$test_root/bin"
utils="$test_root/utils"
log="$test_root/log"
github="$test_root/github"
registry="$test_root/registry"
mkdir -p "$bin" "$utils" "$log"
# The basics the command and the stand-ins use. Nothing else is on PATH, so a
# program the tests don't control can't run by accident.
for tool in cat dirname grep jq mv readlink rm sed stty timeout wc; do
    ln -s "$(command -v "$tool")" "$utils/$tool"
done
script_program="$(command -v script)"

# The clock stands still, so "2 hours ago" stays true however long a test
# takes. date +%s is how the command reads it; everything else is the real
# date.
export TZ=UTC
now="$(date -d '2026-11-12 14:00:00' +%s)"
cat >"$bin/date" <<EOF
#!/bin/bash
[ "\$*" != +%s ] || { echo $now; exit 0; }
exec "$(command -v date)" "\$@"
EOF

# rpm-ostree keeps its deployments in status.json. rebase and cleanup -p change
# them the way rpm-ostree 2026.3 does: a rebase builds on the deployment listed
# first, which is the one waiting for a restart when there is one, refuses the
# image that deployment already follows, and stages a new deployment in place
# of the one that waited. cleanup -p drops what waits.
cat >"$bin/rpm-ostree" <<'EOF'
#!/bin/bash
root="${0%/*}/.."
status="$root/status.json"
change() { jq "$@" "$status" >"$status.new" && mv "$status.new" "$status"; }
if [ "$1" = status ]; then
    [ -e "$status" ] || exit 1
    cat "$status"
    exit 0
fi
printf '%s\n' "$*" >>"$root/log/rpm-ostree"
case "$1" in
    rebase)
        if [ "$(jq -r '.deployments[0]["container-image-reference"]' "$status")" = "$2" ]; then
            echo "error: Old and new refs are equal: $2" >&2
            exit 1
        fi
        change --arg ref "$2" '(.deployments | map(.booted) | index(true)) as $at
            | .deployments |= [{booted: false, staged: true, version: "45.20261112.0",
                "container-image-reference": $ref}] + .[$at:]'
        ;;
    cleanup)
        [ "$2" = -p ] || exit 0
        change '(.deployments | map(.booted) | index(true)) as $at | .deployments |= .[$at:]'
        ;;
esac
EOF

# curl answers for GitHub's API and for the registry from the files under
# github/ and registry/. What it copies from the real ones:
# - GitHub: 404 with a message for what isn't there; 403 or 429 with
#   x-ratelimit-remaining: 0 and the reset time when the hour's requests are
#   used up (GITHUB_LIMIT), or 403 with retry-after when it wants a pause
#   (GITHUB_RETRY).
# - ghcr.io: 401 without the token it hands out; tags oldest first and at most
#   TAG_PAGE at a time, with a link to the next page; 404 for a tag that names
#   an index unless the request accepts OCI indexes; blobs behind a redirect,
#   which only -L follows.
# -w is filled in the way curl does, for the status and the headers asked for.
cat >"$bin/curl" <<'EOF'
#!/bin/bash
# A header's value goes into -w's format as it is, & included.
shopt -u patsub_replacement 2>/dev/null || true
root="${0%/*}/.."
url="${*: -1}"
format=""
for ((i = 1; i < $#; i++)); do
    [ "${!i}" != -w ] || { next=$((i + 1)); format="${!next}"; }
done
# answer STATUS BODY [HEADER=VALUE...]
answer() {
    local status="$1" header name pair
    printf '%s' "$2"
    shift 2
    format="${format//\\n/$'\n'}"
    format="${format//'%{http_code}'/$status}"
    for name in x-ratelimit-remaining x-ratelimit-reset retry-after link; do
        header=""
        for pair in "$@"; do
            [ "${pair%%=*}" != "$name" ] || header="${pair#*=}"
        done
        format="${format//"%header{$name}"/$header}"
    done
    printf '%s' "$format"
}
refuse() {
    echo "curl: (22) The requested URL returned error: $1" >&2
    exit 22
}

api="${MONOLITH_GITHUB_API:-https://api.github.com}/repos/monolithlinux/monolith/"
case "$url" in
    "$api"*)
        printf '%s\n' "${url#"$api"}" >>"$root/log/github"
        case "$*" in *[Aa]uthorization*) echo "sent credentials" >>"$root/log/github" ;; esac
        if [ -n "${GITHUB_DOWN-}" ]; then
            echo "curl: (6) Could not resolve host: api.github.com" >&2
            exit 6
        fi
        left="x-ratelimit-remaining=41"
        reset="x-ratelimit-reset=${GITHUB_RESET:-1794495600}"
        # GITHUB_LIMIT_AFTER: the hour's requests run out after that many.
        asked="$(grep -c . "$root/log/github")"
        if [ -n "${GITHUB_LIMIT-}" ] && [ "$asked" -gt "${GITHUB_LIMIT_AFTER:-0}" ]; then
            answer "$GITHUB_LIMIT" '{"message":"API rate limit exceeded for 203.0.113.7. (But here'"'"'s the good news: Authenticated requests get a higher rate limit. Check out the documentation for more details.)","documentation_url":"https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting"}' \
                x-ratelimit-remaining=0 "$reset"
            exit 0
        fi
        if [ -n "${GITHUB_RETRY-}" ]; then
            answer 403 '{"message":"You have exceeded a secondary rate limit. Please wait a few minutes before you try again.","documentation_url":"https://docs.github.com/rest/overview/rate-limits-for-the-rest-api#about-secondary-rate-limits"}' \
                "$left" "$reset" "retry-after=$GITHUB_RETRY"
            exit 0
        fi
        if [ -n "${GITHUB_ANSWERS-}" ]; then
            # GITHUB_ANSWERS: a status and a body for every question.
            answer "${GITHUB_ANSWERS%% *}" "${GITHUB_ANSWERS#* }" "$left" "$reset"
            exit 0
        fi
        case "${url#"$api"}" in
            "pulls?state=open&per_page=100") file="$root/github/open.json" ;;
            pulls/*) file="$root/github/pull-${url##*/}.json" ;;
            commits/*/check-runs\?per_page=100) file="${url%/check-runs*}" file="$root/github/checks-${file##*/}.json" ;;
            *) file=/nonexistent ;;
        esac
        if [ -e "$file" ]; then
            answer 200 "$(cat "$file")" "$left" "$reset"
        else
            answer 404 '{"message":"Not Found","documentation_url":"https://docs.github.com/rest","status":"404"}' \
                "$left" "$reset"
        fi
        exit 0
        ;;
esac

printf '%s\n' "${url#https://ghcr.io}" >>"$root/log/registry"
if [ -n "${REGISTRY_DOWN-}" ]; then
    echo "curl: (7) Failed to connect to ghcr.io port 443" >&2
    exit 7
fi
case "$url" in
    "https://ghcr.io/token?scope=repository:monolithlinux/"*":pull")
        echo '{"token": "anonymous"}'
        exit 0
        ;;
    https://ghcr.io/v2/monolithlinux/*) ;;
    *) refuse 404 ;;
esac
case "$*" in
    *"Authorization: Bearer anonymous"*) ;;
    *) refuse 401 ;;
esac
path="${url#https://ghcr.io/v2/monolithlinux/}"
image="$root/registry/${path%%/*}"
path="${path#*/}"
case "$path" in
    tags/list*)
        [ -e "$image/tags.json" ] || refuse 404
        last="$(sed -n 's/.*[?&]last=\([^&]*\).*/\1/p' <<<"$path")"
        page="$(jq -c --arg last "$last" --argjson size "${TAG_PAGE:-1000}" '
            (if $last == "" then 0 else (.tags | index($last)) + 1 end) as $from
            | {name, tags: .tags[$from:$from + $size], more: ((.tags | length) > $from + $size)}' "$image/tags.json")" ||
            { cat "$image/tags.json"; exit 0; }
        link=""
        [ "$(jq .more <<<"$page")" = false ] ||
            link="</v2/monolithlinux/${image##*/}/tags/list?last=$(jq -r '.tags[-1]' <<<"$page")&n=1000>; rel=\"next\""
        answer 200 "$(jq -c 'del(.more)' <<<"$page")" "link=$link"
        ;;
    manifests/*)
        [ -e "$image/$path" ] || refuse 404
        if jq -e '.manifests' "$image/$path" >/dev/null 2>&1; then
            case "$*" in *application/vnd.oci.image.index.v1+json*) ;; *) refuse 404 ;; esac
        fi
        cat "$image/$path"
        ;;
    blobs/*)
        [ -e "$image/$path" ] || refuse 404
        case " $* " in *" -"[a-zA-Z]*L*" "* | *" --location "*) cat "$image/$path" ;; esac
        ;;
    *) refuse 404 ;;
esac
EOF
chmod +x "$bin"/*

printf 'NAME=Monolith\nPRETTY_NAME="Monolith KDE"\n' >"$test_root/os-release"
export NO_COLOR=1 LC_ALL=C COLUMNS=80 MONOLITH_OS_RELEASE="$test_root/os-release"
unset MONOLITH_YES MONOLITH_GITHUB_API MONOLITH_REGISTRY MONOLITH_MARK TERM_PROGRAM
unset GITHUB_DOWN GITHUB_LIMIT GITHUB_LIMIT_AFTER GITHUB_RESET GITHUB_RETRY GITHUB_ANSWERS REGISTRY_DOWN TAG_PAGE

# Times in the answers are so many minutes before the clock's.
minutes_ago() { date -u -d "@$((now - $1 * 60))" +%Y-%m-%dT%H:%M:%SZ; }
commit_of() { printf '%040d' "$1"; }

# deployments EDITION TAG [BUILT-MINUTES-AGO] [LAYERED]: this computer runs
# that image, built that long ago. With LAYERED, packages were added later, so
# rpm-ostree reports the image's time as the base's.
deployments() {
    jq -n --arg ref "ostree-image-signed:docker://ghcr.io/monolithlinux/$1:$2" \
        --argjson built "$((now - ${3:-1200} * 60))" --arg layered "${4-}" '
        {deployments: [{booted: true, staged: false, version: "45.20261111.0", "container-image-reference": $ref}
            + (if $layered == "" then {timestamp: $built}
               else {timestamp: ($built + 4000), "base-timestamp": $built, "requested-packages": ["htop"]} end)],
         transaction: null}' >"$test_root/status.json"
}
# pending EDITION TAG: a switch to that image waits for the next restart.
pending() {
    jq --arg ref "ostree-image-signed:docker://ghcr.io/monolithlinux/$1:$2" '
        (.deployments | map(.booted) | index(true)) as $at
        | .deployments |= [{booted: false, staged: true, version: "45.20261112.0",
            "container-image-reference": $ref}] + .[$at:]' "$test_root/status.json" >"$test_root/status.json.new"
    mv "$test_root/status.json.new" "$test_root/status.json"
}
waits() {
    jq -r '.deployments[0] | if .booted then "nothing" else .["container-image-reference"] | sub(".*/"; "") end' \
        "$test_root/status.json"
}

# image EDITION TAG BUILT-MINUTES-AGO [plain]: an image in the registry. Its
# tag names an index with an entry for another kind of computer first, as a
# registry may send, or the image itself with "plain". The time has fractions
# of a second, as the real ones do.
image() {
    local dir="$registry/$1" config config_digest manifest manifest_digest
    mkdir -p "$dir/manifests" "$dir/blobs"
    [ -e "$dir/tags.json" ] || jq -n --arg name "monolithlinux/$1" '{name: $name, tags: []}' >"$dir/tags.json"
    jq --arg tag "$2" '.tags |= (. - [$tag]) + [$tag]' "$dir/tags.json" >"$dir/tags.json.new"
    mv "$dir/tags.json.new" "$dir/tags.json"
    config="$(jq -cn --arg created "$(minutes_ago "$3")" --arg tag "$2" '{architecture: "amd64", os: "linux",
        created: ($created | sub("Z$"; ".52361437Z")), config: {Labels: {"org.opencontainers.image.title": $tag}}}')"
    config_digest="sha256:$(printf '%s' "$config" | sha256sum | cut -d ' ' -f 1)"
    printf '%s' "$config" >"$dir/blobs/$config_digest"
    manifest="$(jq -cn --arg digest "$config_digest" '{schemaVersion: 2,
        config: {mediaType: "application/vnd.oci.image.config.v1+json", digest: $digest, size: 76529}, layers: []}')"
    if [ "${4-}" = plain ]; then
        printf '%s' "$manifest" >"$dir/manifests/$2"
        return 0
    fi
    manifest_digest="sha256:$(printf '%s' "$manifest" | sha256sum | cut -d ' ' -f 1)"
    printf '%s' "$manifest" >"$dir/manifests/$manifest_digest"
    jq -cn --arg digest "$manifest_digest" '{schemaVersion: 2, mediaType: "application/vnd.oci.image.index.v1+json",
        manifests: [
            {mediaType: "application/vnd.oci.image.manifest.v1+json", digest: ("sha256:" + ("0" * 64)), size: 9,
             platform: {architecture: "arm64", os: "linux"}},
            {mediaType: "application/vnd.oci.image.manifest.v1+json", digest: $digest, size: 96879,
             platform: {architecture: "amd64", os: "linux"}}]}' >"$dir/manifests/$2"
}

# pull NUMBER TITLE [AUTHOR]: an open pull request, cut down from what GitHub
# lists. Its newest commit is its number, forty digits long.
pull() {
    [ -e "$github/open.json" ] || echo '[]' >"$github/open.json"
    jq --argjson number "$1" --arg title "$2" --arg login "${3:-Mondrethos}" --arg commit "$(commit_of "$1")" '
        . + [{number: $number, title: $title, state: "open", draft: false, merged_at: null,
              user: {login: $login, type: (if $login | endswith("[bot]") then "Bot" else "User" end)},
              head: {sha: $commit, ref: "some-branch"}, base: {ref: "main"}}]' "$github/open.json" >"$github/open.json.new"
    mv "$github/open.json.new" "$github/open.json"
    # GitHub answers about one pull request with the same and a little more.
    jq --argjson number "$1" '.[] | select(.number == $number) | . + {merged: false}' "$github/open.json" \
        >"$github/pull-$1.json"
}
# gone NUMBER TITLE merged|closed: a pull request that isn't open any more.
gone() {
    jq -n --argjson number "$1" --arg title "$2" --argjson merged "$([ "$3" = merged ] && echo true || echo false)" \
        --arg commit "$(commit_of "$1")" '
        {number: $number, title: $title, state: "closed", merged: $merged, draft: false,
         merged_at: (if $merged then "2026-11-11T09:22:51Z" else null end),
         user: {login: "Mondrethos", type: "User"}, head: {sha: $commit, ref: "some-branch"}, base: {ref: "main"}}' \
        >"$github/pull-$1.json"
}
# checks NUMBER NAME/STATUS/CONCLUSION/STARTED-MINUTES-AGO...: the check runs
# of the pull request's newest commit, as build.yml's jobs report them.
checks() {
    local number="$1" run name status conclusion started runs='[]'
    shift
    for run in "$@"; do
        IFS=/ read -r name status conclusion started <<<"$run"
        runs="$(jq -c --arg name "$name" --arg status "$status" --arg conclusion "$conclusion" \
            --arg started "$(minutes_ago "$started")" '. + [{name: $name, status: $status,
            conclusion: (if $conclusion == "" then null else $conclusion end), started_at: $started,
            completed_at: (if $status == "completed" then $started else null end), app: {slug: "github-actions"}}]' <<<"$runs")"
    done
    jq -n --argjson runs "$runs" '{total_count: ($runs | length), check_runs: $runs}' \
        >"$github/checks-$(commit_of "$number").json"
}
# The usual outcomes of a push to a pull request. A build that passed started
# half an hour before its image was built. One that wasn't needed leaves the
# build job behind once, skipped, under the name the workflow gives it.
decided='Check if a rebuild is needed/completed/success'
passed() { checks "$1" "Build ${3:-KDE}/completed/success/$(($2 + 30))" "$decided/$(($2 + 31))" "Test scripts/completed/success/$(($2 + 31))"; }
unaffected() { checks "$1" 'Build ${{ matrix.label }}/completed/skipped/5' "$decided/5" 'Test scripts/in_progress//5'; }

# fresh: an empty GitHub and a registry with the newest build of both
# editions, made 20 hours ago, on a computer that runs it.
fresh() {
    rm -rf "$log" "$github" "$registry"
    mkdir -p "$log" "$github"
    echo '[]' >"$github/open.json"
    image kde latest 1200
    image kde-nvidia latest 1200
    deployments kde latest 1200
}
# several: a day with every kind of pull request open. #63 and #61 can be
# tried; the others can't, each for another reason. Built once and copied.
several() {
    if [ ! -d "$test_root/several" ]; then
        fresh
        pull 66 'Bump the build action' 'dependabot[bot]'
        unaffected 66
        pull 65 'Try a newer kernel'
        checks 65 'Build KDE/in_progress//12' 'Build KDE NVIDIA/in_progress//12' "$decided/13"
        image kde pr-65-45 180
        pull 64 'Move the release tag when a day gets a second build'
        unaffected 64
        pull 63 'Check real app downloads once a day'
        passed 63 120
        image kde pr-63-45 120
        pull 62 "Show why an app didn't update"
        checks 62 'Build KDE/completed/failure/90' 'Build KDE NVIDIA/completed/success/90' "$decided/91"
        image kde pr-62-45 400
        pull 61 "Show each app's progress in monolith update"
        passed 61 4320
        image kde pr-61-45 4320
        pull 60 'Use the new NVIDIA driver'
        passed 60 200 'KDE NVIDIA'
        image kde-nvidia pr-60-45 200
        pull 59 'Stopped half way'
        checks 59 'Build KDE/completed/cancelled/50' "$decided/51"
        pull 58 'Built, but the image is gone'
        passed 58 300
        pull 57 'Passed, with an image from an earlier push'
        passed 57 60
        image kde pr-57-45 500
        pull 56 'Pushed a moment ago'
        checks 56 'Check if a rebuild is needed/in_progress//0' 'Test scripts/queued//0'
        pull 55 'No checks at all'
        checks 55
        pull 54 'Checked, and the builds come next'
        checks 54 "$decided/1" 'Test scripts/in_progress//1'
        pull 53 'The check before the builds broke'
        checks 53 'Check if a rebuild is needed/completed/failure/9' 'Test scripts/completed/success/9'
        mkdir "$test_root/several"
        cp -r "$github" "$registry" "$test_root/several/"
    fi
    rm -rf "$log" "$github" "$registry"
    mkdir -p "$log"
    cp -r "$test_root/several/github" "$test_root/several/registry" "$test_root/"
    deployments kde latest 1200
}
run() { PATH="$bin:$utils" "$libexec/pr" "$@" </dev/null 2>&1; }
yes_run() { MONOLITH_YES=1 run "$@"; }
# on_terminal KEYS ARGUMENT...: run it on a terminal and type KEYS. A command
# that hangs is killed, so what it showed until then is the failure.
on_terminal() {
    local keys="$1" command
    shift
    command="$(printf '%q ' "$libexec/pr" "$@")"
    printf '%b' "$keys" | PATH="$bin:$utils" SHELL=/bin/bash TERM="${TERM_FOR_TEST:-dumb}" \
        timeout -s KILL 60 "$script_program" -qec "$command" /dev/null 2>&1 | sed 's/\r$//'
}
# plain takes every escape sequence out, colorless only the colors.
plain() { sed 's/\x1b\[[0-9;]*[A-Za-z]//g'; }
colorless() { sed 's/\x1b\[[0-9;]*m//g'; }
# widest: the widest line as the terminal shows it, where a carriage return
# starts the line over. The question's line ends with what's typed there.
widest() { sed 's/.*\r//' | grep -v '^Type a number' | LC_ALL=C.UTF-8 wc -L; }
asked_github() { if [ -e "$log/github" ]; then grep -c . "$log/github" || true; else echo 0; fi; }
rebased() { [ "$(cat "$log/rpm-ostree" 2>/dev/null)" = "rebase ostree-image-signed:docker://ghcr.io/monolithlinux/$1" ] ||
    fail "expected a rebase to $1, got: $(cat "$log/rpm-ostree" 2>/dev/null)"; }
cancelled() { [ "$(cat "$log/rpm-ostree" 2>/dev/null)" = "cleanup -p" ] ||
    fail "expected cleanup -p, got: $(cat "$log/rpm-ostree" 2>/dev/null)"; }
not_rebased() { [ ! -e "$log/rpm-ostree" ] || fail "rpm-ostree changed something: $(cat "$log/rpm-ostree")"; }

# Help, and the same answer as the other commands off Monolith ------------------
fresh
out="$(run --help)" || fail "$out"
has "$out" 'Usage: monolith pr [number|list|latest]'
has "$out" 'A pull request is a change to Monolith'
[ "$(awk 'length > 80' <<<"$out")" = '' ] || fail 'the help has lines over 80 characters'
[ "$(asked_github)" = 0 ] || fail "the help asked GitHub: $(cat "$log/github")"
jq '.deployments[0]["container-image-reference"] = "ostree-unverified-registry:ghcr.io/someone/kde:latest"' \
    "$test_root/status.json" >"$test_root/status.json.new"
mv "$test_root/status.json.new" "$test_root/status.json"
for words in '' list latest 63; do
    # shellcheck disable=SC2086
    out="$(yes_run $words)" && fail "pr $words ran on an image that is not Monolith's"
    has "$out" "You're not on one of Monolith's images (ghcr.io/someone/kde), so there are no test builds"
done
rm "$test_root/status.json"
out="$(run list)" && fail 'pr list ran without rpm-ostree'
has "$out" "Couldn't read your builds from rpm-ostree."
[ "$(asked_github)" = 0 ] || fail "asked GitHub before knowing the computer: $(cat "$log/github")"

# The list ------------------------------------------------------------------------
several
out="$(run)" || fail "$out"
expected="Monolith KDE - test builds
You're on the newest build.

Pull requests you can try
  + #63  Check real app downloads once a day
         built 2 hours ago - 18 hours ahead of latest
  + #61  Show each app's progress in monolith update
         built 3 days ago - 2 days behind latest

Other open pull requests
  - #66  Bump the build action
         gets no test builds
  - #65  Try a newer kernel
         still building, started 12 minutes ago
  - #64  Move the release tag when a day gets a second build
         doesn't change this edition
  - #62  Show why an app didn't update
         its build failed
  - #60  Use the new NVIDIA driver
         doesn't change this edition
  - #59  Stopped half way
         its build was cancelled
  - #58  Built, but the image is gone
         no test build found for this edition
  - #57  Passed, with an image from an earlier push
         its test build is out of date
  - #56  Pushed a moment ago
         its checks are still starting
  - #55  No checks at all
         not built yet
  - #54  Checked, and the builds come next
         its checks are still starting
  - #53  The check before the builds broke
         its checks failed

Switch to one with: monolith pr 63"
[ "$out" = "$expected" ] || fail "the list:"$'\n'"$(diff <(echo "$expected") <(echo "$out") || true)"
# One question for the list and one for each pull request, without an account.
[ "$(asked_github)" = 15 ] || fail "asked GitHub $(asked_github) times: $(cat "$log/github")"
lacks "$(cat "$log/github")" 'sent credentials'
[ "$(sed -n 1p "$log/github")" = 'pulls?state=open&per_page=100' ] || fail "first question: $(sed -n 1p "$log/github")"
[ "$(grep -c '/token' "$log/registry")" = 1 ] || fail "tokens: $(grep -c '/token' "$log/registry")"
not_rebased
# pr list is the same, and neither waits for an answer without a terminal.
[ "$(run list)" = "$expected" ] || fail "pr list differs from pr without a terminal: $(run list)"

# The NVIDIA edition reads its own build job and its own image.
several
deployments kde-nvidia latest 1200
out="$(run list)" || fail "$out"
has "$out" $'  + #60  Use the new NVIDIA driver\n         built 3 hours ago - 17 hours ahead of latest\n'
has "$out" $'  - #65  Try a newer kernel\n         still building, started 12 minutes ago\n'
# Its build of #62 passed, but there's no image of it.
has "$out" $'  - #62  Show why an app didn\'t update\n         no test build found for this edition\n'
has "$out" $'  - #63  Check real app downloads once a day\n         doesn\'t change this edition\n'
has "$out" 'Switch to one with: monolith pr 60'
lacks "$out" '  + #63'
# With a switch to the other edition waiting, the list is that edition's.
several
pending kde-nvidia latest
out="$(run list)" || fail "$out"
has "$out" $'You\'re on the newest build.\nYour next restart starts the newest build of the kde-nvidia edition.\n'
has "$out" '  + #60  Use the new NVIDIA driver'
yes_run 60 >/dev/null || fail 'switching with an edition switch waiting'
[ "$(tail -n 1 "$log/rpm-ostree")" = 'rebase ostree-image-signed:docker://ghcr.io/monolithlinux/kde-nvidia:pr-60-45' ] ||
    fail "with an edition switch waiting: $(cat "$log/rpm-ostree")"

# Nothing open, and nothing to try.
fresh
out="$(run)" || fail "$out"
[ "$out" = $'Monolith KDE - test builds\nYou\'re on the newest build.\n\nThere are no open pull requests right now.' ] ||
    fail "no open pull requests: $out"
[ "$(asked_github)" = 1 ] || fail "asked GitHub $(asked_github) times for an empty list"
[ ! -e "$log/registry" ] || fail "asked the registry for an empty list: $(cat "$log/registry")"
pull 64 'Move the release tag when a day gets a second build'
unaffected 64
out="$(run)" || fail "$out"
has "$out" 'No open pull request has a test build for this computer right now.'
has "$out" $'Other open pull requests\n  - #64  Move the release tag'
lacks "$out" 'you can try'
lacks "$out" 'monolith pr'

# Where this computer is -------------------------------------------------------
# On a test build that's the newest of its pull request.
several
deployments kde pr-63-45 120
out="$(run list)" || fail "$out"
has "$out" $'Monolith KDE - test builds\nYou\'re on the test build of pull request #63:\nCheck real app downloads once a day\nBuilt 2 hours ago, 18 hours ahead of latest.\n\nPull requests'
has "$out" $'  + #63  Check real app downloads once a day\n         built 2 hours ago - 18 hours ahead of latest\n         you\'re on this one\n  + #61'
has "$out" $'\n\nSwitch to one with: monolith pr 63\nReturn to the newest build with: monolith pr latest'
lacks "$out" 'no more updates'
lacks "$out" 'A newer test build'
[ "$(grep -c "you're on" <<<"$out")" = 1 ] || fail "more than one pull request is the one it's on: $out"
[ "$(asked_github)" = 15 ] || fail "asked GitHub $(asked_github) times on a test build that's still open"
# On an older build of it, with packages layered on top.
deployments kde pr-63-45 300 layered
out="$(run list)" || fail "$out"
has "$out" $'Built 5 hours ago, 15 hours ahead of latest.\nA newer test build is out. It comes with your next update.\n\n'
has "$out" $'         built 2 hours ago - 18 hours ahead of latest\n         you\'re on an earlier build of this one\n'
# The other edition's build of it, made ten minutes later, is no newer build
# of the one this computer runs.
fresh
pull 73 'Changes both editions'
checks 73 'Build KDE/completed/success/150' 'Build KDE NVIDIA/completed/success/150' "$decided/151"
image kde pr-73-45 120
image kde-nvidia pr-73-45 110
deployments kde pr-73-45 120
pending kde-nvidia latest
out="$(run list)" || fail "$out"
has "$out" $'Built 2 hours ago, 18 hours ahead of latest.\nYour next restart starts the newest build of the kde-nvidia edition.\n\n'
has "$out" $'  + #73  Changes both editions\n         built 2 hours ago - 18 hours ahead of latest\n\n'
lacks "$out" 'A newer test build'
lacks "$out" "you're on"
# On a build of it from before a push that's still building or that failed.
several
deployments kde pr-65-45 180
out="$(run list)" || fail "$out"
has "$out" $'You\'re on the test build of pull request #65:\nTry a newer kernel\nBuilt 3 hours ago, 17 hours ahead of latest.\n\n'
has "$out" $'  - #65  Try a newer kernel\n         still building, started 12 minutes ago\n         you\'re on an earlier build of this one\n'
# On the test build of a pull request that was merged or closed since: one
# more question for GitHub, and the way back, said once.
for how in merged closed; do
    several
    gone 50 'Say why an app failed' "$how"
    deployments kde pr-50-45 4320
    out="$(run list)" || fail "$out"
    has "$out" $'You\'re on the test build of pull request #50:\nSay why an app failed\nBuilt 3 days ago, 2 days behind latest.\n'"#50 was $how, so this build gets no more updates."$'\n\nPull requests'
    has "$out" $'\nSwitch to one with: monolith pr 63\nReturn to the newest build with: monolith pr latest'
    [ "$(asked_github)" = 16 ] || fail "asked GitHub $(asked_github) times on the test build of a $how pull request"
    [ "$(tail -n 1 "$log/github")" = pulls/50 ] || fail "last question: $(tail -n 1 "$log/github")"
done
# GitHub doesn't know the pull request at all.
several
deployments kde pr-49-45 4320
out="$(run list)" || fail "$out"
has "$out" $'You\'re on the test build of pull request #49\nBuilt 3 days ago, 2 days behind latest.\n#49 isn\'t open any more, so this build gets no more updates.\n\n'
# With nothing open, the way back is all there is to say.
gone 50 'Say why an app failed' merged
deployments kde pr-50-45 4320
echo '[]' >"$github/open.json"
out="$(run)" || fail "$out"
has "$out" $'#50 was merged, so this build gets no more updates.\n\nThere are no open pull requests right now.\n\nReturn to the newest build with: monolith pr latest'
out="$(on_terminal '')" || fail "$out"
has "$out" 'Return to the newest build with: monolith pr latest'
lacks "$out" 'Type a number'
# rpm-ostree doesn't say when the build was made.
several
deployments kde pr-63-45 120
jq 'del(.deployments[0].timestamp)' "$test_root/status.json" >"$test_root/status.json.new"
mv "$test_root/status.json.new" "$test_root/status.json"
out="$(run list)" || fail "$out"
has "$out" $'You\'re on the test build of pull request #63:\nCheck real app downloads once a day\n\nPull requests'
has "$out" "         you're on this one"
# On an older build.
several
deployments kde 20261027 20000
out="$(run list)" || fail "$out"
has "$out" $'You\'re on the build from Tue, Oct 27, 2026, with system updates paused.\n\n'
has "$out" $'Switch to one with: monolith pr 63\nReturn to the newest build with: monolith pr latest'
# With a switch to a test build waiting for a restart.
several
pending kde pr-63-45
out="$(run list)" || fail "$out"
has "$out" $'You\'re on the newest build.\nYour next restart starts the test build of pull request #63.\n\n'
has "$out" $'         built 2 hours ago - 18 hours ahead of latest\n         starts at your next restart\n'
has "$out" $'\nSwitch to one with: monolith pr 63\nStay on the newest build with: monolith pr latest'
# On a test build with the return to the newest build waiting, the way back
# needs no more saying.
several
deployments kde pr-63-45 120
pending kde latest
out="$(run list)" || fail "$out"
has "$out" $'Built 2 hours ago, 18 hours ahead of latest.\nYour next restart starts the newest build.\n\n'
lacks "$out" 'monolith pr latest'
# A downloaded daily update is no switch.
several
pending kde latest
out="$(run list)" || fail "$out"
lacks "$out" 'Your next restart'
lacks "$out" 'monolith pr latest'

# Switching ----------------------------------------------------------------------
# Said yes: what it means comes first, and behind latest is no obstacle.
several
out="$(yes_run 63)" || fail "$out"
expected="Pull request #63: Check real app downloads once a day
Its test build was made 2 hours ago, 18 hours ahead of latest.

Test builds can be broken. On one, this computer follows that pull request's
builds instead of the daily ones, until you run monolith pr latest. Your apps,
files and settings stay. Nothing changes until you restart.


Done. Restart to start the test build of pull request #63.
To return to the newest build afterwards: monolith pr latest"
[ "$out" = "$expected" ] || fail "switching:"$'\n'"$(diff <(echo "$expected") <(echo "$out") || true)"
rebased kde:pr-63-45
[ "$(waits)" = kde:pr-63-45 ] || fail "$(waits) waits after switching"
# One question about the pull request and one about its build.
[ "$(cat "$log/github")" = "pulls/63"$'\n'"commits/$(commit_of 63)/check-runs?per_page=100" ] ||
    fail "questions for one pull request: $(cat "$log/github")"
for number in '#61' 061; do
    several
    out="$(yes_run "$number")" || fail "$out"
    has "$out" 'Its test build was made 3 days ago, 2 days behind latest.'
    rebased kde:pr-61-45
done
# Not said yes: nothing happens.
several
out="$(run 63)" || fail "$out"
has "$out" 'Test builds can be broken.'
has "$out" 'Nothing changed.'
not_rebased

# On a terminal it asks. pr on its own asks for a number first.
several
out="$(on_terminal 'y\n' 63)" || fail "$out"
has "$out" 'Switch to the test build of #63? [y/N]'
has "$out" 'Done. Restart to start the test build of pull request #63.'
rebased kde:pr-63-45
several
out="$(on_terminal 'n\n' 63)" || fail "$out"
has "$out" 'Nothing changed.'
not_rebased
several
out="$(on_terminal '61\nyes\n')" || fail "$out"
has "$out" 'Pull requests you can try'
has "$out" 'Type a number to switch, or press Enter to change nothing:'
has "$out" 'Pull request #61: Show each app'
has "$out" 'Switch to the test build of #61? [y/N]'
has "$out" 'Done. Restart to start the test build of pull request #61.'
lacks "$out" 'Switch to one with'
rebased kde:pr-61-45
# The list already has what switching needs.
[ "$(asked_github)" = 15 ] || fail "asked GitHub $(asked_github) times for the list and a switch"
several
out="$(on_terminal '\n')" || fail "$out"
has "$out" 'Nothing changed.'
lacks "$out" 'Switch to the test build'
not_rebased
several
out="$(on_terminal '#63\nn\n')" || fail "$out"
has "$out" 'Switch to the test build of #63? [y/N]'
has "$out" 'Nothing changed.'
not_rebased
several
out="$(on_terminal '65\n')" && fail 'picked a pull request that is still building'
has "$out" $'Can\'t switch to pull request #65: still building, started 12 minutes ago.\nNothing was changed.'
not_rebased
out="$(on_terminal '7\n')" && fail 'picked a number that is not in the list'
has "$out" "7 isn't the number of an open pull request. Nothing was changed."
not_rebased
# pr list never asks, and with nothing to try there's nothing to ask.
out="$(on_terminal '63\ny\n' list)" || fail "$out"
lacks "$out" 'Type a number'
has "$out" 'Switch to one with: monolith pr 63'
not_rebased
fresh
out="$(on_terminal '63\n')" || fail "$out"
lacks "$out" 'Type a number'
has "$out" 'There are no open pull requests right now.'
# Away from the newest build, the way back comes before the question, and
# the question takes it too.
several
deployments kde pr-61-45 4320
out="$(on_terminal 'latest\ny\n')" || fail "$out"
has "$out" $'Return to the newest build with: monolith pr latest\n\nType a number to switch'
has "$out" 'Return to the newest kde build and daily updates? [y/N]'
has "$out" 'Done. Restart to start the newest build.'
rebased kde:latest

# The tag's Fedora version comes from the registry. With two, the image of the
# build that passed is the one made after that build started.
fresh
pull 70 'Move to Fedora 46'
passed 70 45
image kde pr-70-46 45
yes_run 70 >/dev/null || fail 'switching to a test build on another Fedora version'
rebased kde:pr-70-46
fresh
pull 70 'Move to Fedora 46'
passed 70 45
image kde pr-70-45 900
image kde pr-70-46 45
image kde pr-7-45 45
image kde pr-700-45 45
yes_run 70 >/dev/null || fail 'switching with an older tag on the old Fedora version'
rebased kde:pr-70-46
fresh
pull 70 'Stay on Fedora 45 after all'
passed 70 45
image kde pr-70-46 900
image kde pr-70-45 45
yes_run 70 >/dev/null || fail 'switching with an older tag on the new Fedora version'
rebased kde:pr-70-45
# Another pull request's tag is no match, however alike the numbers.
fresh
pull 7 'Lucky seven'
passed 7 45
image kde pr-70-45 45
image kde pr-17-45 45
image kde pr-7-45-rc 45
out="$(yes_run 7)" && fail 'took the test build of another pull request'
has "$out" "Can't switch to pull request #7: no test build found for this edition. Nothing was changed."
not_rebased
# A job that ran twice for one commit counts with its newest run.
fresh
pull 72 'Built again after a failure'
checks 72 'Build KDE/completed/success/75' 'Build KDE/completed/failure/200' "$decided/201"
image kde pr-72-45 45
yes_run 72 >/dev/null || fail 'switching after a build that passed the second time'
rebased kde:pr-72-45
# A build job of this edition that was skipped built nothing.
fresh
pull 71 'Skipped for this edition alone'
checks 71 'Build KDE/completed/skipped/5' 'Build KDE NVIDIA/completed/success/5' "$decided/6"
image kde pr-71-45 400
out="$(yes_run 71)" && fail 'took a skipped build for one that passed'
has "$out" "Can't switch to pull request #71: doesn't change this edition. Nothing was changed."
# A tag can name the image itself, without an index.
fresh
pull 70 'An image without an index'
passed 70 45
image kde pr-70-45 45 plain
has "$(yes_run 70)" 'Its test build was made 45 minutes ago, 19 hours ahead of latest.'
rebased kde:pr-70-45
# Built within a minute of the newest build, or a moment ago.
fresh
pull 70 'Built along with the newest build'
passed 70 1200
image kde pr-70-45 1200
has "$(run 70)" 'Its test build was made 20 hours ago, as new as latest.'
fresh
pull 70 'Built just now'
passed 70 0
image kde pr-70-45 0
has "$(run 70)" 'Its test build was made just now, 20 hours ahead of latest.'
# The tags come in pages, and the newest are on the last.
several
out="$(TAG_PAGE=2 yes_run 61)" || fail "$out"
rebased kde:pr-61-45
[ "$(grep -c 'tags/list' "$log/registry")" -gt 1 ] || fail "read one page of tags: $(cat "$log/registry")"

# Already there, already waiting, and back on it after changing one's mind.
several
deployments kde pr-63-45 120
has "$(yes_run 63)" $'You\'re already on the test build of pull request #63.\nIts new builds come with your updates.'
not_rebased
several
pending kde pr-63-45
out="$(yes_run 63)" || fail "$out"
has "$out" $'The test build of pull request #63 already waits for your next restart.\nRestart to start it.'
not_rebased
several
deployments kde pr-63-45 120
pending kde latest
out="$(run 63)" || fail "$out"
has "$out" 'Cancelling the switch to the newest build.'
has "$out" 'Done. You stay on the test build of pull request #63.'
cancelled
[ "$(waits)" = nothing ] || fail "$(waits) still waits"
# From one test build to another, and with a rollback waiting.
several
deployments kde pr-61-45 4320
yes_run 63 >/dev/null || fail 'switching between test builds'
rebased kde:pr-63-45
several
pending kde 20261027
yes_run 63 >/dev/null || fail 'switching with a rollback waiting'
rebased kde:pr-63-45

# Refusals: each says why, and that nothing was changed ---------------------------
several
gone 50 'Say why an app failed' merged
gone 51 'Start the nightly build on time' closed
refused() {
    local out
    out="$(yes_run "$1")" && fail "pr $1 succeeded"
    has "$out" "$2"
    has "$out" 'Nothing was changed.'
    not_rebased
}
refused 66 "Can't switch to pull request #66: gets no test builds."
refused 65 "Can't switch to pull request #65: still building, started 12 minutes ago."
refused 64 "Can't switch to pull request #64: doesn't change this edition."
refused 62 "Can't switch to pull request #62: its build failed."
refused 60 "Can't switch to pull request #60: doesn't change this edition."
refused 59 "Can't switch to pull request #59: its build was cancelled."
refused 58 "Can't switch to pull request #58: no test build found for this edition."
refused 57 "Can't switch to pull request #57: its test build is out of date."
refused 56 "Can't switch to pull request #56: its checks are still starting."
refused 55 "Can't switch to pull request #55: not built yet."
refused 54 "Can't switch to pull request #54: its checks are still starting."
refused 53 "Can't switch to pull request #53: its checks failed."
refused 50 'Pull request #50 was merged, so you get its changes with the regular builds.'
refused 51 'Pull request #51 was closed, so its test build gets no more updates.'
refused 999 'Monolith has no pull request #999.'
[ "$(asked_github)" = 27 ] || fail "asked GitHub $(asked_github) times for the refusals: $(cat "$log/github")"
for words in sixty-three 63x -63 1234567890; do
    out="$(yes_run "$words")" && fail "took $words for a number"
    has "$out" 'monolith pr takes the number of a pull request, list or latest.'
done
[ "$(asked_github)" = 27 ] || fail "asked GitHub about something that is no number: $(cat "$log/github")"

# pr latest is rollback latest ------------------------------------------------------
several
deployments kde pr-63-45 120
has "$(run latest)" 'Nothing changed.'
not_rebased
out="$(yes_run latest)" || fail "$out"
has "$out" 'Done. Restart to start the newest build.'
rebased kde:latest
several
pending kde pr-63-45
out="$(run latest)" || fail "$out"
has "$out" 'Cancelling the switch to the test build of pull request #63.'
has "$out" 'Done. You stay on the newest build, and daily updates continue.'
cancelled
rm "$log/rpm-ostree"
has "$(run latest)" "You're already on the newest build, and updates are on."
not_rebased
[ "$(asked_github)" = 0 ] || fail "pr latest asked GitHub: $(cat "$log/github")"
[ ! -e "$log/registry" ] || fail "pr latest asked the registry: $(cat "$log/registry")"
for words in 'pr latest' 'rollback latest'; do
    several
    deployments kde pr-63-45 120
    pending kde 20261027
    # shellcheck disable=SC2086
    out="$(PATH="$bin:$utils" MONOLITH_YES=1 "$libexec/${words% *}" ${words#* } </dev/null 2>&1)" || fail "$out"
    [ "$out" = $'\nDone. Restart to start the newest build.' ] || fail "$words: $out"
    rebased kde:latest
done

# When GitHub or the registry can't answer ------------------------------------
# The hour's requests are used up: for the list, for one pull request, and
# half way through the list.
several
for status in 403 429; do
    for words in '' list 63; do
        # shellcheck disable=SC2086
        out="$(GITHUB_LIMIT="$status" GITHUB_RESET="$((now + 23 * 60))" yes_run $words)" && fail "pr $words passed a rate limit"
        [ "$out" = 'GitHub limits how many lookups one internet address gets an hour, and this one has used them up. Try again in 23 minutes. Nothing was changed.' ] ||
            fail "a rate limit ($status) for pr $words: $out"
    done
done
out="$(GITHUB_LIMIT=403 GITHUB_RESET="$((now + 3 * 3600))" run)" && fail 'a rate limit passed'
has "$out" 'Try again in 3 hours.'
out="$(GITHUB_LIMIT=403 GITHUB_RESET="$((now - 5))" run)" && fail 'a rate limit passed'
has "$out" 'Try again in 1 minute.'
out="$(GITHUB_LIMIT=403 GITHUB_RESET=soon run)" && fail 'a rate limit passed'
has "$out" 'this one has used them up. Try again in an hour. Nothing was changed.'
rm "$log/github"
out="$(GITHUB_LIMIT=403 GITHUB_LIMIT_AFTER=4 GITHUB_RESET="$((now + 23 * 60))" run)" && fail 'a rate limit half way passed'
has "$out" 'this one has used them up. Try again in 23 minutes.'
lacks "$out" 'Pull requests you can try'
[ "$(asked_github)" = 5 ] || fail "kept asking GitHub after the limit: $(asked_github) questions"
out="$(GITHUB_RETRY=120 run)" && fail 'a request to slow down passed'
[ "$out" = 'GitHub asked for a pause between lookups. Try again in 2 minutes. Nothing was changed.' ] || fail "a pause: $out"
# On a terminal a long message breaks between words, at 80 columns or the
# window's edge.
out="$(GITHUB_LIMIT=403 GITHUB_RESET="$((now + 23 * 60))" on_terminal '')" && fail 'a rate limit passed on a terminal'
has "$out" $'\nGitHub limits how many lookups one internet address gets an hour, and this one\nhas used them up. Try again in 23 minutes. Nothing was changed.'
out="$(COLUMNS=200 GITHUB_LIMIT=403 GITHUB_RESET="$((now + 23 * 60))" on_terminal '' 63)" && fail 'a rate limit passed on a terminal'
has "$out" $'\nGitHub limits how many lookups one internet address gets an hour, and this one\nhas used them up. Try again in 23 minutes. Nothing was changed.'
out="$(COLUMNS=40 GITHUB_DOWN=1 on_terminal '')" && fail 'no network passed on a terminal'
has "$out" $'\nCouldn\'t reach GitHub to look up\nMonolith\'s pull requests. Check your\nconnection and try again. Nothing was\nchanged.'
# Other refusals and errors of GitHub's.
out="$(GITHUB_ANSWERS='403 {"message":"Forbidden"}' run)" && fail 'a 403 passed'
has "$out" "GitHub didn't give the answer monolith expected (HTTP 403). Try again later. Nothing was changed."
out="$(GITHUB_ANSWERS='502 <html><body>Bad Gateway</body></html>' yes_run 63)" && fail 'a 502 passed'
has "$out" "GitHub didn't give the answer monolith expected (HTTP 502). Try again later. Nothing was changed."
lacks "$out" 'Bad Gateway'
# No network.
for words in '' list 63; do
    # shellcheck disable=SC2086
    out="$(GITHUB_DOWN=1 yes_run $words)" && fail "pr $words passed without GitHub"
    [ "$out" = "Couldn't reach GitHub to look up Monolith's pull requests. Check your connection and try again. Nothing was changed." ] ||
        fail "pr $words without GitHub: $out"
    # shellcheck disable=SC2086
    out="$(REGISTRY_DOWN=1 yes_run $words)" && fail "pr $words passed without the registry"
    [ "$out" = "Couldn't read Monolith's builds from ghcr.io. Check your connection and try again. Nothing was changed." ] ||
        fail "pr $words without the registry: $out"
done
not_rebased
# Answers that aren't what GitHub's API sends: a sign-in page, another shape.
for body in '<html>Sign in to the hotel Wi-Fi</html>' '{"message": "Moved"}' '[{"number": "sixty", "head": {}}]' \
    '[{"number": 1}]' '[{"number": 63, "title": "No commit", "head": {"sha": "main"}, "user": {}}]' '["63"]' ''; do
    for words in '' 63; do
        # shellcheck disable=SC2086
        out="$(GITHUB_ANSWERS="200 $body" yes_run $words)" && fail "pr $words passed with the answer $body"
        [ "$out" = "GitHub's answer wasn't what monolith expected. Try again later. Nothing was changed." ] ||
            fail "pr $words with the answer $body: $out"
    done
done
# A number or a commit that isn't one goes no further: not into the next
# question, and not into the search for its tag. GitHub would answer these.
for change in '.number = "6 3"' '.head.sha = "main"' '.head.sha = "../../pulls/61"'; do
    several
    jq "(.[] | select(.number == 63)) |= ($change)" "$github/open.json" >"$github/open.json.new"
    mv "$github/open.json.new" "$github/open.json"
    jq "$change" "$github/pull-63.json" >"$github/pull-63.json.new"
    mv "$github/pull-63.json.new" "$github/pull-63.json"
    cp "$github/checks-$(commit_of 63).json" "$github/checks-main.json"
    for words in list 63; do
        # pr 63 has its number from the command line.
        [ "$words$change" != '63.number = "6 3"' ] || continue
        out="$(yes_run "$words")" && fail "pr $words passed with $change"
        [ "$out" = "GitHub's answer wasn't what monolith expected. Try again later. Nothing was changed." ] ||
            fail "pr $words with $change: $out"
    done
    lacks "$(cat "$log/github")" 'commits/main'
    lacks "$(cat "$log/github")" '..'
    not_rebased
done
several
for runs in '{"total_count": "many", "check_runs": "none"}' '{"check_runs": [{"name": 7}]}' '[]' \
    '{"check_runs": [{"name": "Build KDE", "status": "completed", "conclusion": "success", "started_at": "yesterday"}]}' \
    '{"check_runs": [{"name": "Build KDE", "status": "completed", "conclusion": "success", "started_at": null}]}'; do
    echo "$runs" >"$github/checks-$(commit_of 63).json"
    for words in list 63; do
        out="$(yes_run "$words")" && fail "pr $words passed with the check runs $runs"
        [ "$out" = "GitHub's answer wasn't what monolith expected. Try again later. Nothing was changed." ] ||
            fail "pr $words with the check runs $runs: $out"
    done
done
not_rebased
# The registry answers with something else.
several
echo '<html>Sign in to the hotel Wi-Fi</html>' >"$registry/kde/tags.json"
out="$(yes_run 63)" && fail 'a tag list that is no tag list passed'
has "$out" "Couldn't read Monolith's builds from ghcr.io."
several
echo '{"errors": [{"code": "MANIFEST_UNKNOWN"}]}' >"$registry/kde/manifests/latest"
out="$(run list)" && fail 'a newest build without a manifest passed'
has "$out" "Couldn't read Monolith's builds from ghcr.io."
# One test build that can't be read doesn't hide the others. Its time has to
# be a time.
for created in 5 '"tomorrow"' '"2026-11-12"' null; do
    several
    for blob in "$registry"/kde/blobs/*; do
        [ "$(jq -r '.config.Labels["org.opencontainers.image.title"]' "$blob")" != pr-63-45 ] ||
            echo "{\"created\": $created}" >"$blob"
    done
    out="$(run list)" || fail "$out"
    has "$out" $'  - #63  Check real app downloads once a day\n         its test build couldn\'t be read\n'
    has "$out" '  + #61  Show each app'
    refused 63 "Can't switch to pull request #63: its test build couldn't be read."
done

# A title is someone else's text: what could steer the terminal is taken out.
fresh
pull 80 $'Looks \e[31mharmless\e[0m\a \xe2\x80\xaegnp.exe\ttab'
passed 80 45
image kde pr-80-45 45
out="$(LC_ALL=C.UTF-8 run list)" || fail "$out"
has "$out" '  + #80  Looks  [31mharmless [0m   gnp.exe tab'
lacks "$out" $'\e'
lacks "$out" $'\a'
lacks "$out" $'\xe2\x80\xae'
has "$(LC_ALL=C.UTF-8 yes_run 80)" 'Pull request #80: Looks  [31mharmless [0m   gnp.exe tab'
deployments kde pr-80-45 45
has "$(LC_ALL=C.UTF-8 run list)" $'pull request #80:\nLooks  [31mharmless [0m   gnp.exe tab\n'
# And a merged one's, which comes from another answer.
gone 79 $'Gone \e]0;title\a' merged
deployments kde pr-79-45 45
out="$(run list)" || fail "$out"
has "$out" $'pull request #79:\nGone  ]0;title \n'
lacks "$out" $'\e'
# Nothing else in an answer can get in front of the title either: not with
# the character that keeps an answer's fields apart.
fresh
pull 80 'Plain title' $'someone\x1f\e[31mred'
passed 80 45
image kde pr-80-45 45
out="$(run list)" || fail "$out"
has "$out" $'  + #80  Plain title\n'
lacks "$out" $'\e'
out="$(yes_run 80)" || fail "$out"
has "$out" $'Pull request #80: Plain title\n'
lacks "$out" $'\e'
rebased kde:pr-80-45

# Another address for GitHub's API, as tests and mirrors use.
several
out="$(MONOLITH_GITHUB_API=https://github.test/api/v3 run list)" || fail "$out"
has "$out" '  + #63  Check real app downloads once a day'

# How it looks -----------------------------------------------------------------
# On a terminal: Monolith's mark, symbols, and only the terminal's own sixteen
# colors, bold and dim.
several
out="$(unset NO_COLOR; LC_ALL=C.UTF-8 TERM_FOR_TEST=xterm-256color MONOLITH_MARK=1 on_terminal '\n')" || fail "$out"
text="$(plain <<<"$out")"
has "$text" $' █▄      ▄█   Monolith KDE · test builds\n ██▜ ▄▄ ▛██   You\'re on the newest build.\n ██ ▐██▌ ██\n\nPull requests you can try\n'
has "$text" $'  ✓ #63  Check real app downloads once a day\n         built 2 hours ago · 18 hours ahead of latest\n'
has "$text" '  • #64  Move the release tag when a day gets a second build'
has "$out" $'\e[1m\e[94m#63\e[0m'
codes="$(grep -o $'\e\\[[0-9;]*m' <<<"$out" | sort -u | tr -d '\033' | tr '\n' ' ')"
[ "$codes" = '[0m [1m [2m [32m [39m [94m ' ] || fail "colors on a terminal: $codes"
lacks "$(colorless <<<"$out")" $'\e'
# The note about looking things up is wiped again, and never reaches a pipe.
note="Looking up Monolith's open pull requests..."
has "$out" "$note"$'\r'"$(printf '%*s' "${#note}" '')"$'\r'
lacks "$(run list)" 'Looking up'
# A pipe gets plain text, whatever the terminal behind it could show.
out="$(unset NO_COLOR; LC_ALL=C.UTF-8 TERM=xterm-256color MONOLITH_MARK=0 run list)" || fail "$out"
lacks "$out" $'\e'
has "$out" $'Monolith KDE · test builds\nYou\'re on the newest build.\n'
# NO_COLOR, and a terminal that knows no colors: plain text.
out="$(LC_ALL=C.UTF-8 TERM_FOR_TEST=xterm-256color on_terminal '\n')" || fail "$out"
lacks "$out" $'\e'
has "$out" '  ✓ #63  Check real app downloads once a day'
out="$(unset NO_COLOR; on_terminal '\n')" || fail "$out"
lacks "$out" $'\e'
has "$out" "$note"$'\nMonolith KDE - test builds\n'
# The lines about this computer fit beside the mark in a narrow window.
several
gone 50 'Say why an app failed, in as many words as it takes to get past the edge of the window' merged
deployments kde pr-50-45 4320
pending kde 20261027
out="$(COLUMNS=60 LC_ALL=C.UTF-8 TERM_FOR_TEST=xterm-256color MONOLITH_MARK=1 on_terminal '\n')" || fail "$out"
has "$out" $' █▄      ▄█   Monolith KDE · test builds
 ██▜ ▄▄ ▛██   You\'re on the test build of pull request #50:
 ██ ▐██▌ ██   Say why an app failed, in as many words as it…
              Built 3 days ago, 2 days behind latest.
              #50 was merged, so this build gets no more
              updates.
              Your next restart starts the build from Tue,
              Oct 27, 2026.\n\n'
has "$out" $'\nReturn to the newest build with: monolith pr latest\n\nType a number'
[ "$(widest <<<"$out")" -le 60 ] || fail "lines over 60 columns: $out"
# A long title is cut to the window on a terminal, by what it takes up there,
# and whole in a pipe.
fresh
long='A title that goes on and on about everything this pull request changes, and then some more'
pull 81 "$long"
passed 81 45
image kde pr-81-45 45
pull 82 "$(printf '漢%.0s' {1..40})"
passed 82 45
image kde pr-82-45 45
out="$(COLUMNS=60 on_terminal '\n')" || fail "$out"
has "$out" $'\n'"  + #81  ${long:0:48}..."$'\n'
[ "$(widest <<<"$out")" -le 60 ] || fail "lines over 60 columns: $out"
has "$(COLUMNS=60 run list)" "  + #81  $long"$'\n'
out="$(COLUMNS=60 LC_ALL=C.UTF-8 TERM_FOR_TEST=xterm-256color on_terminal '\n')" || fail "$out"
# The cut falls after a space, which goes too.
has "$out" $'\n'"  ✓ #81  ${long:0:49}…"$'\n'
has "$out" $'\n'"  ✓ #82  $(printf '漢%.0s' {1..25})…"$'\n'
[ "$(widest <<<"$out")" -le 60 ] || fail "lines over 60 columns: $out"

# What the command takes from build.yml: the names of its jobs, one build job
# for each edition, and no builds for Dependabot.
workflow="$repo_root/.github/workflows/build.yml"
for edition in $(bash "$repo_root/.github/workflows/pick-editions.sh" all); do
    label="$(bash -c '. "$1"; printf "%s\n" "${MONOLITH_EDITION_LABELS[$2]-}"' _ "$libexec/common.sh" "$edition")"
    [ -n "$label" ] || fail "common.sh has no build job name for the $edition edition"
    grep -qF "if has $edition; then entries+=('{\"label\":\"$label\"," "$workflow" ||
        fail "build.yml doesn't call the $edition edition's build $label"
done
grep -q '^    name: Build \${{ matrix.label }}$' "$workflow" || fail 'build.yml no longer names its build jobs "Build LABEL"'
grep -q '^    name: Check if a rebuild is needed$' "$workflow" ||
    fail 'build.yml no longer has a job named "Check if a rebuild is needed"'
grep -q "^    if: github.actor != 'dependabot\[bot\]' && " "$workflow" ||
    fail "build.yml no longer skips Dependabot's builds"

printf 'monolith pr tests passed\n'
