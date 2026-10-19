#!/usr/bin/env bash
# Moving the OpenDeck Flatpak that Monolith Software installed to the built-in
# OpenDeck must keep the user's profiles, never overwrite the built-in app's
# own files, and leave Flatpaks that Monolith did not install alone.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
migration="$repo_root/files/system/usr/libexec/monolith/opendeck-flatpak-migration"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# A flatpak stand-in: OpenDeck counts as installed while $STUB/installed
# exists and as running while $STUB/running exists.
mkdir -p "$test_root/bin"
cat >"$test_root/bin/flatpak" <<'EOF'
#!/usr/bin/env bash
case "$1" in
    ps) [ ! -e "$STUB/running" ] || echo me.amankhanna.opendeck ;;
    info) [ -e "$STUB/installed" ] ;;
    uninstall) rm "$STUB/installed" ;;
    *) exit 1 ;;
esac
EOF
chmod +x "$test_root/bin/flatpak"

# An account with Monolith's record of the Flatpak, which has a profile, a log,
# and Start at login turned on.
account() {
    HOME="$test_root/$1"
    STUB="$HOME/stub"
    sandbox="$HOME/.var/app/me.amankhanna.opendeck"
    marker="$HOME/.local/state/monolith/software/opendeck.managed"
    mkdir -p "$STUB" "${marker%/*}" "$sandbox/config/opendeck/profiles" "$sandbox/data/opendeck/logs"
    printf 'version=2.14.1\n' >"$marker"
    printf '{"autolaunch": true, "background": true}\n' >"$sandbox/config/opendeck/settings.json"
    printf '{"name": "Default"}\n' >"$sandbox/config/opendeck/profiles/sd-1.json"
    printf 'log\n' >"$sandbox/data/opendeck/logs/opendeck.log"
    touch "$STUB/installed"
}

# Run the migration; a directory given as $1 comes first in PATH.
migrate() {
    env -u XDG_CONFIG_HOME -u XDG_DATA_HOME -u XDG_STATE_HOME \
        HOME="$HOME" STUB="$STUB" PATH="${1:+$1:}$test_root/bin:$PATH" "$migration" >/dev/null 2>&1
}

account moved
migrate
cmp -s "$sandbox/config/opendeck/profiles/sd-1.json" "$HOME/.config/opendeck/profiles/sd-1.json" ||
    fail 'the profile was not copied to the built-in OpenDeck'
[ -f "$HOME/.local/share/opendeck/logs/opendeck.log" ] || fail 'the data folder was not copied'
grep -qx 'Exec=/usr/bin/opendeck --hide' "$HOME/.config/autostart/OpenDeck.desktop" ||
    fail 'Start at login was not carried over'
[ ! -e "$STUB/installed" ] || fail 'the Flatpak was not uninstalled'
[ ! -e "$marker" ] || fail "Monolith's record of the Flatpak was kept"
[ -f "$sandbox/config/opendeck/profiles/sd-1.json" ] || fail "the Flatpak's own data was removed"

# Files the built-in OpenDeck already has win; the Flatpak still goes.
account existing
mkdir -p "$HOME/.config/opendeck"
printf '{"autolaunch": false}\n' >"$HOME/.config/opendeck/settings.json"
migrate
grep -qF '"autolaunch": false' "$HOME/.config/opendeck/settings.json" ||
    fail "the built-in OpenDeck's settings were overwritten"
[ ! -e "$HOME/.config/opendeck/profiles" ] || fail 'the Flatpak profiles were merged into existing files'
[ ! -e "$HOME/.config/autostart/OpenDeck.desktop" ] || fail 'Start at login was turned on over the built-in settings'
[ ! -e "$STUB/installed" ] && [ ! -e "$marker" ] || fail 'the Flatpak was kept'

# Start at login stays off when it was off.
account login-off
printf '{"autolaunch": false}\n' >"$sandbox/config/opendeck/settings.json"
migrate
[ -f "$HOME/.config/opendeck/settings.json" ] || fail 'the settings were not copied'
[ ! -e "$HOME/.config/autostart/OpenDeck.desktop" ] || fail 'Start at login was turned on'

# While the Flatpak runs, nothing changes until the next login.
account running
touch "$STUB/running"
migrate
[ ! -e "$HOME/.config/opendeck" ] || fail 'files were copied while OpenDeck was running'
[ -e "$STUB/installed" ] && [ -e "$marker" ] || fail 'the Flatpak was removed while running'

# A Flatpak that Monolith did not install is the user's own.
account unmanaged
rm "$marker"
migrate
[ ! -e "$HOME/.config/opendeck" ] && [ -e "$STUB/installed" ] || fail 'an unmanaged Flatpak was migrated'

# A copy that fails partway keeps the Flatpak and the record, and the next
# login finishes the move.
account interrupted
mkdir -p "$test_root/failing-cp"
printf '#!/bin/sh\nfor last; do :; done\nmkdir -p "$last"\nexit 1\n' >"$test_root/failing-cp/cp"
chmod +x "$test_root/failing-cp/cp"
if migrate "$test_root/failing-cp"; then fail 'a failed copy was reported as success'; fi
[ ! -e "$HOME/.config/opendeck" ] || fail 'a failed copy was put in place'
[ -e "$STUB/installed" ] && [ -e "$marker" ] || fail 'the Flatpak was removed after a failed copy'
migrate
[ -f "$HOME/.config/opendeck/profiles/sd-1.json" ] && [ ! -e "$STUB/installed" ] ||
    fail 'the next login did not finish the move'

printf 'OpenDeck Flatpak migration tests passed\n'
