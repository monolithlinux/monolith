# vim: set ft=make :

alias upgrade := update

# Update the OS image, Flatpaks, containers, and Monolith software (runs topgrade)
update:
    topgrade

alias changelog := changelogs

# Show the package changes in the pending or booted deployment
changelogs:
    rpm-ostree db diff --changelogs

# Update device firmware
[no-exit-message]
update-firmware:
    fwupdmgr refresh --force
    fwupdmgr get-updates
    fwupdmgr update

alias auto-update := toggle-updates

# Turn the daily automatic OS and Flatpak updates on or off: ujust toggle-updates enable|disable
toggle-updates ACTION="status":
    #!/usr/bin/bash
    set -euo pipefail
    case "{{ ACTION }}" in
      enable)
        sudo systemctl enable --now rpm-ostreed-automatic.timer flatpak-system-update.timer
        systemctl --user unmask flatpak-user-update.timer
        systemctl --user enable --now flatpak-user-update.timer
        echo "Automatic updates enabled." ;;
      disable)
        sudo systemctl disable --now rpm-ostreed-automatic.timer flatpak-system-update.timer
        # The image enables this timer globally, which a per-user disable cannot undo.
        systemctl --user stop flatpak-user-update.timer
        systemctl --user mask flatpak-user-update.timer
        echo "Automatic updates disabled." ;;
      status)
        if systemctl -q is-enabled rpm-ostreed-automatic.timer; then state=enabled; else state=disabled; fi
        echo "Automatic updates are ${state}. Use: ujust toggle-updates enable|disable" ;;
      *)
        echo "Usage: ujust toggle-updates enable|disable" >&2
        exit 1 ;;
    esac

# Remove unused podman images, Flatpaks and old deployments
clean-system:
    #!/usr/bin/bash
    podman image prune -af
    flatpak uninstall --unused
    rpm-ostree cleanup -bm

# Reboot into the UEFI firmware setup
bios:
    #!/usr/bin/bash
    if [ -d /sys/firmware/efi ]; then
      systemctl reboot --firmware-setup
    else
      echo "Rebooting to legacy BIOS from the OS is not supported."
    fi

# Show firmware and board information
bios-info:
    #!/usr/bin/bash
    echo "Manufacturer: $(sudo dmidecode -s baseboard-manufacturer)"
    echo "Product Name: $(sudo dmidecode -s baseboard-product-name)"
    echo "Version: $(sudo dmidecode -s bios-version)"
    echo "Release Date: $(sudo dmidecode -s bios-release-date)"

# Show all messages from this boot
logs-this-boot:
    sudo journalctl --no-hostname -b 0

# Show all messages from the previous boot
logs-last-boot:
    sudo journalctl --no-hostname -b -1

# Show local /etc changes compared with the image defaults
check-local-overrides:
    #!/usr/bin/bash
    diff -r \
      --suppress-common-lines \
      --color="always" \
      --exclude "passwd*" \
      --exclude "group*" \
      --exclude="subgid*" \
      --exclude="subuid*" \
      --exclude="machine-id" \
      --exclude="adjtime" \
      --exclude="fstab" \
      --exclude="system-connections" \
      --exclude="shadow*" \
      --exclude="gshadow*" \
      --exclude="ssh_host*" \
      --exclude="cmdline" \
      --exclude="crypttab" \
      --exclude="hostname" \
      --exclude="localtime" \
      --exclude="locale*" \
      --exclude="*lock" \
      --exclude=".updated" \
      --exclude="*LOCK" \
      --exclude="vconsole*" \
      --exclude="00-keyboard.conf" \
      --exclude="grub" \
      --exclude="system.control*" \
      --exclude="cdi" \
      --exclude="default.target" \
      /usr/etc /etc 2>/dev/null | sed '/Binary\ files\ /d'

# Upload system information to a pastebin for bug reports
device-info:
    #!/usr/bin/bash
    fpaste <(rpm-ostree status) <(fpaste --sysinfo --printonly) <(flatpak list --columns=application,version,options)

# Measure idle power draw
check-idle-power-draw:
    #!/usr/bin/bash
    sudo powerstat -a -r
