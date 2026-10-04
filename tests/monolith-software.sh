#!/usr/bin/env bash
# Offline end-to-end checks for software ownership, updates, and cleanup.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
manager="$repo_root/files/system/usr/bin/monolith"
# A checkout-local TMPDIR also covers non-/tmp paths on normal checkouts.
test_root="$(mktemp -d "$repo_root/.monolith-software-test.XXXXXX")"
trap 'rm -rf -- "$test_root"' EXIT
mock_bin="$test_root/mock-bin"
fixtures="$test_root/fixtures"
mkdir -p "$mock_bin" "$fixtures"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
assert_absent() { [ ! -e "$1" ] && [ ! -L "$1" ] || fail "unexpected path: $1"; }
assert_file_equals() { cmp -s -- "$1" "$2" || fail "files differ: $1 and $2"; }

# Release fixtures. v9.9.9 publishes programs that fail to start.
for version in v1.0.0 v2.0.0 v9.9.9; do
    dir="$fixtures/$version"
    mkdir -p "$dir/superfile-src/dist/superfile-linux-$version-amd64" "$dir/ngit-src"
    for tool in omp nak herdr claude opencode codex tea spf ngit git-remote-nostr; do
        if [ "$version" = v9.9.9 ]; then
            printf '#!/bin/sh\necho "broken %s" >&2\nexit 3\n' "$tool" >"$dir/$tool"
        else
            printf '#!/bin/sh\nprintf "%%s\\n" "%s %s"\n' "$tool" "$version" >"$dir/$tool"
        fi
        chmod 0755 "$dir/$tool"
    done
    plain="${version#v}"
    cp -- "$dir/omp" "$dir/omp-linux-x64"
    (cd "$dir" && sha256sum omp-linux-x64 >SHA256SUMS.txt)
    cp -- "$dir/nak" "$dir/nak-$version-linux-amd64"
    cp -- "$dir/tea" "$dir/tea-$plain-linux-amd64"
    (cd "$dir" && sha256sum "tea-$plain-linux-amd64" >"tea-$plain-linux-amd64.sha256")
    cp -- "$dir/spf" "$dir/superfile-src/dist/superfile-linux-$version-amd64/spf"
    tar -czf "$dir/superfile-linux-$version-amd64.tar.gz" -C "$dir/superfile-src" dist
    (cd "$dir" && {
        printf '%064d  superfile-linux-%s-arm64.tar.gz\n' 0 "$version"
        sha256sum "superfile-linux-$version-amd64.tar.gz"
    } >"superfile-$version-checksums.txt")
    cp -- "$dir/ngit" "$dir/git-remote-nostr" "$dir/ngit-src/"
    tar -czf "$dir/ngit-$version-x86_64-unknown-linux-musl.tar.gz" -C "$dir/ngit-src" ngit git-remote-nostr
done
# A correctly checksummed Superfile archive without the expected program.
mkdir -p "$fixtures/bad-archive/dist/elsewhere"
cp -- "$fixtures/v2.0.0/spf" "$fixtures/bad-archive/dist/elsewhere/spf"
tar -czf "$fixtures/bad-archive/superfile-linux-v2.0.0-amd64.tar.gz" -C "$fixtures/bad-archive" dist
(cd "$fixtures/bad-archive" && sha256sum superfile-linux-v2.0.0-amd64.tar.gz >superfile-v2.0.0-checksums.txt)

cat >"$mock_bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
output= url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o|--output) output="$2"; shift 2 ;;
        --retry|--retry-delay|--connect-timeout|--max-time|--speed-limit|--speed-time) shift 2 ;;
        http://*|https://*) url="$1"; shift ;;
        -*) shift ;;
        *) echo "Unexpected curl argument: $1" >&2; exit 90 ;;
    esac
done
printf '%s\n' "$url" >>"$MOCK_CURL_LOG"
if [ -n "${MOCK_CURL_FAIL_MATCH:-}" ] && [[ "$url" == *"$MOCK_CURL_FAIL_MATCH"* ]]; then
    echo "Injected download failure" >&2
    exit 22
fi
version="$MOCK_VERSION"
plain="${version#v}"
dir="$MOCK_FIXTURES/$version"

# GitHub release metadata with an uploaded asset and its digest.
github_release() {
    local repo="$1" name="$2" file="$3" digest
    case "${MOCK_DIGEST:-normal}" in
        normal) digest="\"sha256:$(sha256sum -- "$file" | cut -d ' ' -f 1)\"" ;;
        missing) digest=null ;;
        wrong) digest="\"sha256:$(printf '%064d' 0)\"" ;;
    esac
    printf '{"tag_name":"%s","draft":false,"assets":[' "$version"
    printf '{"name":"%s.sig","browser_download_url":"https://github.com/%s/releases/download/%s/%s.sig","state":"uploaded","digest":null},' \
        "$name" "$repo" "$version" "$name"
    printf '{"name":"%s","browser_download_url":"https://github.com/%s/releases/download/%s/%s","state":"uploaded","digest":%s}]}\n' \
        "$name" "$repo" "$version" "$name" "$digest"
}

payload=
case "$url" in
    https://api.github.com/repos/can1357/oh-my-pi/releases/latest|https://api.github.com/repos/yorukot/superfile/releases/latest|https://gitea.com/api/v1/repos/gitea/tea/releases/latest)
        printf '{"name":"release","tag_name":"%s","body":"\\"tag_name\\": \\"v0.0.0-decoy\\""}\n' "$version"
        exit 0 ;;
    https://api.github.com/repos/fiatjaf/nak/releases/latest)
        github_release fiatjaf/nak "nak-$version-linux-amd64" "$dir/nak-$version-linux-amd64"
        exit 0 ;;
    https://api.github.com/repos/DanConwayDev/ngit-cli/releases/latest)
        github_release DanConwayDev/ngit-cli "ngit-$version-x86_64-unknown-linux-musl.tar.gz" \
            "$dir/ngit-$version-x86_64-unknown-linux-musl.tar.gz"
        exit 0 ;;
    # The sources the upstream installers read their newest version from.
    https://downloads.claude.ai/claude-code-releases/stable)
        printf '%s\n' "$plain"
        exit 0 ;;
    https://releases.openai.com/codex/channels/latest)
        printf '{"tag_name":"rust-%s","assets":[]}\n' "$version"
        exit 0 ;;
    https://api.github.com/repos/anomalyco/opencode/releases/latest)
        printf '{"tag_name":"%s"}\n' "$version"
        exit 0 ;;
    https://herdr.dev/latest.json)
        printf '{\n  "version": "%s",\n  "assets": {}\n}\n' "$plain"
        exit 0 ;;
    https://github.com/can1357/oh-my-pi/releases/download/"$version"/omp-linux-x64)
        payload="$dir/omp-linux-x64" ;;
    https://github.com/can1357/oh-my-pi/releases/download/"$version"/SHA256SUMS.txt)
        payload="$dir/SHA256SUMS.txt" ;;
    https://github.com/fiatjaf/nak/releases/download/"$version"/nak-"$version"-linux-amd64)
        payload="$dir/nak-$version-linux-amd64" ;;
    https://github.com/DanConwayDev/ngit-cli/releases/download/"$version"/ngit-"$version"-x86_64-unknown-linux-musl.tar.gz)
        payload="$dir/ngit-$version-x86_64-unknown-linux-musl.tar.gz" ;;
    https://dl.gitea.com/tea/"$plain"/tea-"$plain"-linux-amd64)
        payload="$dir/tea-$plain-linux-amd64" ;;
    https://dl.gitea.com/tea/"$plain"/tea-"$plain"-linux-amd64.sha256)
        payload="$dir/tea-$plain-linux-amd64.sha256" ;;
    https://github.com/yorukot/superfile/releases/download/"$version"/superfile-linux-"$version"-amd64.tar.gz)
        payload="$dir/superfile-linux-$version-amd64.tar.gz"
        [ "${MOCK_BAD_ARCHIVE:-0}" != 1 ] || payload="$MOCK_FIXTURES/bad-archive/superfile-linux-$version-amd64.tar.gz" ;;
    https://github.com/yorukot/superfile/releases/download/"$version"/superfile-"$version"-checksums.txt)
        payload="$dir/superfile-$version-checksums.txt"
        [ "${MOCK_BAD_ARCHIVE:-0}" != 1 ] || payload="$MOCK_FIXTURES/bad-archive/superfile-$version-checksums.txt" ;;
    https://herdr.dev/install.sh) payload="$MOCK_FIXTURES/install-herdr.sh" ;;
    https://claude.ai/install.sh) payload="$MOCK_FIXTURES/install-claude.sh" ;;
    https://opencode.ai/install) payload="$MOCK_FIXTURES/install-opencode.sh" ;;
    https://chatgpt.com/codex/install.sh) payload="$MOCK_FIXTURES/install-codex.sh" ;;
    *) echo "Unmocked URL (network prohibited): $url" >&2; exit 91 ;;
