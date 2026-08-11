<div align="center">
  <img src="files/system/usr/share/plymouth/themes/spinner/watermark.png#gh-dark-mode-only" alt="Monolith" width="200"/>
</div>

[![Build](https://github.com/monolithlinux/monolith/actions/workflows/build.yml/badge.svg)](https://github.com/monolithlinux/monolith/actions/workflows/build.yml)

Monolith is a Fedora Atomic desktop image built with BlueBuild on Universal Blue. It publishes GNOME, KDE, and COSMIC variants with the CachyOS kernel and a shared set of desktop and gaming tools.

## Pick your edition

Choose a standard image for AMD, Intel, or Nouveau/NVK. Use an NVIDIA image for the packaged open driver on Turing-or-newer GPUs.

| Edition | Image | Use this if… |
| --- | --- | --- |
| **GNOME** | `gnome` | GNOME with the standard Mesa graphics stack. |
| **GNOME — NVIDIA** | `gnome-nvidia` | GNOME with the NVIDIA open driver built for the CachyOS kernel. |
| **KDE** | `kde` | KDE Plasma with the standard Mesa graphics stack. |
| **KDE — NVIDIA** | `kde-nvidia` | KDE Plasma with the NVIDIA open driver built for the CachyOS kernel. |
| **COSMIC** | `cosmic` | COSMIC with the standard Mesa graphics stack. |
| **COSMIC — NVIDIA** | `cosmic-nvidia` | COSMIC with the NVIDIA open driver built for the CachyOS kernel. |

All images live under `ghcr.io/monolithlinux/`. In the commands below, replace `<edition>` with the image name from the table (for example, `gnome` or `gnome-nvidia`).

All other historical editions have been retired. Anyone tracking an image not listed above must choose one of these six images to keep receiving updates:

```bash
# Replace gnome with the desired image name from the table.
rpm-ostree rebase ostree-image-signed:docker://ghcr.io/monolithlinux/gnome:latest
systemctl reboot
```

## Rebasing

To rebase an existing Fedora Atomic installation:

- Install Monolith's signing policy with the unsigned image:
  ```
  rpm-ostree rebase ostree-unverified-registry:ghcr.io/monolithlinux/<edition>:latest
  ```
- Reboot:
  ```
  systemctl reboot
  ```
- Switch to the signed image:
  ```
  rpm-ostree rebase ostree-image-signed:docker://ghcr.io/monolithlinux/<edition>:latest
  ```
- Reboot again:
  ```
  systemctl reboot
  ```

`latest` tracks the newest build for the Fedora version set in `recipes/recipe-<edition>.yml`.

After the final reboot, run the adoption helper once per user account:

```
ujust monolith-adopt
```

Rebased accounts use this to copy missing `/etc/skel` defaults and rebuild the font cache. A different existing topgrade configuration is backed up first.

## Optional software

Run the interactive per-user software manager:

```bash
monolith
```

The menu contains Developer CLIs and AI coding tools. Selecting an unchecked
item installs it; selecting a checked item removes its managed program files
while preserving settings and data. The `ujust monolith`, `ujust software`, and
`ujust monolith-software` aliases open the same menu.

The underlying command also supports scripting and troubleshooting:

```bash
monolith list
monolith install codex herdr
monolith update
monolith remove codex
```

## Secure Boot

Monolith signs the CachyOS kernel and NVIDIA modules with its own Machine Owner Key (MOK). Enroll the public certificate once to enable Secure Boot.

After installing or rebasing, run:

```bash
ujust enroll-monolith-secure-boot-key
```

Reboot, then choose **Enroll MOK → Continue** in MokManager and enter `monolith`.

Skip enrollment when Secure Boot is disabled. The one-time password only confirms local console access.

## Verification

Download `cosign.pub` to verify an image signature:

```bash
cosign verify --key cosign.pub ghcr.io/monolithlinux/<edition>
```
