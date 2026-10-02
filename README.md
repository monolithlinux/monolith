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

Run the interactive per-user software dashboard:

```bash
monolith
```

The dashboard groups software into Developer CLIs, AI coding, Gaming, and
Appearance, and shows each item's status and version. Type to filter, press
Enter to open an item, then choose an explicit action: Install, Update, Repair,
Adopt and update (for a supported copy installed outside Monolith), or Remove.
Removal asks first and keeps settings, logins, sessions, addon data, and
projects. Esc goes back. **Update installed** updates every Monolith-managed
item; inside a category it updates only that category. The `ujust monolith`,
`ujust software`, and `ujust monolith-software` aliases open the same
dashboard. Without fzf, or with `TERM=dumb`, it uses numbered menus; set
`NO_COLOR` to turn off colour.

The underlying command also supports scripting and troubleshooting:

```bash
monolith list
monolith install codex herdr
monolith install wowup-cf
monolith update
monolith remove codex
```

Each item is installed, updated, or removed in its own step. A failing item is
reported and the rest of the batch continues; the command then exits with an
error. Ctrl-C cancels the current item and the rest of the batch. Only one
install, update, or removal runs at a time.

Running `topgrade` also runs `monolith update`, as its **Monolith software**
step after the other updates. Items that cannot update, such as WowUp-CF while
it is open, are reported as a failure in topgrade's summary; the rest still
update. At each login, Monolith links `~/.config/topgrade.d/monolith.toml` to
the image's copy unless a file already exists there. To opt out, replace that
link with an empty file, or run
`ln -s /dev/null ~/.config/user-tmpfiles.d/monolith-topgrade.conf` and then
delete the link.

Oh My Pi (`omp`) is available in the AI coding category. Monolith installs its
standalone Linux binary from the [official releases](https://github.com/can1357/oh-my-pi/releases)
and verifies the release checksum; Bun and npm are not required. Use
`monolith update omp` to update it and `monolith remove omp` to uninstall the
managed executable. Your `~/.omp/` settings, authentication, sessions, and project
files are preserved.

Tea, Superfile, and Oh My Pi downloads are checked against their published
SHA256 checksums, and nak and ngit downloads against the SHA256 digests GitHub
records for their release files. Updates to Tea, Superfile, nak, ngit, Oh My Pi,
and Herdr keep the previous installation until the new executable, launchers,
and version record have been published successfully. Claude Code, Codex CLI,
and OpenCode use their official installers; if an installer or the new
version's start-up check fails, Monolith restores the previous program files,
launcher, and version record.
Commands that you replace yourself are preserved on removal and must be moved
out of the way before updating. Older Herdr installs that placed a binary
directly in `~/.local/bin/herdr` may show `needs repair`: move that executable
aside, then run `monolith install herdr` to use the managed layout.

### WowUp-CF

**Gaming → WowUp-CF** installs the CurseForge edition of the WowUp World of
Warcraft addon manager, including World of Warcraft: Forever support. Monolith
downloads the official AppImage from the
[WowUp-CF releases](https://github.com/WowUp/WowUp.CF/releases), checks it
against the SHA256 digest GitHub publishes, and adds it to Gear Lever, so it
appears in the application menu like other Gear Lever apps. An existing Gear
Lever WowUp-CF integration is adopted and updated in place rather than gaining a
second launcher. If Gear Lever is missing, Monolith asks before installing its
Flathub Flatpak for your account.

Until a compatible 2.24.0 or newer stable release exists, Monolith follows only
the 2.24.0 beta series, starting with beta.6. Once such a stable release is
available, it switches to stable releases and does not enroll you in later beta
series. Monolith sets WowUp-CF's own Application Release Channel to Stable and
leaves addon release settings unchanged. Use **Update** in the dashboard or
`monolith update wowup-cf` for application updates; Monolith clears any Gear
Lever update source for WowUp-CF so that only one updater applies this policy.
Close WowUp-CF before updating or removing it. Removal keeps your WowUp-CF
settings, game installations, and addons.

### Waywallen wallpapers

Both KDE images include the [Waywallen KDE companion plugin](https://github.com/waywallen/waywallen-display).
The image build installs a pinned, checksum-verified upstream package and its
native QML module system-wide. The Waywallen application remains optional:
install `org.waywallen.waywallen` from Flathub through Bazaar.

After booting the updated image, launch Waywallen, right-click the desktop, open
**Configure Desktop and Wallpaper…**, select **Waywallen** as the wallpaper type,
and apply it. Repeat for each display you want Waywallen to control. No RPM
layering or separate companion-plugin installation is needed. Monolith leaves
your existing wallpaper selection and autostart settings unchanged.

An existing per-user `org.waywallen.kde` installation takes precedence over the
image's copy. To use the bundled version, remove only that user-installed plugin
with `kpackagetool6 --type Plasma/Wallpaper --remove org.waywallen.kde` (without
`sudo` or `--global`), then log out and back in.

## Monolith News

KDE editions include Monolith News, a small window for project announcements such
as required actions and notable changes. At login it downloads
[`news/announcements.toml`](news/announcements.toml) from the `main` branch and
opens only when an announcement you have not dismissed is available; closing the
window dismisses what it showed. Open **Monolith News** from the application menu
to read past announcements. To stop the login check, clear **Check for
announcements at login** in the window or disable Monolith News in Plasma's
Autostart settings. Dismissals are stored per user in
`~/.local/state/monolith/news/`.

To publish an announcement, add an entry with a new `id` at the top of
`news/announcements.toml`; the file's header describes the fields. Merged changes
reach users without an image rebuild, and editing an existing entry does not
reopen it for people who dismissed it. Preview a change before opening a pull
request with `files/kde/usr/bin/monolith-news --preview news/announcements.toml`.

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
