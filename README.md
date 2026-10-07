<div align="center">
  <img src="files/system/usr/share/plymouth/themes/spinner/watermark.png#gh-dark-mode-only" alt="Monolith" width="200"/>
</div>

[![Build](https://github.com/monolithlinux/monolith/actions/workflows/build.yml/badge.svg)](https://github.com/monolithlinux/monolith/actions/workflows/build.yml)

Monolith is a Fedora Atomic desktop image built with BlueBuild on Fedora Kinoite. It publishes KDE Plasma editions with the CachyOS kernel and a set of desktop and gaming tools.

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
the managed collection, `monolith install ghostty-themes` to download it again,
or `monolith remove ghostty-themes` to remove its unchanged files while keeping
your edits.

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
mjust monolith-adopt
```

Rebased accounts use this to copy missing `/etc/skel` defaults and rebuild the font cache. A different existing topgrade configuration is backed up first.

Run `mjust` to list Monolith's helper commands (updates, firmware, logs, Secure Boot key enrollment, TPM disk unlock). `ujust`, its former name, still works.

## Optional software

Run the interactive per-user software dashboard:

```bash
monolith
```

The dashboard groups software into Developer CLIs, AI coding, Gaming, Media,
and Appearance, and shows each item's status and version. Type to filter, press
Enter to open an item, then choose an explicit action: Install, Update, Repair,
Adopt and update (for a supported copy installed outside Monolith), or Remove.
Removal asks first and keeps settings, logins, sessions, game addons and mods,
Minecraft instances, media downloads, and projects. Esc goes back. **Update installed** updates every Monolith-managed
item; inside a category it updates only that category. The `mjust monolith`,
`mjust software`, and `mjust monolith-software` aliases open the same
dashboard. Without fzf, or with `TERM=dumb`, it uses numbered menus; set
`NO_COLOR` to turn off colour.

The underlying command also supports scripting and troubleshooting:

```bash
monolith list
monolith install codex herdr
monolith install wowup-cf r2modman prismlauncher moonfin plezy
monolith update
monolith remove codex
```

Each item is installed, updated, or removed in its own step. A failing item is
reported and the rest of the batch continues; the command then exits with an
error. Ctrl-C cancels the current item and the rest of the batch. Only one
install, update, or removal runs at a time.

`monolith update`, and **Update** in the dashboard, first check each item for a
newer release. An item that already has it, and needs no repair, is reported as
`Up to date` and left alone, so nothing is downloaded. `monolith install` always
installs the newest release again; only a Flatpak app that already has it is left
as it is.

Running `topgrade` also runs `monolith update`, as its **Monolith software**
step after the other updates. Items that need an update but cannot be updated,
such as WowUp-CF while it is open, are reported as a failure in topgrade's
summary; the rest still update. At each login, Monolith links
`~/.config/topgrade.d/monolith.toml` to the image's copy unless a file already
exists there. To opt out, replace that link with an empty file, or run
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
Commands that you replace yourself are preserved on removal and are never
updated over. When such a command is a standalone copy of the program, for
example a Herdr binary that older Monolith versions installed directly in
`~/.local/bin/herdr`, the item shows `needs repair`: open it in `monolith` and
choose **Adopt this copy and update** to move that copy into the managed layout
and update it, or move the file aside and run `monolith install <tool>`. After
adoption, `herdr update` updates the managed copy in place.

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
Close WowUp-CF before updating or removing it; an update that finds no newer
release leaves it alone, so it can stay open. Removal keeps your WowUp-CF
settings, game installations, and addons.

### r2modman

**Gaming → r2modman** installs [r2modman](https://github.com/ebkr/r2modmanPlus),
the mod manager for Thunderstore games such as Lethal Company, Risk of Rain 2,
and Valheim. As with WowUp-CF, Monolith downloads the official AppImage, checks
it against the SHA256 digest GitHub publishes, and adds it to Gear Lever. An
existing Gear Lever r2modman integration is adopted and updated in place rather
than gaining a second launcher; if it is older than r2modman 3.2.2, which has no
published checksum, start r2modman once so it updates itself, then adopt it.

Monolith installs the newest stable r2modman release and skips prereleases. Use
**Update** in the dashboard or `monolith update r2modman`; r2modman may also
update itself to newer stable releases, which Monolith recognizes. Close
r2modman before updating or removing it; an update that finds no newer release
leaves it alone, so it can stay open. Removal keeps your profiles, mods, and
settings in `~/.config/r2modmanPlus-local`.

### Prism Launcher

**Gaming → Prism Launcher** installs [Prism Launcher](https://prismlauncher.org),
the Minecraft launcher for separate instances, mod loaders, and modpacks, as the
official [Flathub Flatpak](https://flathub.org/apps/org.prismlauncher.PrismLauncher).
Monolith installs it for your account only, so no administrator password or Gear
Lever is needed. The first installation may also download the KDE runtime that it
uses. A Prism Launcher Flatpak already installed for your account is adopted
rather than installed twice.

A system-wide Prism Launcher, for example one installed through Bazaar, already
updates with the system's Flatpaks, so Monolith leaves it alone and does not add
a second copy. To have Monolith manage a per-user copy instead, uninstall the
system-wide one first. Both copies keep their data in the same place, so your
instances stay.

Prism Launcher stays an ordinary Flatpak. Use **Update** in the dashboard or
`monolith update prismlauncher`; Bazaar, `flatpak update`, topgrade, and the
daily automatic Flatpak update keep it current as well. You do not need to close
Prism Launcher first, because an update takes effect the next time it starts.
Removal keeps your instances, worlds, accounts, and settings in
`~/.var/app/org.prismlauncher.PrismLauncher`. Monolith does not move instances
from a non-Flatpak Prism Launcher; to bring them over, copy the folders in
`~/.local/share/PrismLauncher/instances` to
`~/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/instances`.

### Moonfin

**Media → Moonfin** installs [Moonfin](https://moonfin.io), a media client for
Jellyfin and Emby servers. Moonfin is not on Flathub, so Monolith downloads the
Flatpak bundle of the newest
[Moonfin release](https://github.com/Moonfin-Client/Moonfin-Core/releases),
checks it against the SHA256 digest GitHub publishes, and installs it for your
account only, so no administrator password is needed. Moonfin uses the GNOME
runtime, which Flatpak takes from the system's Flatpaks when they have the
version it needs; otherwise the installation downloads it from Flathub, which
Monolith adds to your account's Flatpak remotes if it is missing. A Moonfin
Flatpak already installed for your account is adopted, and Monolith installs the
newest verified release over it unless it already has that release.

A system-wide Moonfin, for example one installed with `flatpak install` without
`--user`, is left alone rather than installed twice. To have Monolith manage
Moonfin, uninstall the system-wide copy first with
`flatpak uninstall --system org.moonfin.linux`. Both copies keep their data in
the same place, so your servers and settings stay.

Flatpak cannot update the bundle, so Bazaar, `flatpak update`, and the daily
automatic Flatpak update leave Moonfin as it is. Monolith updates it instead:
use **Update** in the dashboard, `monolith update moonfin`, or `mjust update`,
which runs topgrade and with it `monolith update`. Moonfin tells you inside the
app when a new release is out; you do not need to download it yourself. When the
newest release is already installed, updating or installing again downloads
nothing. Moonfin can stay open while it updates: the new version starts the next
time you open it. Removal keeps your servers, sign-ins, settings, and downloads
in `~/.var/app/org.moonfin.linux`.

### Plezy

**Media → Plezy** installs [Plezy](https://plezy.app), a media client for Plex,
Jellyfin, and Emby servers. Plezy is not on Flathub either and publishes no
AppImage, so it works like Moonfin: Monolith downloads the Flatpak bundle of the
newest [Plezy release](https://github.com/edde746/plezy/releases), checks it
against the SHA256 digest GitHub publishes, and installs it for your account
only. Plezy uses the freedesktop runtime (`org.freedesktop.Platform` 25.08),
which Flatpak takes from the system's Flatpaks or downloads from Flathub. A
Plezy Flatpak already installed for your account is adopted. A system-wide copy
is left alone; to have Monolith manage Plezy instead, uninstall it first with
`flatpak uninstall --system com.edde746.plezy`.

As with Moonfin, only Monolith updates Plezy: use **Update** in the dashboard,
`monolith update plezy`, or `mjust update`. When the newest release is already
installed, updating or installing again downloads nothing. Plezy can stay open
while it updates. Removal keeps your servers, sign-ins, settings, and downloads
in `~/.var/app/com.edde746.plezy`.

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

KDE editions include Monolith News, a reader for project announcements such as
required actions and notable changes. It lists announcements beside the one you
are reading and marks the ones you have not opened. At login it downloads
[`news/announcements.toml`](news/announcements.toml) from the `main` branch and
opens only when an announcement you have not dismissed is available; closing the
window dismisses what it listed. Open **Monolith News** from the application menu
to read past announcements. To stop the login check, turn off **Show at login** in
the window or disable Monolith News in Plasma's Autostart settings. Dismissals and
read announcements are stored per user in `~/.local/state/monolith/news/`.

To publish an announcement, add an entry with a new `id` at the top of
`news/announcements.toml`; the file's header describes the fields, including the
optional category, **Get it** steps, and note. Merged changes reach users without
an image rebuild, and editing an existing entry does not reopen it for people who
dismissed it. Preview a change before opening a pull request with
`files/kde/usr/bin/monolith-news --preview news/announcements.toml`.

## Secure Boot

Monolith signs the CachyOS kernel and NVIDIA modules with its own Machine Owner Key (MOK). Enroll the public certificate once to enable Secure Boot.

After installing or rebasing, run:

```bash
mjust enroll-monolith-secure-boot-key
```

Reboot, then choose **Enroll MOK → Continue** in MokManager and enter `monolith`.

Skip enrollment when Secure Boot is disabled. The one-time password only confirms local console access.

## Verification

Download `cosign.pub` to verify an image signature:

```bash
cosign verify --key cosign.pub ghcr.io/monolithlinux/<edition>
```
