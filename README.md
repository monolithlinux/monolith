<div align="center">
  <img src="files/system/usr/share/plymouth/themes/spinner/watermark.png#gh-dark-mode-only" alt="Monolith" width="200"/>
</div>

[![Build](https://github.com/monolithlinux/monolith/actions/workflows/build.yml/badge.svg)](https://github.com/monolithlinux/monolith/actions/workflows/build.yml)

Monolith is a Fedora Atomic desktop image built with BlueBuild on Universal Blue. It publishes KDE Plasma editions with the CachyOS kernel and a set of desktop and gaming tools.

## Pick your edition

Choose a standard image for AMD, Intel, or Nouveau/NVK. Use an NVIDIA image for the packaged open driver on Turing-or-newer GPUs.

| Edition | Image | Use this if… |
| --- | --- | --- |
| **KDE** | `kde` | KDE Plasma with the standard Mesa graphics stack. |
| **KDE — NVIDIA** | `kde-nvidia` | KDE Plasma with the NVIDIA open driver built for the CachyOS kernel. |

KDE editions use Ghostty with Fish, the Pure prompt, and JetBrainsMono Nerd Font
at 12 points. First launch creates defaults in `~/.config/ghostty/` when no
Ghostty configuration exists. Existing shell settings and user-installed tools
continue to work, and existing Ghostty configurations are preserved.

The image includes 554 themes: the full Tinted Terminal Ghostty catalog, including
19 Gruvbox variants, plus the four official Catppuccin themes. Browse and preview
them with `ghostty +list-themes`, then set, for example,
`theme = base16-gruvbox-dark-medium` in your Ghostty configuration and reload with
Ctrl+Shift+Comma. Terra's Ghostty package omits the upstream theme collection;
Monolith supplies these catalogs with their MIT licenses.

For the original collection bundled with Ghostty 1.3.1, run
`monolith install ghostty-themes` or choose Ghostty Themes in the Appearance menu
of `monolith`. This downloads Ghostty's official 463-theme bundle for your user,
including names such as `Gruvbox Dark`. The original collection is downloaded
on request rather than included in the image. Existing custom theme files and
your selected theme are preserved. Use `monolith update ghostty-themes` to repair
or refresh the managed collection, or `monolith remove ghostty-themes` to remove
its unchanged files while keeping your edits.

On KDE, Ghostty remembers its last normal window size across logins and reboots.
A KWin script saves the dimensions, and a window rule restores them on launch.
Existing custom rules take priority. Control recording with "Remember Ghostty
Window Size" in Plasma's KWin Scripts settings, and edit or remove "Ghostty:
remember window size" in Window Rules to change restoration. Monolith will not
recreate a removed rule after the first setup.

All images live under `ghcr.io/monolithlinux/`. In the commands below, replace `<edition>` with the image name from the table (for example, `kde` or `kde-nvidia`).

GNOME and COSMIC editions, including their NVIDIA variants, are retired and no longer receive image or ISO builds. Previously published images are historical builds. Users of these or other retired editions must rebase to `kde` or `kde-nvidia` to keep receiving updates:

```bash
# Use kde-nvidia instead if you need the NVIDIA driver.
rpm-ostree rebase ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest
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

The menu contains Developer CLIs, AI coding tools, and Appearance options.
Selecting an unchecked item installs it; selecting a checked item removes its managed program files
while preserving settings and data. The `ujust monolith`, `ujust software`, and
`ujust monolith-software` aliases open the same menu.

The underlying command also supports scripting and troubleshooting:

```bash
monolith list
monolith install codex herdr
monolith install omp
monolith update
monolith remove codex
```

Oh My Pi (`omp`) is available in the AI coding category. Monolith installs its
standalone Linux binary from the [official releases](https://github.com/can1357/oh-my-pi/releases)
and verifies the release checksum; Bun and npm are not required. Use
`monolith update omp` to update it and `monolith remove omp` to uninstall the
managed executable. Your `~/.omp/` settings, authentication, sessions, and project
files are preserved.

Updates to Tea, Superfile, nak, ngit, Oh My Pi, and Herdr keep the previous
installation until the new executable, launchers, and version record have been
published successfully.
Commands that you replace yourself are preserved on removal and must be moved
out of the way before updating. Older Herdr installs that placed a binary
directly in `~/.local/bin/herdr` may show `needs repair`: move that executable
aside, then run `monolith install herdr` to use the managed layout.

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
