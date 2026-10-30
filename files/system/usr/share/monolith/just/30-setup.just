# vim: set ft=make :
# Setting up an account, Secure Boot and disk unlocking.

alias monolith-adopt := adopt

# Give an older account Monolith's default settings
[group('Setup')]
adopt:
    #!/usr/bin/bash
    set -euo pipefail
    # Copy defaults missed by accounts created before rebasing to Monolith.
    # Replace foreign topgrade config after backing it up.
    shopt -s dotglob globstar nullglob
    for src in /etc/skel/**/*; do
        [ -f "$src" ] || continue
        rel="${src#/etc/skel/}"
        dest="$HOME/$rel"
        if [ "$rel" = ".config/topgrade.toml" ]; then
            if [ -f "$dest" ] && ! cmp -s "$src" "$dest"; then
                cp -a "$dest" "$dest.pre-monolith"
                echo "Backed up existing $rel to $rel.pre-monolith"
            fi
            install -Dm0644 "$src" "$dest"
            echo "Installed $rel"
        elif [ ! -e "$dest" ]; then
            install -Dm0644 "$src" "$dest"
            echo "Installed $rel"
        fi
    done
    # Rebuild stale pre-rebase font caches.
    rm -rf "$HOME/.cache/fontconfig"
    fc-cache -rf >/dev/null 2>&1
    echo "Rebuilt the user font cache."
    echo "Done. Log out and back in so running apps pick everything up."

alias enroll-monolith-secure-boot-key := enroll-secure-boot-key

# Let Secure Boot trust Monolith's kernel and drivers
[group('Setup')]
enroll-secure-boot-key:
    #!/usr/bin/bash
    set -euo pipefail
    ENROLLMENT_PASSWORD="monolith"
    SECUREBOOT_KEY=/etc/pki/akmods/certs/akmods-monolith.der
    if [ ! -f "$SECUREBOOT_KEY" ]; then
        echo "Secure Boot key not found at $SECUREBOOT_KEY" >&2
        exit 1
    fi
    sudo mokutil --timeout -1
    echo -e "$ENROLLMENT_PASSWORD\n$ENROLLMENT_PASSWORD" | sudo mokutil --import "$SECUREBOOT_KEY"
    echo 'At the next reboot the MokManager UEFI menu appears (QWERTY input).'
    echo 'Select "Enroll MOK", then enter the password "monolith".'

# Unlock your encrypted disk with the TPM at boot
[group('Setup')]
setup-luks-tpm-unlock:
    sudo /usr/libexec/luks-enable-tpm2-autounlock

# Stop unlocking your encrypted disk with the TPM
[group('Setup')]
remove-luks-tpm-unlock:
    sudo /usr/libexec/luks-disable-tpm2-autounlock
