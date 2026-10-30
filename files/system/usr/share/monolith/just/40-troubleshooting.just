# vim: set ft=make :
# When something goes wrong.

alias device-info := report-bug

# Start a bug report with your system details
[group('Troubleshooting')]
report-bug:
    "$MONOLITH_LIBEXEC/report-bug"

# Show all messages from this boot
[group('Troubleshooting')]
logs-this-boot:
    sudo journalctl --no-hostname -b 0

# Show all messages from the previous boot
[group('Troubleshooting')]
logs-last-boot:
    sudo journalctl --no-hostname -b -1

# Show how your /etc differs from the image
[group('Troubleshooting')]
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
