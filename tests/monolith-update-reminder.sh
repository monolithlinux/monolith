#!/usr/bin/env bash
# The login reminder appears when topgrade has not finished a run for a week, at
# most once a week, and not before an account has been tracked for a week.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
reminder="$repo_root/files/system/usr/libexec/monolith/update-reminder"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# Stand-ins for the desktop. notify-send logs what it shows and prints the
# button named in $test_root/click; ghostty logs what it runs; busctl finds the
# notification service unless $test_root/no-notifications exists.
bin="$test_root/bin"
log="$test_root/log"
mkdir -p "$bin"
cat >"$bin/notify-send" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" >>"$log/notify-send"
cat "$test_root/click" 2>/dev/null || true
EOF
cat >"$bin/ghostty" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"$log/ghostty"
EOF
cat >"$bin/busctl" <<EOF
#!/usr/bin/env bash
[ ! -e "$test_root/no-notifications" ]
EOF
printf '#!/usr/bin/env bash\n' >"$bin/sleep"
chmod +x "$bin"/*

account=0
new_account() {
    account=$((account + 1))
    home="$test_root/home$account"
    state="$home/.local/state/monolith/update-reminder"
    mkdir -p "$home"
}
# run [--record]: one login check, or the post command after a topgrade run.
run() {
    rm -rf "$log"
    mkdir -p "$log"
    env -u XDG_STATE_HOME PATH="$bin:$PATH" HOME="$home" "$reminder" "$@"
}
age() { mkdir -p "$state"; touch -d "$2 days ago" "$state/$1"; }
shown() { [ -s "$log/notify-send" ]; }
just_written() { [ -n "$(find "$state/$1" -mmin -1)" ]; }

# A new account starts counting instead of being reminded at once.
new_account
run
! shown || fail 'reminded on a new account'
[ -e "$state/last-reminder" ] || fail 'did not start counting on a new account'

age last-reminder 6
run
! shown || fail 'reminded before a week had passed'

# A week without a recorded update brings the reminder, once.
age last-reminder 7
run
shown || fail 'no reminder after a week without an update'
grep -Fq 'over 7 days since' "$log/notify-send" || fail 'wrong age without a recorded update'
just_written last-reminder || fail 'the reminder was not recorded'
run
! shown || fail 'reminded twice in a week'

# A finished topgrade run postpones the reminder by a week.
new_account
age last-reminder 30
run --record
just_written last-update || fail '--record did not note the update'
run
! shown || fail 'reminded right after an update'

age last-update 9
run
shown || fail 'no reminder nine days after an update'
grep -Fq 'It has been 9 days since' "$log/notify-send" || fail 'wrong age of the last update'

age last-reminder 2
run
! shown || fail 'reminded again two days after a reminder'

# Update now opens a terminal that stays open after topgrade.
age last-reminder 7
: >"$test_root/click"
run
[ ! -e "$log/ghostty" ] || fail 'dismissing the reminder opened a terminal'
age last-reminder 7
printf 'update\n' >"$test_root/click"
run
[ "$(cat "$log/ghostty")" = '--wait-after-command=true -e topgrade' ] ||
    fail "Update now ran: $(cat "$log/ghostty" 2>/dev/null)"

# Without a notification service, the next login tries again.
age last-reminder 7
: >"$test_root/no-notifications"
run
! shown || fail 'notified without a notification service'
! just_written last-reminder || fail 'a reminder nobody saw postponed the next one'

printf 'update reminder tests passed\n'