esac
if [ "${MOCK_SUMS:-normal}" = missing-entry ] && [[ "$url" == *.sha256 || "$url" == *checksums.txt ]]; then
    printf '%064d  some-other-file\n' 0 >"${output:-/dev/stdout}"
    exit 0
fi
if [ -n "$output" ]; then
    cp -- "$payload" "$output"
    if [ -n "${MOCK_CORRUPT_MATCH:-}" ] && [[ "$url" == *"$MOCK_CORRUPT_MATCH"* ]]; then
        printf '# corrupted download fixture\n' >>"$output"
    fi
else
    cat -- "$payload"
fi
MOCK

cat >"$mock_bin/mv" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [ "${#args[@]}" -ge 2 ]; then
    source_path="${args[${#args[@]}-2]}"
    destination_path="${args[${#args[@]}-1]}"
    if [ -n "${MOCK_FAIL_PUBLISH_TOOL:-}" ] &&
        [[ "$source_path" == "$MOCK_MANAGED_ROOT"/.stage.* ]] &&
        [ "$destination_path" = "$MOCK_MANAGED_ROOT/$MOCK_FAIL_PUBLISH_TOOL" ] &&
        [ ! -e "$MOCK_MV_FAILURE_SEEN" ]; then
        : >"$MOCK_MV_FAILURE_SEEN"
        echo "Injected publication move failure" >&2
        exit 73
    fi
    if [ -n "${MOCK_FAIL_MOVE_DESTINATION:-}" ] &&
        [ "$destination_path" = "$MOCK_FAIL_MOVE_DESTINATION" ] &&
        [ ! -e "$MOCK_MV_FAILURE_SEEN" ]; then
        : >"$MOCK_MV_FAILURE_SEEN"
        echo "Injected launcher/marker move failure" >&2
        exit 73
    fi
fi
exec /usr/bin/mv "$@"
MOCK

cat >"$mock_bin/uname" <<'MOCK'
#!/bin/sh
if [ "${1:-}" = -m ]; then echo x86_64; else exec /usr/bin/uname "$@"; fi
MOCK

