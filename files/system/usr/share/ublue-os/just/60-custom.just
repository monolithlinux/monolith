# Monolith user helpers (ujust).
# The enrollment recipe has a distinct name to avoid the uBlue recipe imported
# earlier from 00-default.just.

# Per-user optional software manager.
alias software := monolith
alias monolith-software := monolith

monolith:
    monolith

# Adopt an existing home directory after rebasing to Monolith from another image: install the skel defaults new accounts get and rebuild the font cache.
monolith-adopt:
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

# Enroll the MOK after rebasing instead of installing from an ISO.
enroll-monolith-secure-boot-key:
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
