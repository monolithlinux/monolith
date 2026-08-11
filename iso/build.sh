#!/usr/bin/bash
# Prepare a published Monolith image as a transient titanoboa live root.
# Based on titanoboa's examples/zirconium/src/build.sh.
set -exo pipefail

{ export PS4='+( ${BASH_SOURCE}:${LINENO} ): ${FUNCNAME[0]:+${FUNCNAME[0]}(): }'; } 2>/dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# livesys/dracut expects writable sysctls.
mount -o remount,rw /proc/sys || true

dnf install -y dracut-live livesys-scripts grub2-efi-x64-cdboot jq

# titanoboa copies this initramfs verbatim, so add live support here.
kernel=$(kernel-install list --json pretty | jq -r '.[] | select(.has_kernel == true) | .version')
DRACUT_NO_XATTR=1 dracut -v --force --zstd --reproducible --no-hostonly \
    --add "dmsquash-live dmsquash-live-autooverlay" \
    "/usr/lib/modules/${kernel}/initramfs.img" "${kernel}"

# Select the livesys session from the image's VARIANT_ID.
variant_id=$(. /usr/lib/os-release && echo "${VARIANT_ID:-}")
if ! [ -d /usr/libexec/livesys/sessions.d ] || ! grep -qs . "/usr/libexec/livesys/sessions.d/livesys-${variant_id}"; then
    echo "no livesys session script for VARIANT_ID='${variant_id}'" >&2
    ls /usr/libexec/livesys/sessions.d >&2 || true
    exit 1
fi
sed -i "s/^livesys_session=.*/livesys_session=${variant_id}/" /etc/sysconfig/livesys
systemctl enable livesys.service livesys-late.service

# Match Fedora live media's passwordless liveuser polkit rule.
mkdir -p /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/50-liveuser.rules << 'EOF'
polkit.addRule(function(action, subject) {
    if (subject.user === "liveuser") {
        return polkit.Result.YES;
    }
});
EOF

# Anaconda WebUI launches Firefox internally, so add it to the live layer only.
dnf install -qy anaconda-live anaconda-webui firefox rsync \
    libblockdev-btrfs libblockdev-lvm libblockdev-dm
mkdir -p /var/lib/rpm-state   # anaconda-webui expects this to exist

# Anaconda profile keyed to our os-release ID (recipe sets ID=monolith).
mkdir -p /etc/anaconda/profile.d
cat >/etc/anaconda/profile.d/monolith.conf <<'EOF'
[Profile]
profile_id = monolith

[Profile Detection]
os_id = monolith

[Storage]
default_scheme = BTRFS
btrfs_compression = zstd:1

[Bootloader]
efi_dir = fedora
menu_auto_hide = True
EOF

# Install the source image from the registry; it is not embedded in the ISO.
: "${INSTALL_IMAGEREF:=ghcr.io/monolithlinux/gnome:latest}"
cat >/usr/share/anaconda/interactive-defaults.ks <<EOF
ostreecontainer --url=${INSTALL_IMAGEREF} --transport=registry --no-signature-verification

# Use the signed origin after installation.
%post --erroronfail --log=/tmp/monolith-origin.log
sed -i 's|^container-image-reference=.*|container-image-reference=ostree-image-signed:docker://${INSTALL_IMAGEREF}|' \
    /ostree/deploy/*/deploy/*.origin || true
%end

# Best-effort copy of pre-staged Flatpaks; first boot remains the fallback.
%post --nochroot --log=/tmp/monolith-flatpak-copy.log
set -x
for base in /mnt/sysroot /mnt/sysimage; do
    [ -d "\$base/ostree/deploy" ] || continue
    tgt=\$(ls -d "\$base"/ostree/deploy/*/deploy/*.0/var/lib 2>/dev/null | head -1)
    [ -n "\$tgt" ] || continue
    rsync -aAXUH --filter='-x security.selinux' /var/lib/flatpak "\$tgt/" && break
done
%end
EOF

# Keep image defaults; only suppress GNOME's welcome dialog in the live session.
if [ "$variant_id" = gnome ]; then
    mkdir -p /usr/share/glib-2.0/schemas
    cat >/usr/share/glib-2.0/schemas/zzzz-monolith-live.gschema.override <<'EOF'
[org.gnome.shell]
welcome-dialog-last-shown-version='4294967295'
EOF
    glib-compile-schemas /usr/share/glib-2.0/schemas
fi

# Materialize /opt links normally created by boot-time tmpfiles.
if [ -d /usr/lib/opt ]; then
    mkdir -p /var/opt
    for d in /usr/lib/opt/*/; do
        ln -sfn "$d" "/var/opt/$(basename "$d")"
    done
fi

# Pre-stage the image's configured system Flatpaks.
/usr/libexec/bluebuild/default-flatpaks/system-flatpak-setup

# Stage Universal Blue's EFI binaries where titanoboa expects them.
mkdir -p /boot/efi
cp -av /usr/lib/efi/*/*/EFI /boot/efi/
cp -v /boot/efi/EFI/fedora/grubx64.efi /boot/efi/EFI/BOOT/fbx64.efi

# Give bootc install enough temporary space in the live environment.
cat >/etc/systemd/system/var-tmp.mount <<'EOF'
[Unit]
Description=Larger tmpfs for /var/tmp on the live system

[Mount]
What=tmpfs
Where=/var/tmp
Type=tmpfs
Options=size=50%%,nr_inodes=1m

[Install]
WantedBy=local-fs.target
EOF
systemctl enable var-tmp.mount

# titanoboa label and GRUB configuration.
mkdir -p /usr/lib/bootc-image-builder
cp "$SCRIPT_DIR/iso.yaml" /usr/lib/bootc-image-builder/iso.yaml
