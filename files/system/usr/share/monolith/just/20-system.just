# vim: set ft=make :
# The system: health, edition, CPU scheduler and firmware.

# Check this computer and say what needs attention
[group('System')]
status:
    "$MONOLITH_LIBEXEC/status"

# Switch between the KDE and KDE NVIDIA editions
[group('System')]
[positional-arguments]
edition NAME="":
    "$MONOLITH_LIBEXEC/edition" "$@"

# Pick a CPU scheduler: gaming, powersave, auto, off
[group('System')]
[positional-arguments]
scheduler MODE="" SCHEDULER="":
    "$MONOLITH_LIBEXEC/scheduler" "$@"

# Remove unused containers, Flatpaks and old builds
[group('System')]
clean-system:
    #!/usr/bin/bash
    set -euo pipefail
    . "$MONOLITH_LIBEXEC/common.sh"
    ask "Remove unused container images, Flatpaks and old builds?" || { echo "Nothing was removed."; exit 0; }
    podman image prune -af
    flatpak uninstall --unused
    rpm-ostree cleanup -bm

# Measure idle power draw
[group('System')]
check-idle-power-draw:
    sudo powerstat -a -r

# Restart into the UEFI firmware setup
[group('System')]
bios:
    #!/usr/bin/bash
    set -euo pipefail
    . "$MONOLITH_LIBEXEC/common.sh"
    if [ ! -d /sys/firmware/efi ]; then
      echo "This computer started in legacy BIOS mode, which can't be opened from Linux."
      exit 1
    fi
    ask "Restart into the firmware setup now?" || exit 0
    systemctl reboot --firmware-setup

# Show firmware and board information
[group('System')]
bios-info:
    #!/usr/bin/bash
    echo "Manufacturer: $(sudo dmidecode -s baseboard-manufacturer)"
    echo "Product Name: $(sudo dmidecode -s baseboard-product-name)"
    echo "Version: $(sudo dmidecode -s bios-version)"
    echo "Release Date: $(sudo dmidecode -s bios-release-date)"
