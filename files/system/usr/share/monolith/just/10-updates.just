# vim: set ft=make :
# Updates. A recipe's first comment line is its description in `monolith help`.

alias upgrade := update

# Update the system, Flatpaks, apps and dev tools
[group('Updates')]
update:
    topgrade

alias changelog := changelogs

# Show which packages the last update changed
[group('Updates')]
changelogs:
    rpm-ostree db diff --changelogs

# Go back to an earlier build
[group('Updates')]
[positional-arguments]
rollback DATE="":
    "$MONOLITH_LIBEXEC/rollback" "$@"

alias auto-update := toggle-updates

# Turn the daily automatic updates on or off
[group('Updates')]
toggle-updates ACTION="status":
    #!/usr/bin/bash
    set -euo pipefail
    case "{{ ACTION }}" in
      enable)
        sudo systemctl enable --now rpm-ostreed-automatic.timer flatpak-system-update.timer
        systemctl --user unmask flatpak-user-update.timer
        systemctl --user enable --now flatpak-user-update.timer
        echo "Automatic updates are on." ;;
      disable)
        sudo systemctl disable --now rpm-ostreed-automatic.timer flatpak-system-update.timer
        # The image enables this timer globally, which a per-user disable cannot undo.
        systemctl --user stop flatpak-user-update.timer
        systemctl --user mask flatpak-user-update.timer
        echo "Automatic updates are off." ;;
      status)
        if systemctl -q is-enabled rpm-ostreed-automatic.timer; then state=on; else state=off; fi
        echo "Automatic updates are ${state}. Turn them on or off with: monolith toggle-updates enable|disable" ;;
      *)
        echo "Usage: monolith toggle-updates enable|disable" >&2
        exit 1 ;;
    esac

# Update device firmware
[group('Updates')]
update-firmware:
    fwupdmgr refresh --force
    fwupdmgr get-updates
    fwupdmgr update
