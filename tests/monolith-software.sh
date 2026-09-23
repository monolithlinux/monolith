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

for version in v1.0.0 v2.0.0; do
    mkdir -p "$fixtures/$version"
    for tool in omp nak herdr claude opencode; do
        printf '#!/bin/sh\nprintf "%%s\\n" "%s %s"\n' "$tool" "$version" >"$fixtures/$version/$tool"
        chmod 0755 "$fixtures/$version/$tool"
    done
    cp -- "$fixtures/$version/omp" "$fixtures/$version/omp-linux-x64"
    (cd "$fixtures/$version" && sha256sum omp-linux-x64 >SHA256SUMS.txt)
done

cat >"$mock_bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
output= url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o|--output) output="$2"; shift 2 ;;
        --retry|--retry-delay|--connect-timeout|--max-time) shift 2 ;;
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
case "$url" in
    https://api.github.com/repos/can1357/oh-my-pi/releases/latest|https://api.github.com/repos/fiatjaf/nak/releases/latest)
        printf '{"tag_name":"%s"}\n' "$MOCK_VERSION"
        exit 0 ;;
    https://github.com/can1357/oh-my-pi/releases/download/"$MOCK_VERSION"/omp-linux-x64)
        payload="$MOCK_FIXTURES/$MOCK_VERSION/omp-linux-x64" ;;
    https://github.com/can1357/oh-my-pi/releases/download/"$MOCK_VERSION"/SHA256SUMS.txt)
        payload="$MOCK_FIXTURES/$MOCK_VERSION/SHA256SUMS.txt" ;;
    https://github.com/fiatjaf/nak/releases/download/"$MOCK_VERSION"/nak-"$MOCK_VERSION"-linux-amd64)
        payload="$MOCK_FIXTURES/$MOCK_VERSION/nak" ;;
    https://herdr.dev/install.sh) payload="$MOCK_FIXTURES/install-herdr.sh" ;;
    https://claude.ai/install.sh) payload="$MOCK_FIXTURES/install-claude.sh" ;;
    https://opencode.ai/install) payload="$MOCK_FIXTURES/install-opencode.sh" ;;
    *) echo "Unmocked URL (network prohibited): $url" >&2; exit 91 ;;
esac
if [ -n "$output" ]; then
    cp -- "$payload" "$output"
    if [ "$MOCK_CORRUPT_OMP" = 1 ] && [[ "$url" == */omp-linux-x64 ]]; then
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

cat >"$fixtures/install-herdr.sh" <<'MOCK'
#!/bin/sh
set -eu
mkdir -p "$HERDR_INSTALL_DIR"
cp "$MOCK_FIXTURES/$MOCK_VERSION/herdr" "$HERDR_INSTALL_DIR/herdr"
MOCK
cat >"$fixtures/install-claude.sh" <<'MOCK'
#!/bin/sh
set -eu
mkdir -p "$HOME/.local/share/claude/bin" "$HOME/.local/bin"
cp "$MOCK_FIXTURES/$MOCK_VERSION/claude" "$HOME/.local/share/claude/bin/claude"
ln -sfn "$HOME/.local/share/claude/bin/claude" "$HOME/.local/bin/claude"
MOCK
cat >"$fixtures/install-opencode.sh" <<'MOCK'
#!/bin/sh
set -eu
mkdir -p "$HOME/.opencode/bin"
cp "$MOCK_FIXTURES/$MOCK_VERSION/opencode" "$HOME/.opencode/bin/opencode"
MOCK
chmod 0755 "$mock_bin"/*

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
    corrupt_omp=0
    mkdir -p "$case_home" "$case_root"/{data,state,config,cache,runtime,tmp} "$bin_dir"
    : >"$case_root/curl.log"
}

run_monolith() {
    env -i HOME="$case_home" USER=monolith-test LOGNAME=monolith-test \
        SHELL=/bin/bash LC_ALL=C PATH="$mock_bin:/usr/bin:/bin" \
        XDG_DATA_HOME="$case_root/data" XDG_STATE_HOME="$case_root/state" \
        XDG_CONFIG_HOME="$case_root/config" XDG_CACHE_HOME="$case_root/cache" \
        XDG_RUNTIME_DIR="$case_root/runtime" TMPDIR="$case_root/tmp" \
        MOCK_FIXTURES="$fixtures" MOCK_VERSION="$release_version" \
        MOCK_CURL_LOG="$case_root/curl.log" MOCK_CURL_FAIL_MATCH="$fail_download_match" \
        MOCK_CORRUPT_OMP="$corrupt_omp" \
        MOCK_FAIL_PUBLISH_TOOL="$fail_publish_tool" MOCK_MANAGED_ROOT="$managed_root" \
        MOCK_FAIL_MOVE_DESTINATION="$fail_move_destination" \
        MOCK_MV_FAILURE_SEEN="$case_root/mv-failed" \
        "$manager" "$@"
}

expect_failure() {
    if run_monolith "$@" >"$case_root/expected-failure.log" 2>&1; then
        fail "command unexpectedly succeeded: $*"
    fi
}

assert_version() {
    local command="$1" expected="$2" actual
    actual="$("$bin_dir/$command" --version)"
    [ "$actual" = "$expected" ] || fail "expected $expected; got $actual"
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
    run_monolith install omp
    cp -- "$state_root/omp.managed" "$case_root/marker-before"
    release_version=v2.0.0
    corrupt_omp=1
    expect_failure update omp
    assert_version omp 'omp v1.0.0'
    assert_file_equals "$case_root/marker-before" "$state_root/omp.managed"
    assert_omp_data_preserved
    assert_no_temp_leaks
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
    assert_absent "$case_home/.local/share/claude"
    assert_absent "$case_home/.opencode/bin"
    assert_file_equals "$case_root/claude-before" "$case_home/.claude/settings.json"
    assert_file_equals "$case_root/opencode-before" "$case_root/config/opencode/opencode.json"
    assert_no_temp_leaks
}

passed=0
failed=0
for test in omp_lifecycle omp_unmanaged_launcher omp_replaced_launcher omp_checksum_failure failed_publication \
    failed_launcher_publication failed_marker_publication download_failure_cleanup batch_preflight \
    herdr_lifecycle herdr_legacy_launcher \
    herdr_replaced_launcher native_installers; do
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