# Installers optionally pause after changing files so tests can interrupt
# them or overlap another action at a known point.
cat >"$fixtures/block.sh" <<'MOCK'
mock_block() {
    if [ -n "${MOCK_BLOCK_FILE:-}" ]; then
        : >"$MOCK_BLOCK_FILE.started"
        tries=0
        while [ ! -e "$MOCK_BLOCK_FILE" ] && [ "$tries" -lt 600 ]; do
            sleep 0.05
            tries=$((tries + 1))
        done
    fi
}
MOCK
cat >"$fixtures/install-herdr.sh" <<'MOCK'
#!/bin/sh
set -eu
. "$MOCK_FIXTURES/block.sh"
mkdir -p "$HERDR_INSTALL_DIR"
cp "$MOCK_FIXTURES/$MOCK_VERSION/herdr" "$HERDR_INSTALL_DIR/herdr"
mock_block
MOCK
cat >"$fixtures/install-claude.sh" <<'MOCK'
#!/bin/sh
set -eu
. "$MOCK_FIXTURES/block.sh"
[ "${1:-}" = stable ] || { echo "expected the stable channel" >&2; exit 2; }
# Like the native build, program files follow XDG_DATA_HOME.
data="${XDG_DATA_HOME:-$HOME/.local/share}"
mkdir -p "$data/claude/versions" "$HOME/.local/bin"
cp "$MOCK_FIXTURES/$MOCK_VERSION/claude" "$data/claude/versions/$MOCK_VERSION"
ln -sfn "$data/claude/versions/$MOCK_VERSION" "$HOME/.local/bin/claude"
printf '{"installMethod":"native","version":"%s"}\n' "$MOCK_VERSION" >"$HOME/.claude.json"
mock_block
if [ "${MOCK_INSTALLER_FAIL:-}" = claude ]; then echo "Injected installer failure" >&2; exit 1; fi
MOCK
cat >"$fixtures/install-opencode.sh" <<'MOCK'
#!/bin/sh
set -eu
# Like the upstream installer, an inherited VERSION pins the release.
version="${VERSION:-$MOCK_VERSION}"
[ -x "$MOCK_FIXTURES/$version/opencode" ] || { echo "no OpenCode release $version" >&2; exit 1; }
mkdir -p "$HOME/.opencode/bin"
cp "$MOCK_FIXTURES/$version/opencode" "$HOME/.opencode/bin/opencode"
if [ "${MOCK_INSTALLER_FAIL:-}" = opencode ]; then echo "Injected installer failure" >&2; exit 1; fi
MOCK
cat >"$fixtures/install-codex.sh" <<'MOCK'
#!/bin/sh
set -eu
# Like the upstream installer, CODEX_HOME relocates and CODEX_RELEASE pins.
codex_home="${CODEX_HOME:-$HOME/.codex}"
version="${CODEX_RELEASE:-$MOCK_VERSION}"
[ -x "$MOCK_FIXTURES/$version/codex" ] || { echo "no Codex release $version" >&2; exit 1; }
[ "${CODEX_NON_INTERACTIVE:-}" = true ] || { echo "installer would prompt" >&2; exit 1; }
root="$codex_home/packages/standalone"
mkdir -p "$root/releases/$version" "${CODEX_INSTALL_DIR:?}"
cp "$MOCK_FIXTURES/$version/codex" "$root/releases/$version/codex"
ln -sfn "$root/releases/$version/codex" "$CODEX_INSTALL_DIR/codex"
if [ "${MOCK_INSTALLER_FAIL:-}" = codex ]; then echo "Injected installer failure" >&2; exit 1; fi
MOCK
chmod 0755 "$mock_bin"/*
# Flatpak answers from a per-case model state; see tests/gear-lever-apps.py.
fake_flatpak="$repo_root/tests/gear-lever-apps.py"
python3 "$fake_flatpak" install-fake-flatpak "$mock_bin" "$fixtures/flatpak-state.json"
prism_id=org.prismlauncher.PrismLauncher
prism_ref="https://dl.flathub.org/repo/appstream/$prism_id.flatpakref"

setup_case() {
    case_root="$test_root/$1"
    case_home="$case_root/home"
    managed_root="$case_root/data/monolith/software"
    state_root="$case_root/state/monolith/software"
    bin_dir="$case_home/.local/bin"
    release_version=v1.0.0
    fail_publish_tool=
    fail_move_destination=
    fail_download_match=
    corrupt_match=
    digest_mode=normal
    sums_mode=normal
    bad_archive=0
    installer_fail=
    block_file=
    extra_env=()
    mkdir -p "$case_home" "$case_root"/{data,state,config,cache,runtime,tmp} "$bin_dir"
    : >"$case_root/curl.log"
    cp -- "$fixtures/flatpak-state.json" "$case_root/flatpak-state.json"
}

monolith_env() {
    monolith_command=(env -i HOME="$case_home" USER=monolith-test LOGNAME=monolith-test
        SHELL=/bin/bash LC_ALL=C PATH="$mock_bin:/usr/bin:/bin"
        XDG_DATA_HOME="$case_root/data" XDG_STATE_HOME="$case_root/state"
        XDG_CONFIG_HOME="$case_root/config" XDG_CACHE_HOME="$case_root/cache"
        XDG_RUNTIME_DIR="$case_root/runtime" TMPDIR="$case_root/tmp"
        MOCK_FIXTURES="$fixtures" MOCK_VERSION="$release_version"
        MOCK_CURL_LOG="$case_root/curl.log" MOCK_CURL_FAIL_MATCH="$fail_download_match"
        MOCK_CORRUPT_MATCH="$corrupt_match" MOCK_DIGEST="$digest_mode" MOCK_SUMS="$sums_mode"
        MOCK_BAD_ARCHIVE="$bad_archive" MOCK_INSTALLER_FAIL="$installer_fail"
        MOCK_BLOCK_FILE="$block_file"
        MOCK_FAIL_PUBLISH_TOOL="$fail_publish_tool" MOCK_MANAGED_ROOT="$managed_root"
        MOCK_FAIL_MOVE_DESTINATION="$fail_move_destination"
        MOCK_MV_FAILURE_SEEN="$case_root/mv-failed"
        MONOLITH_TEST_FLATPAK_STATE="$case_root/flatpak-state.json"
        "${extra_env[@]}" "$manager")
}

run_monolith() {
    monolith_env
    "${monolith_command[@]}" "$@"
}

expect_failure() {
    if run_monolith "$@" >"$case_root/expected-failure.log" 2>&1; then
        fail "command unexpectedly succeeded: $*"
    fi
}

assert_failure_reported() {
    local name="$1"
    grep -Eq "^  $name +Failed" "$case_root/expected-failure.log" || fail "$name failure was not reported"
    if grep -Fxq "Installed $name." "$case_root/expected-failure.log"; then
        fail "$name was reported as installed after a failure"
    fi
}

assert_version() {
    local command="$1" expected="$2" actual
    actual="$("$bin_dir/$command" --version)"
    [ "$actual" = "$expected" ] || fail "expected $expected; got $actual"
}

assert_launcher_under() {
    local command="$1" root="$2"
    [ -L "$bin_dir/$command" ] || fail "$command launcher is not a symlink"
    case "$(readlink -f -- "$bin_dir/$command")" in
        "$root"/*) ;;
        *) fail "$command launcher points outside $root" ;;
    esac
}

assert_no_temp_leaks() {
    local leftovers
    leftovers="$(find "$case_root/tmp" -mindepth 1 -print)"
    [ -z "$leftovers" ] || fail "TMPDIR leaked: $leftovers"
    if [ -d "$managed_root" ]; then
        leftovers="$(find "$managed_root" -mindepth 1 -maxdepth 1 \( -name '.stage.*' -o -name '.rollback.*' \) -print)"
        [ -z "$leftovers" ] || fail "staging/rollback directories leaked: $leftovers"
    fi
    leftovers="$(find "$bin_dir" -maxdepth 1 -name '*.monolith.*' -print)"
    [ -z "$leftovers" ] || fail "temporary launchers leaked: $leftovers"
    if [ -d "$state_root" ]; then
        leftovers="$(find "$state_root" -maxdepth 1 -name '*.managed.tmp.*' -print)"
        [ -z "$leftovers" ] || fail "temporary markers leaked: $leftovers"
    fi
}

# Seed settings, logins, and sessions that installs and removals must keep.
seed_user_data() {
    local path
    for path in "$@"; do
        mkdir -p "$(dirname -- "$path")"
        printf 'user data for %s\n' "$path" >"$path"
        printf '%s\n' "$path" >>"$case_root/seeded-files"
    done
    while IFS= read -r path; do sha256sum -- "$path"; done <"$case_root/seeded-files" >"$case_root/seeded.sha256"
}

assert_user_data_preserved() {
    sha256sum --check --quiet "$case_root/seeded.sha256" || fail 'user data changed'
}

# Replace top-level keys of the case's Flatpak model, such as "apps" or "faults".
flatpak_state() {
    python3 "$fake_flatpak" update-fake-flatpak "$case_root/flatpak-state.json" "$1"
}

# Print each flatpak call the manager made as one line of arguments.
flatpak_calls() {
    [ -f "$case_root/flatpak-state.json.calls" ] || return 0
    python3 -c '
import json, sys
for line in open(sys.argv[1]):
    record = json.loads(line)
    if "argv" in record:
        print(" ".join(record["argv"]))' "$case_root/flatpak-state.json.calls"
}

forget_flatpak_calls() {
    rm -f -- "$case_root/flatpak-state.json.calls"
}

assert_flatpak_called() {
    flatpak_calls >"$case_root/flatpak-calls.log"
    grep -Fxq -- "$1" "$case_root/flatpak-calls.log" || fail "flatpak was not called with: $1"
}

assert_no_flatpak_changes() {
    flatpak_calls >"$case_root/flatpak-calls.log"
    if grep -Eq '^(install|update|uninstall) ' "$case_root/flatpak-calls.log"; then
        fail "unexpected flatpak changes: $(grep -E '^(install|update|uninstall) ' "$case_root/flatpak-calls.log")"
    fi
}

assert_flatpak_modelled() {
    if grep -q '"unmodelled"' "$case_root/flatpak-state.json.calls" 2>/dev/null; then
        fail "unmodelled flatpak calls: $(grep '"unmodelled"' "$case_root/flatpak-state.json.calls")"
    fi
}

# Print Prism Launcher's installations in the model, such as {"user": "11.1.1"}.
prism_installations() {
    python3 -c '
import json, sys
state = json.load(open(sys.argv[1]))
print(json.dumps(state.get("apps", {}).get(sys.argv[2], {}), sort_keys=True))' \
        "$case_root/flatpak-state.json" "$prism_id"
}

write_prism_marker() {
    mkdir -p "$state_root"
    printf 'version=%s\nsource=%s\n' "$1" "$prism_ref" >"$state_root/prismlauncher.managed"
}

assert_prism_listed() {
    run_monolith list >"$case_root/list.log"
    grep -Eq "^Gaming +Prism Launcher +$1\$" "$case_root/list.log" ||
        fail "Prism Launcher is not listed as '$1': $(grep 'Prism Launcher' "$case_root/list.log")"
}

seed_omp_data() {
    mkdir -p "$case_home/.omp/agent/sessions"
    printf 'user configuration\n' >"$case_home/.omp/agent/config.yml"
    printf 'authentication and session database fixture\n' >"$case_home/.omp/agent/agent.db"
    printf 'session fixture\n' >"$case_home/.omp/agent/sessions/session.jsonl"
    cp -a -- "$case_home/.omp" "$case_root/omp-data-before"
}

assert_omp_data_preserved() {
    diff -r -- "$case_root/omp-data-before" "$case_home/.omp" || fail 'OMP user data changed'
}

# Install v1, update to v2, and remove one static tool while user data stays.
static_lifecycle() {
    local tool="$1"
    shift
    local commands=("$@") command
    run_monolith install "$tool"
    for command in "${commands[@]}"; do
        assert_launcher_under "$command" "$managed_root/$tool"
        assert_version "$command" "$command v1.0.0"
    done
    assert_user_data_preserved
    release_version=v2.0.0
    run_monolith update "$tool"
    for command in "${commands[@]}"; do
        assert_launcher_under "$command" "$managed_root/$tool"
        assert_version "$command" "$command v2.0.0"
    done
    assert_user_data_preserved
    run_monolith remove "$tool"
    for command in "${commands[@]}"; do
        assert_absent "$bin_dir/$command"
    done
    assert_absent "$managed_root/$tool"
    assert_absent "$state_root/$tool.managed"
    assert_user_data_preserved
    assert_no_temp_leaks
}

# Install TOOL at the current release and record what a failed update keeps.
snapshot_install() {
    local tool="$1" command
    shift
    run_monolith install "$tool"
    cp -- "$state_root/$tool.managed" "$case_root/$tool.marker-before"
    for command in "$@"; do
        readlink -- "$bin_dir/$command" >"$case_root/$command.link-before"
        cp -- "$(readlink -f -- "$bin_dir/$command")" "$case_root/$command.bytes-before"
        "$bin_dir/$command" --version >"$case_root/$command.version-before"
    done
}

# A failed update must keep the previous program, launchers, and marker.
assert_failed_update_preserves() {
    local tool="$1" name="$2" command
    shift 2
    expect_failure update "$tool"
    assert_failure_reported "$name"
    for command in "$@"; do
        [ "$(readlink -- "$bin_dir/$command")" = "$(cat -- "$case_root/$command.link-before")" ] ||
            fail "$command launcher changed"
        assert_file_equals "$case_root/$command.bytes-before" "$(readlink -f -- "$bin_dir/$command")"
        assert_version "$command" "$(cat -- "$case_root/$command.version-before")"
    done
    assert_file_equals "$case_root/$tool.marker-before" "$state_root/$tool.managed"
    assert_no_temp_leaks
}

display_name() {
    case "$1" in
        claude) echo 'Claude Code' ;;
        codex) echo 'Codex CLI' ;;
        opencode) echo OpenCode ;;
        omp) echo 'Oh My Pi' ;;
        tea) echo Tea ;;
        superfile) echo Superfile ;;
        *) echo "$1" ;;
    esac
}

test_omp_lifecycle() {
    seed_omp_data
    run_monolith install omp
    [ -L "$bin_dir/omp" ] || fail 'OMP launcher is not a symlink'
    [ "$(readlink -f -- "$bin_dir/omp")" = "$managed_root/omp/bin/omp" ] || fail 'wrong OMP launcher target'
    assert_version omp 'omp v1.0.0'
    assert_omp_data_preserved
    release_version=v2.0.0
    run_monolith update omp
    assert_version omp 'omp v2.0.0'
    grep -qx 'version=v2.0.0' "$state_root/omp.managed" || fail 'OMP marker did not update'
    assert_omp_data_preserved
    run_monolith remove omp
    assert_absent "$bin_dir/omp"
    assert_absent "$managed_root/omp"
    assert_absent "$state_root/omp.managed"
    assert_omp_data_preserved
    assert_no_temp_leaks
}

test_omp_unmanaged_launcher() {
    cp -- "$fixtures/v1.0.0/omp" "$case_root/unmanaged-omp"
    ln -s "$case_root/unmanaged-omp" "$bin_dir/omp"
    expect_failure install omp
    [ "$(readlink -- "$bin_dir/omp")" = "$case_root/unmanaged-omp" ] || fail 'external launcher replaced'
    assert_file_equals "$fixtures/v1.0.0/omp" "$case_root/unmanaged-omp"
    assert_absent "$state_root/omp.managed"
    [ ! -s "$case_root/curl.log" ] || fail 'conflicting install downloaded files'
}

test_omp_replaced_launcher() {
    seed_omp_data
    run_monolith install omp
    rm -- "$bin_dir/omp"
    printf 'user replacement\n' >"$bin_dir/omp"
    cp -- "$bin_dir/omp" "$case_root/replacement-before"
    : >"$case_root/curl.log"
    release_version=v2.0.0
    expect_failure update omp
    assert_file_equals "$case_root/replacement-before" "$bin_dir/omp"
    [ ! -s "$case_root/curl.log" ] || fail 'replaced launcher update downloaded files'
    run_monolith remove omp
    assert_file_equals "$case_root/replacement-before" "$bin_dir/omp"
    assert_absent "$managed_root/omp"
    assert_omp_data_preserved
}

test_omp_checksum_failure() {
    seed_omp_data
    snapshot_install omp omp
    release_version=v2.0.0
    corrupt_match=omp-linux-x64
    assert_failed_update_preserves omp 'Oh My Pi' omp
    assert_omp_data_preserved
}

test_failed_publication() {
    run_monolith install nak
    cp -- "$state_root/nak.managed" "$case_root/marker-before"
    release_version=v2.0.0
    fail_publish_tool=nak
    expect_failure update nak
    [ -f "$case_root/mv-failed" ] || fail 'publication fault was not reached'
    assert_version nak 'nak v1.0.0'
    assert_file_equals "$case_root/marker-before" "$state_root/nak.managed"
    assert_no_temp_leaks
    fail_publish_tool=
    run_monolith update nak
    assert_version nak 'nak v2.0.0'
}

assert_failed_commit_preserves_install() {
    local destination="$1" original_launcher
    run_monolith install nak
    cp -- "$state_root/nak.managed" "$case_root/marker-before"
    original_launcher="$(readlink -- "$bin_dir/nak")"
    release_version=v2.0.0
    fail_move_destination="$destination"
    expect_failure update nak
    [ -f "$case_root/mv-failed" ] || fail 'late publication fault was not reached'
    assert_version nak 'nak v1.0.0'
    [ "$(readlink -- "$bin_dir/nak")" = "$original_launcher" ] || fail 'original launcher not restored'
    assert_file_equals "$case_root/marker-before" "$state_root/nak.managed"
    assert_no_temp_leaks
    fail_move_destination=
    run_monolith update nak
    assert_version nak 'nak v2.0.0'
    assert_no_temp_leaks
}

test_failed_launcher_publication() {
    assert_failed_commit_preserves_install "$bin_dir/nak"
}

test_failed_marker_publication() {
    assert_failed_commit_preserves_install "$state_root/nak.managed"
}

test_download_failure_cleanup() {
    fail_download_match=nak-v1.0.0-linux-amd64
    expect_failure install nak
    assert_failure_reported nak
    assert_absent "$bin_dir/nak"
    assert_absent "$state_root/nak.managed"
    assert_no_temp_leaks
    fail_download_match=
    run_monolith install nak
    assert_version nak 'nak v1.0.0'
    assert_no_temp_leaks
}

test_batch_preflight() {
    expect_failure install nak not-a-tool
    assert_absent "$bin_dir/nak"
    [ ! -s "$case_root/curl.log" ] || fail 'invalid install batch performed downloads'
    expect_failure install not-a-tool nak
    [ ! -s "$case_root/curl.log" ] || fail 'invalid install batch performed downloads'
    run_monolith install nak
    cp -- "$state_root/nak.managed" "$case_root/marker-before"
    release_version=v2.0.0
    : >"$case_root/curl.log"
    expect_failure update nak not-a-tool
    expect_failure update nak tea
    assert_version nak 'nak v1.0.0'
    assert_file_equals "$case_root/marker-before" "$state_root/nak.managed"
    [ ! -s "$case_root/curl.log" ] || fail 'invalid update batch performed downloads'
    expect_failure remove nak not-a-tool
    expect_failure remove nak tea
    assert_version nak 'nak v1.0.0'
    assert_file_equals "$case_root/marker-before" "$state_root/nak.managed"
}

test_empty_update_all() {
    run_monolith update >"$case_root/update.log"
    grep -Fxq 'No Monolith-managed software is installed.' "$case_root/update.log" ||
        fail 'empty update did not report that nothing is managed'
    [ ! -s "$case_root/curl.log" ] || fail 'empty update performed downloads'
}

# An update leaves healthy items that already have the newest release alone:
# it reads only release metadata and republishes nothing. Install still
# reinstalls on request, and a failed version lookup never counts as current.
test_update_skips_current_items() {
    local tools=(tea superfile nak ngit omp herdr claude codex opencode) tool name
    local names=(Tea Superfile nak ngit 'Oh My Pi' Herdr 'Claude Code' 'Codex CLI' OpenCode)
    run_monolith install "${tools[@]}"
    for tool in "${tools[@]}"; do
        cp -- "$state_root/$tool.managed" "$case_root/$tool.marker-before"
    done
    : >"$case_root/curl.log"
    run_monolith update >"$case_root/update.log"
    for name in "${names[@]}"; do
        grep -Eq "^  $name +Up to date\$" "$case_root/update.log" || fail "$name was not reported up to date"
    done
    if grep -Ev '/releases/latest$|/claude-code-releases/stable$|/channels/latest$|/latest\.json$' \
        "$case_root/curl.log"; then
        fail 'an update with nothing newer downloaded files'
    fi
    for tool in "${tools[@]}"; do
        assert_file_equals "$case_root/$tool.marker-before" "$state_root/$tool.managed"
    done
    assert_no_temp_leaks

    : >"$case_root/curl.log"
    run_monolith install nak
    grep -q '/nak-v1\.0\.0-linux-amd64$' "$case_root/curl.log" || fail 'install did not download nak again'

    release_version=v2.0.0
    run_monolith update >"$case_root/update.log"
    for name in "${names[@]}"; do
        grep -Eq "^  $name +Updated\$" "$case_root/update.log" || fail "$name was not updated"
    done
    assert_version nak 'nak v2.0.0'
    assert_version claude 'claude v2.0.0'
    assert_version herdr 'herdr v2.0.0'

    fail_download_match=downloads.claude.ai
    run_monolith update claude >"$case_root/update.log" 2>&1
    grep -Fq 'could not check for a newer Claude Code; running its installer' "$case_root/update.log" ||
        fail 'the failed version lookup was not reported'
    grep -Eq '^  Claude Code +Updated$' "$case_root/update.log" || fail 'Claude Code installer did not run'
    assert_no_temp_leaks
}

test_batch_continues_after_failure() {
    fail_download_match=omp-linux-x64
    expect_failure install nak omp herdr
    assert_failure_reported 'Oh My Pi'
    grep -Eq '^  nak +Installed' "$case_root/expected-failure.log" || fail 'nak result missing'
    grep -Eq '^  Herdr +Installed' "$case_root/expected-failure.log" || fail 'Herdr result missing'
    assert_version nak 'nak v1.0.0'
    assert_version herdr 'herdr v1.0.0'
    assert_absent "$bin_dir/omp"
    assert_absent "$state_root/omp.managed"
    # Update-all also continues past a failing item.
    release_version=v2.0.0
    fail_download_match=nak-v2.0.0
    expect_failure update
    assert_failure_reported nak
    assert_version nak 'nak v1.0.0'
    assert_version herdr 'herdr v2.0.0'
    assert_no_temp_leaks
}

test_tea_lifecycle() {
    seed_user_data "$case_root/config/tea/config.yml"
    static_lifecycle tea tea
}

test_superfile_lifecycle() {
    seed_user_data "$case_root/config/superfile/config.toml" "$case_root/data/superfile/state.json"
    static_lifecycle superfile spf
}

test_nak_lifecycle() {
    seed_user_data "$case_home/.config/nak/config.json"
    static_lifecycle nak nak
}

test_ngit_lifecycle() {
    seed_user_data "$case_home/.gitconfig" "$case_root/config/ngit/accounts.json"
    static_lifecycle ngit ngit git-remote-nostr
}

test_herdr_lifecycle() {
    mkdir -p "$case_root/config/herdr"
    printf 'session configuration\n' >"$case_root/config/herdr/config.toml"
    cp -- "$case_root/config/herdr/config.toml" "$case_root/herdr-config-before"
    run_monolith install herdr
    [ -L "$bin_dir/herdr" ] || fail 'Herdr launcher is not managed symlink'
    [ "$(readlink -f -- "$bin_dir/herdr")" = "$managed_root/herdr/bin/herdr" ] || fail 'wrong Herdr launcher target'
    release_version=v2.0.0
    run_monolith update herdr
    assert_version herdr 'herdr v2.0.0'
    run_monolith remove herdr
    assert_absent "$bin_dir/herdr"
    assert_absent "$managed_root/herdr"
    assert_file_equals "$case_root/herdr-config-before" "$case_root/config/herdr/config.toml"
    assert_no_temp_leaks
}

test_digest_failures() {
    local mode
    snapshot_install nak nak
    snapshot_install ngit ngit git-remote-nostr
    release_version=v2.0.0
    for mode in wrong missing; do
        digest_mode="$mode"
        assert_failed_update_preserves nak nak nak
        assert_failed_update_preserves ngit ngit ngit git-remote-nostr
    done
    # A missing digest also blocks a first install.
    run_monolith remove nak
    expect_failure install nak
    assert_absent "$bin_dir/nak"
    assert_absent "$state_root/nak.managed"
    assert_no_temp_leaks
}

test_checksum_list_failures() {
    snapshot_install tea tea
    snapshot_install superfile spf
    release_version=v2.0.0
    corrupt_match=tea-2.0.0-linux-amd64
    assert_failed_update_preserves tea Tea tea
    corrupt_match=superfile-linux-v2.0.0-amd64.tar.gz
    assert_failed_update_preserves superfile Superfile spf
    corrupt_match=
    sums_mode=missing-entry
    assert_failed_update_preserves tea Tea tea
    assert_failed_update_preserves superfile Superfile spf
}

test_bad_archive_and_broken_program() {
    snapshot_install superfile spf
    snapshot_install nak nak
    release_version=v2.0.0
    bad_archive=1
    assert_failed_update_preserves superfile Superfile spf
    bad_archive=0
    release_version=v9.9.9
    assert_failed_update_preserves nak nak nak
}

test_second_ngit_launcher_failure() {
    run_monolith install ngit
    cp -- "$state_root/ngit.managed" "$case_root/marker-before"
    release_version=v2.0.0
    fail_move_destination="$bin_dir/git-remote-nostr"
    expect_failure update ngit
    [ -f "$case_root/mv-failed" ] || fail 'second launcher fault was not reached'
    assert_version ngit 'ngit v1.0.0'
    assert_version git-remote-nostr 'git-remote-nostr v1.0.0'
    assert_launcher_under ngit "$managed_root/ngit"
    assert_launcher_under git-remote-nostr "$managed_root/ngit"
    assert_file_equals "$case_root/marker-before" "$state_root/ngit.managed"
    assert_no_temp_leaks
}

test_regular_file_adoption() {
    local tool
    cp -- "$fixtures/v1.0.0/omp" "$bin_dir/omp"
    cp -- "$fixtures/v1.0.0/ngit" "$bin_dir/ngit"
    cp -- "$fixtures/v1.0.0/git-remote-nostr" "$bin_dir/git-remote-nostr"
    release_version=v2.0.0
    run_monolith install omp ngit
    for tool in omp ngit; do
        [ -f "$state_root/$tool.managed" ] || fail "$tool was not adopted"
    done
    assert_launcher_under omp "$managed_root/omp"
    assert_launcher_under ngit "$managed_root/ngit"
    assert_launcher_under git-remote-nostr "$managed_root/ngit"
    assert_version omp 'omp v2.0.0'
    assert_version git-remote-nostr 'git-remote-nostr v2.0.0'
    assert_no_temp_leaks
}

test_failed_adoption_restores_originals() {
    cp -- "$fixtures/v1.0.0/ngit" "$bin_dir/ngit"
    cp -- "$fixtures/v1.0.0/git-remote-nostr" "$bin_dir/git-remote-nostr"
    cp -- "$bin_dir/ngit" "$case_root/ngit-before"
    cp -- "$bin_dir/git-remote-nostr" "$case_root/remote-before"
    fail_move_destination="$bin_dir/git-remote-nostr"
    expect_failure install ngit
    [ -f "$case_root/mv-failed" ] || fail 'adoption fault was not reached'
    for command in ngit git-remote-nostr; do
        [ -f "$bin_dir/$command" ] && [ ! -L "$bin_dir/$command" ] || fail "$command was not restored as a file"
        [ -x "$bin_dir/$command" ] || fail "$command lost its mode"
    done
    assert_file_equals "$case_root/ngit-before" "$bin_dir/ngit"
    assert_file_equals "$case_root/remote-before" "$bin_dir/git-remote-nostr"
    assert_absent "$state_root/ngit.managed"
    assert_absent "$managed_root/ngit"
    [ ! -s "$case_root/curl.log" ] || fail 'failed adoption continued to a download'
    assert_no_temp_leaks
    fail_move_destination=
    run_monolith install ngit
    assert_launcher_under ngit "$managed_root/ngit"
    assert_version ngit 'ngit v1.0.0'
}

test_claude_broken_launcher_needs_repair() {
    mkdir -p "$case_root/data/claude/versions" "$state_root"
    ln -s "$case_root/data/claude/versions/missing" "$bin_dir/claude"
    printf 'version=1.0.0 (Claude Code)\nsource=https://claude.ai/install.sh\n' >"$state_root/claude.managed"
    run_monolith list >"$case_root/list.log"
    grep -Eq '^AI coding +Claude Code +needs repair' "$case_root/list.log" || fail 'broken Claude launcher looked installed'
    # Repair reinstalls through the native installer and keeps settings.
    seed_user_data "$case_home/.claude/settings.json"
    run_monolith update claude
    assert_version claude 'claude v1.0.0'
    run_monolith list >"$case_root/list.log"
    grep -Eq '^AI coding +Claude Code +installed +1\.0\.0' "$case_root/list.log" || fail 'repaired Claude not listed'
    assert_user_data_preserved
}

test_native_installers() {
    mkdir -p "$case_home/.claude" "$case_root/config/opencode"
    printf 'Claude auth/config\n' >"$case_home/.claude/settings.json"
    printf 'OpenCode config\n' >"$case_root/config/opencode/opencode.json"
    cp -- "$case_home/.claude/settings.json" "$case_root/claude-before"
    cp -- "$case_root/config/opencode/opencode.json" "$case_root/opencode-before"
    run_monolith install claude opencode
    assert_version claude 'claude v1.0.0'
    assert_version opencode 'opencode v1.0.0'
    [ -L "$bin_dir/opencode" ] || fail 'OpenCode launcher missing'
    assert_absent "$bin_dir/install"
    release_version=v2.0.0
    run_monolith update claude opencode
    assert_version claude 'claude v2.0.0'
    assert_version opencode 'opencode v2.0.0'
    assert_absent "$bin_dir/update"
    run_monolith remove claude opencode
    assert_absent "$bin_dir/claude"
    assert_absent "$bin_dir/opencode"
    assert_absent "$case_root/data/claude"
    assert_absent "$case_home/.opencode/bin"
    assert_file_equals "$case_root/claude-before" "$case_home/.claude/settings.json"
    assert_file_equals "$case_root/opencode-before" "$case_root/config/opencode/opencode.json"
    assert_no_temp_leaks
}

test_codex_lifecycle_and_inherited_environment() {
    seed_user_data "$case_home/.codex/auth.json" "$case_home/.codex/config.toml" \
        "$case_home/.codex/sessions/2026/10/01/session.jsonl" "$case_root/config/opencode/opencode.json"
    mkdir -p "$case_root/foreign-codex"
    printf 'foreign profile\n' >"$case_root/foreign-codex/config.toml"
    cp -a -- "$case_root/foreign-codex" "$case_root/foreign-codex-before"
    # Inherited variables must neither relocate nor pin the managed installs.
    extra_env=(CODEX_HOME="$case_root/foreign-codex" CODEX_RELEASE=v1.0.0 VERSION=v1.0.0)
    run_monolith install codex opencode
    assert_launcher_under codex "$case_home/.codex/packages/standalone"
    assert_version codex 'codex v1.0.0'
    assert_version opencode 'opencode v1.0.0'
    release_version=v2.0.0
    run_monolith update codex opencode
    assert_launcher_under codex "$case_home/.codex/packages/standalone"
    assert_version codex 'codex v2.0.0'
    assert_version opencode 'opencode v2.0.0'
    diff -r -- "$case_root/foreign-codex-before" "$case_root/foreign-codex" || fail 'foreign CODEX_HOME changed'
    assert_user_data_preserved
    run_monolith remove codex opencode
    assert_absent "$bin_dir/codex"
    assert_absent "$bin_dir/opencode"
    assert_absent "$case_home/.codex/packages"
    assert_absent "$case_home/.opencode"
    assert_user_data_preserved
    assert_no_temp_leaks
}

test_native_failures_roll_back() {
    local tool
    seed_user_data "$case_home/.claude/settings.json" "$case_home/.codex/auth.json" \
        "$case_root/config/opencode/opencode.json"
    for tool in claude codex opencode; do
        snapshot_install "$tool" "$tool"
    done
    release_version=v2.0.0
    for tool in claude codex opencode; do
        installer_fail="$tool"
        assert_failed_update_preserves "$tool" "$(display_name "$tool")" "$tool"
    done
    installer_fail=
    assert_absent "$case_root/data/claude/versions/v2.0.0"
    # A new program that cannot report its version is rolled back too.
    release_version=v9.9.9
    assert_failed_update_preserves claude 'Claude Code' claude
    assert_failed_update_preserves codex 'Codex CLI' codex
    # So is a failure to publish the marker.
    release_version=v2.0.0
    fail_move_destination="$state_root/claude.managed"
    assert_failed_update_preserves claude 'Claude Code' claude
    [ -f "$case_root/mv-failed" ] || fail 'marker fault was not reached'
    assert_user_data_preserved
    fail_move_destination=
    run_monolith update claude
    assert_version claude 'claude v2.0.0'
}

test_native_adoption() {
    mkdir -p "$case_home/.opencode/bin"
    cp -- "$fixtures/v1.0.0/opencode" "$case_home/.opencode/bin/opencode"
    release_version=v2.0.0
    run_monolith install opencode
    [ -L "$bin_dir/opencode" ] || fail 'adopted OpenCode has no launcher'
    assert_version opencode 'opencode v2.0.0'
    # A launcher the user replaced is never taken over.
    rm -- "$bin_dir/opencode"
    printf '#!/bin/sh\necho mine\n' >"$bin_dir/opencode"
    chmod 0755 "$bin_dir/opencode"
    cp -- "$bin_dir/opencode" "$case_root/opencode-replacement"
    expect_failure update opencode
    grep -Fq "$bin_dir/opencode no longer points to the Monolith-managed copy" "$case_root/expected-failure.log" ||
        fail 'replaced OpenCode launcher was not named'
    assert_file_equals "$case_root/opencode-replacement" "$bin_dir/opencode"
}

test_herdr_legacy_launcher() {
    cp -- "$fixtures/v1.0.0/herdr" "$bin_dir/herdr"
    mkdir -p "$state_root"
    printf 'version=legacy\nsource=https://herdr.dev/install.sh\n' >"$state_root/herdr.managed"
    expect_failure update herdr
    assert_file_equals "$fixtures/v1.0.0/herdr" "$bin_dir/herdr"
    [ ! -s "$case_root/curl.log" ] || fail 'legacy launcher was updated before ownership resolved'
    run_monolith remove herdr
    assert_file_equals "$fixtures/v1.0.0/herdr" "$bin_dir/herdr"
    assert_absent "$state_root/herdr.managed"
}

test_herdr_replaced_launcher() {
    run_monolith install herdr
    rm -- "$bin_dir/herdr"
    cp -- "$fixtures/v2.0.0/herdr" "$case_root/external-herdr"
    ln -s "$case_root/external-herdr" "$bin_dir/herdr"
    expect_failure update herdr
    run_monolith remove herdr
    [ "$(readlink -- "$bin_dir/herdr")" = "$case_root/external-herdr" ] || fail 'replaced Herdr symlink removed'
    assert_file_equals "$fixtures/v2.0.0/herdr" "$case_root/external-herdr"
    assert_absent "$managed_root/herdr"
}

# Older Monolith versions put Herdr directly in ~/.local/bin, where Herdr's
# own updater replaces it in place. On request, adopt that copy, then update.
test_legacy_herdr_adoption() {
    cp -- "$fixtures/v1.0.0/herdr" "$bin_dir/herdr"
    mkdir -p "$state_root"
    printf 'version=herdr 0.9.0\nsource=https://herdr.dev/install.sh\n' >"$state_root/herdr.managed"
    seed_user_data "$case_root/config/herdr/config.toml"
    release_version=v2.0.0
    run_monolith __action adopt herdr
    assert_launcher_under herdr "$managed_root/herdr"
    assert_version herdr 'herdr v2.0.0'
    grep -qx 'version=herdr v2.0.0' "$state_root/herdr.managed" || fail 'Herdr marker did not update'
    run_monolith list >"$case_root/list.log"
    grep -Eq '^AI coding +Herdr +installed' "$case_root/list.log" || fail 'adopted Herdr is not installed'
    assert_user_data_preserved
    assert_no_temp_leaks
}

# A standalone copy that replaced a managed launcher can be adopted as well.
test_replaced_launcher_adoption() {
    run_monolith install nak
    rm -- "$bin_dir/nak"
    cp -- "$fixtures/v1.0.0/nak" "$bin_dir/nak"
    release_version=v2.0.0
    run_monolith __action adopt nak
    assert_launcher_under nak "$managed_root/nak"
    assert_version nak 'nak v2.0.0'
    grep -qx 'version=v2.0.0' "$state_root/nak.managed" || fail 'nak marker did not update'
    assert_no_temp_leaks
}

# Adoption is published before the update starts, so a failed download leaves
# the adopted copy working in the managed layout and ready to update later.
test_adopted_copy_survives_failed_update() {
    cp -- "$fixtures/v1.0.0/herdr" "$bin_dir/herdr"
    mkdir -p "$state_root"
    printf 'version=herdr 0.9.0\nsource=https://herdr.dev/install.sh\n' >"$state_root/herdr.managed"
    release_version=v2.0.0
    fail_download_match=herdr.dev/install.sh
    expect_failure __action adopt herdr
    assert_launcher_under herdr "$managed_root/herdr"
    assert_version herdr 'herdr v1.0.0'
    grep -qx 'version=herdr v1.0.0' "$state_root/herdr.managed" || fail 'adopted version was not recorded'
    assert_no_temp_leaks
    fail_download_match=
    run_monolith update herdr
    assert_version herdr 'herdr v2.0.0'
}

test_adoption_refuses_unusable_copies() {
    mkdir -p "$state_root"
    printf 'version=herdr 0.9.0\nsource=https://herdr.dev/install.sh\n' >"$state_root/herdr.managed"
    cp -- "$state_root/herdr.managed" "$case_root/marker-before"
    # A copy that does not run is left alone.
    cp -- "$fixtures/v9.9.9/herdr" "$bin_dir/herdr"
    expect_failure __action adopt herdr
    assert_file_equals "$fixtures/v9.9.9/herdr" "$bin_dir/herdr"
    # So is a launcher that links to a program elsewhere.
    rm -- "$bin_dir/herdr"
    ln -s "$fixtures/v1.0.0/herdr" "$bin_dir/herdr"
    expect_failure __action adopt herdr
    [ "$(readlink -- "$bin_dir/herdr")" = "$fixtures/v1.0.0/herdr" ] || fail 'linked Herdr launcher was replaced'
    assert_file_equals "$case_root/marker-before" "$state_root/herdr.managed"
    assert_absent "$managed_root/herdr"
    [ ! -s "$case_root/curl.log" ] || fail 'refused adoption downloaded files'
    assert_no_temp_leaks
}

wait_for_file() {
    local path="$1" tries=0
    while [ ! -e "$path" ]; do
        [ "$tries" -lt 400 ] || fail "timed out waiting for $path"
        sleep 0.05
        tries=$((tries + 1))
    done
}

# Start the manager as its own process group so a signal reaches every child.
start_background() {
    set -m
    monolith_env
    "${monolith_command[@]}" "$@" >"$case_root/background.log" 2>&1 &
    background_pid=$!
    set +m
}

finish_background() {
    background_status=0
    wait "$background_pid" || background_status=$?
}

test_overlapping_mutation_is_locked() {
    block_file="$case_root/release-herdr"
    start_background install herdr
    wait_for_file "$block_file.started"
    block_file=
    expect_failure install nak
    grep -Fq 'Another Monolith software operation is running.' "$case_root/expected-failure.log" ||
        fail 'lock conflict was not reported'
    assert_absent "$bin_dir/nak"
    if grep -q nak "$case_root/curl.log"; then fail 'locked action downloaded files'; fi
    : >"$case_root/release-herdr"
    finish_background
    [ "$background_status" -eq 0 ] || fail "first action failed: $(cat "$case_root/background.log")"
    assert_version herdr 'herdr v1.0.0'
    [ -f "$state_root/.lock" ] || fail 'lock file was unlinked'
    run_monolith install nak
    assert_version nak 'nak v1.0.0'
    assert_no_temp_leaks
}

test_interrupt_rolls_back_and_cancels_batch() {
    seed_user_data "$case_home/.claude/settings.json"
    run_monolith install claude nak
    cp -- "$state_root/claude.managed" "$case_root/claude-marker-before"
    cp -- "$state_root/nak.managed" "$case_root/nak-marker-before"
    : >"$case_root/curl.log"
    release_version=v2.0.0
    block_file="$case_root/never"
    start_background update claude nak
    wait_for_file "$block_file.started"
    kill -INT -- "-$background_pid"
    finish_background
    [ "$background_status" -eq 130 ] || fail "interrupted batch exited $background_status"
    grep -Eq '^  Claude Code +Cancelled' "$case_root/background.log" || fail 'cancellation not reported'
    grep -Eq '^  nak +Not run' "$case_root/background.log" || fail 'later item was not skipped'
    if grep -q nak "$case_root/curl.log"; then fail 'cancelled batch continued to nak'; fi
    block_file=
    assert_version claude 'claude v1.0.0'
    assert_absent "$case_root/data/claude/versions/v2.0.0"
    assert_file_equals "$case_root/claude-marker-before" "$state_root/claude.managed"
    assert_version nak 'nak v1.0.0'
    assert_file_equals "$case_root/nak-marker-before" "$state_root/nak.managed"
    assert_user_data_preserved
    assert_no_temp_leaks
}

test_terminate_cleans_staging() {
    run_monolith install herdr
    cp -- "$state_root/herdr.managed" "$case_root/marker-before"
    release_version=v2.0.0
    block_file="$case_root/never"
    start_background update herdr
    wait_for_file "$block_file.started"
    kill -TERM -- "-$background_pid"
    finish_background
    [ "$background_status" -eq 143 ] || fail "terminated update exited $background_status"
    block_file=
    assert_version herdr 'herdr v1.0.0'
    assert_file_equals "$case_root/marker-before" "$state_root/herdr.managed"
    assert_no_temp_leaks
}

# Prism Launcher is the Flathub Flatpak, installed for the user only.
test_prism_lifecycle() {
    local data="$case_home/.var/app/$prism_id/data/PrismLauncher"
    seed_user_data "$data/instances/Vanilla/instance.cfg" "$data/accounts.json"
    assert_prism_listed 'not installed +-'
    run_monolith install prismlauncher
    assert_flatpak_called "install --user --noninteractive --assumeyes $prism_ref"
    [ "$(prism_installations)" = '{"user": "11.1.1"}' ] || fail "unexpected installations: $(prism_installations)"
    grep -qx 'version=11.1.1' "$state_root/prismlauncher.managed" || fail 'Prism Launcher marker has the wrong version'
    grep -qx "source=$prism_ref" "$state_root/prismlauncher.managed" || fail 'Prism Launcher marker has the wrong source'
    assert_prism_listed 'installed +11\.1\.1'

    # Updating everything updates Prism Launcher through Flatpak beside other
    # tools; nak has no newer release, so it is left alone.
    run_monolith install nak
    flatpak_state '{"flathub": {"org.prismlauncher.PrismLauncher": "11.2.0"}}'
    forget_flatpak_calls
    run_monolith update >"$case_root/update.log"
    grep -Eq '^  nak +Up to date$' "$case_root/update.log" || fail 'nak was not reported up to date'
    grep -Eq '^  Prism Launcher +Updated' "$case_root/update.log" || fail 'Prism Launcher was not updated'
    assert_flatpak_called "update --user --noninteractive --assumeyes $prism_id"
    grep -qx 'version=11.2.0' "$state_root/prismlauncher.managed" || fail 'Prism Launcher marker did not update'
    assert_prism_listed 'installed +11\.2\.0'

    # Another updater, such as topgrade's Flatpak step, can update it first;
    # Monolith then reports it current and records the version it finds.
    flatpak_state '{"flathub": {"org.prismlauncher.PrismLauncher": "11.3.0"}, "apps": {"org.prismlauncher.PrismLauncher": {"user": "11.3.0"}}}'
    run_monolith update prismlauncher >"$case_root/update.log"
    grep -Eq '^  Prism Launcher +Up to date$' "$case_root/update.log" || fail 'current Prism Launcher was not reported up to date'
    grep -qx 'version=11.3.0' "$state_root/prismlauncher.managed" || fail 'Prism Launcher marker did not record 11.3.0'

    # Removal uninstalls the app without --delete-data, so its data stays.
    run_monolith remove prismlauncher >"$case_root/remove.log"
    assert_flatpak_called "uninstall --user --noninteractive --assumeyes $prism_id"
    grep -Fq 'Instances, worlds, accounts, and settings were kept.' "$case_root/remove.log" ||
        fail 'removal did not say what it kept'
    assert_absent "$state_root/prismlauncher.managed"
    [ "$(prism_installations)" = '{}' ] || fail "Prism Launcher is still installed: $(prism_installations)"
    assert_prism_listed 'not installed +-'
    assert_user_data_preserved
    assert_flatpak_modelled
    assert_no_temp_leaks
}

test_prism_adopts_user_copy() {
    flatpak_state '{"apps": {"org.prismlauncher.PrismLauncher": {"user": "11.0.0"}}}'
    assert_prism_listed 'external +11\.0\.0'
    run_monolith install prismlauncher >"$case_root/install.log"
    grep -Fq '(1/1) Prism Launcher — Adopt and update' "$case_root/install.log" || fail 'adoption was not announced'
    assert_flatpak_called "update --user --noninteractive --assumeyes $prism_id"
    if grep -q '^install ' "$case_root/flatpak-calls.log"; then
        fail 'adoption installed a second copy'
    fi
    [ "$(prism_installations)" = '{"user": "11.1.1"}' ] || fail "unexpected installations: $(prism_installations)"
    grep -qx 'version=11.1.1' "$state_root/prismlauncher.managed" || fail 'adopted Prism Launcher was not recorded'
    assert_prism_listed 'installed +11\.1\.1'
    assert_flatpak_modelled
}

# A system-wide copy updates with the system's Flatpaks and is never duplicated.
test_prism_leaves_system_copy_alone() {
    flatpak_state '{"apps": {"org.prismlauncher.PrismLauncher": {"system": "11.1.1"}}}'
    assert_prism_listed 'external +11\.1\.1'
    expect_failure install prismlauncher
    assert_failure_reported "Prism Launcher"
    grep -Fq 'already installed system-wide' "$case_root/expected-failure.log" ||
        fail 'the refusal did not explain the system-wide copy'
    assert_absent "$state_root/prismlauncher.managed"

    # A record of a per-user copy that a system-wide one has since replaced.
    write_prism_marker 11.0.0
    assert_prism_listed 'needs repair +recorded 11\.0\.0'
    expect_failure update prismlauncher
    assert_failure_reported "Prism Launcher"
    grep -Fq 'Run monolith remove prismlauncher' "$case_root/expected-failure.log" ||
        fail 'the refusal did not say how to drop the record'
    run_monolith remove prismlauncher >"$case_root/remove.log"
    grep -Fq 'already uninstalled for your account' "$case_root/remove.log" || fail 'removal did not explain itself'
    assert_absent "$state_root/prismlauncher.managed"
    [ "$(prism_installations)" = '{"system": "11.1.1"}' ] || fail "the system-wide copy changed: $(prism_installations)"
    assert_no_flatpak_changes
    assert_flatpak_modelled
}

test_prism_repair_and_failures() {
    # A recorded copy that was uninstalled elsewhere is reinstalled by Repair.
    write_prism_marker 11.0.0
    assert_prism_listed 'needs repair +recorded 11\.0\.0'
    run_monolith install prismlauncher >"$case_root/install.log"
    grep -Fq '(1/1) Prism Launcher — Repair' "$case_root/install.log" || fail 'repair was not announced'
    assert_flatpak_called "install --user --noninteractive --assumeyes $prism_ref"
    grep -qx 'version=11.1.1' "$state_root/prismlauncher.managed" || fail 'repair was not recorded'

    # Failed updates and removals keep the record so they can be retried.
    cp -- "$state_root/prismlauncher.managed" "$case_root/marker-before"
    flatpak_state '{"flathub": {"org.prismlauncher.PrismLauncher": "11.2.0"}, "faults": {"app-update": "fail"}}'
    expect_failure update prismlauncher
    assert_failure_reported "Prism Launcher"
    assert_file_equals "$case_root/marker-before" "$state_root/prismlauncher.managed"
    for fault in fail partial; do
        flatpak_state "{\"faults\": {\"app-uninstall\": \"$fault\"}}"
        expect_failure remove prismlauncher
        assert_failure_reported "Prism Launcher"
        grep -Fq 'kept its record so the removal can be retried' "$case_root/expected-failure.log" ||
            fail "a $fault removal did not keep the record"
        assert_file_equals "$case_root/marker-before" "$state_root/prismlauncher.managed"
    done
    [ "$(prism_installations)" = '{"user": "11.1.1"}' ] || fail "unexpected installations: $(prism_installations)"

    # A failed fresh installation records nothing.
    flatpak_state '{"apps": {}, "faults": {"app-install": "fail"}}'
    rm -f -- "$state_root/prismlauncher.managed"
    expect_failure install prismlauncher
    assert_failure_reported "Prism Launcher"
    assert_absent "$state_root/prismlauncher.managed"
    assert_flatpak_modelled
    assert_no_temp_leaks
}

# A failing Flatpak never breaks listing and never loses Monolith's record.
test_prism_with_broken_flatpak() {
    flatpak_state '{"apps": {"org.prismlauncher.PrismLauncher": {"user": "11.1.1"}}, "faults": {"flatpak": "broken"}}'
    assert_prism_listed 'not installed +-'
    write_prism_marker 11.1.1
    cp -- "$state_root/prismlauncher.managed" "$case_root/marker-before"
    assert_prism_listed 'needs repair +recorded 11\.1\.1'
    expect_failure install prismlauncher
    assert_failure_reported "Prism Launcher"
    expect_failure remove prismlauncher
    assert_failure_reported "Prism Launcher"
    assert_file_equals "$case_root/marker-before" "$state_root/prismlauncher.managed"
    assert_flatpak_modelled
}

passed=0
failed=0
for test in omp_lifecycle omp_unmanaged_launcher omp_replaced_launcher omp_checksum_failure failed_publication \
    failed_launcher_publication failed_marker_publication download_failure_cleanup batch_preflight \
    empty_update_all update_skips_current_items batch_continues_after_failure tea_lifecycle superfile_lifecycle nak_lifecycle \
    ngit_lifecycle herdr_lifecycle digest_failures checksum_list_failures bad_archive_and_broken_program \
    second_ngit_launcher_failure regular_file_adoption failed_adoption_restores_originals \
    claude_broken_launcher_needs_repair native_installers codex_lifecycle_and_inherited_environment \
    native_failures_roll_back native_adoption herdr_legacy_launcher herdr_replaced_launcher \
    legacy_herdr_adoption replaced_launcher_adoption adopted_copy_survives_failed_update \
    adoption_refuses_unusable_copies \
    overlapping_mutation_is_locked interrupt_rolls_back_and_cancels_batch terminate_cleans_staging \
    prism_lifecycle prism_adopts_user_copy prism_leaves_system_copy_alone prism_repair_and_failures \
    prism_with_broken_flatpak; do
    # Do not put the subshell in an if condition: that disables Bash errexit
    # throughout the test function and can conceal a failing manager command.
    set +e
    (set -e; setup_case "$test"; "test_$test") >"$test_root/$test.log" 2>&1
    result=$?
    set -e
    if [ "$result" -eq 0 ]; then
        printf 'PASS %s\n' "$test"
        passed=$((passed + 1))
    else
        printf 'FAIL %s\n' "$test" >&2
        cat -- "$test_root/$test.log" >&2
        if [ -f "$test_root/$test/expected-failure.log" ]; then
            cat -- "$test_root/$test/expected-failure.log" >&2
        fi
        failed=$((failed + 1))
    fi
done
printf '%s passed; %s failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
