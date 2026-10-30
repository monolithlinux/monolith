<div align="center">
  <img src="files/system/usr/share/plymouth/themes/spinner/watermark.png#gh-dark-mode-only" alt="Monolith" width="200"/>

  <p>KDE Plasma on Fedora Atomic, with the CachyOS kernel and a dashboard for the apps Fedora doesn't ship.</p>

  [![Build](https://github.com/monolithlinux/monolith/actions/workflows/build.yml/badge.svg)](https://github.com/monolithlinux/monolith/actions/workflows/build.yml)
</div>

Monolith is built on Fedora Kinoite. Like Kinoite, it's an atomic image: the
system updates as one piece in the background, and if an update ever goes wrong
you can boot the previous version from the boot menu.

On top of Kinoite you get:

- the CachyOS kernel, signed with Monolith's own key so Secure Boot keeps working
- Ghostty with Fish as the terminal, with hundreds of themes ready to go
- `monolith`, a small dashboard that installs and updates the apps Fedora doesn't
  ship, like the Epic Games Launcher, WowUp-CF, r2modman, Prism Launcher, Sidra,
  the Crafting Apps and coding agents
- OpenDeck for Stream Decks, Node.js 24, Wine and the Waywallen wallpaper plugin,
  already installed
- daily automatic updates for the system and Flatpaks, and a weekly nudge to run
  `topgrade` for everything else
- Monolith News, which tells you when something needs your attention

## Pick your edition

| Edition | Image | Choose it if you have |
| --- | --- | --- |
| KDE | `kde` | AMD or Intel graphics, or an older NVIDIA card on Nouveau/NVK |
| KDE NVIDIA | `kde-nvidia` | an NVIDIA card from the GTX 16 or RTX 20 series onward, for NVIDIA's open driver built for the CachyOS kernel |

## Install

Monolith installs on top of an existing Fedora Atomic system, such as Fedora
Kinoite, by switching ("rebasing") it to the Monolith image. Replace `<edition>`
below with `kde` or `kde-nvidia`.

1. Switch to the image once without signature checks. This installs Monolith's
   signing policy:

   ```bash
   rpm-ostree rebase ostree-unverified-registry:ghcr.io/monolithlinux/<edition>:latest
   systemctl reboot
   ```

2. Switch to the signed image, so every update from now on is verified:

   ```bash
   rpm-ostree rebase ostree-image-signed:docker://ghcr.io/monolithlinux/<edition>:latest
   systemctl reboot
   ```

`latest` always points to the newest build for the Fedora version Monolith is on.

## First steps

After the last reboot, there are three things to do.

**Set up your account.** Run this once for each user account that existed before
the switch. It copies any of Monolith's default settings (from `/etc/skel`) that
your account is missing and rebuilds the font cache. If you already have a
different topgrade configuration, it's backed up first.

```bash
monolith adopt
```

**Turn on Secure Boot support.** Monolith signs its kernel and NVIDIA modules with
its own key, which your computer needs to trust once. Skip this if Secure Boot is
off.

```bash
monolith enroll-secure-boot-key
```

Reboot, choose **Enroll MOK → Continue** in the MokManager screen, and type
`monolith` as the password. The password only proves you're at the keyboard.

**Add some apps.** Open the dashboard and install what you like:

```bash
monolith
```

## Staying up to date

The system and your Flatpaks update by themselves every day. A system update
takes effect the next time you boot, and a Flatpak update the next time you open
the app.

Everything else, such as apps from `monolith`, Distrobox containers and tools like
rustup or Claude Code, updates when you run `monolith update`. If you haven't run
it for a week, Monolith reminds you at login.

When something needs you to act, Monolith News opens at login and explains what to
do.

## Monolith Software

`monolith` installs these apps for your account, keeps them updated, and keeps your
settings and data when you remove one.

| Category | Apps |
| --- | --- |
| Gaming | Epic Games Launcher, WowUp-CF, r2modman, Prism Launcher |
| Media | Sidra, Moonfin, Plezy |
| Crafting Apps | PhotoCraft, VectorCraft, FilmCraft, LightCraft, PdfCraft, EffectCraft, DesignCraft, FigCraft, WordCraft, SoundCraft, DeckCraft, CADCraft, GridCraft |
| Communication | Fluxer, Fluxer Canary |
| AI coding | Claude Code, Codex CLI, OpenCode, Oh My Pi, Herdr |
| Developer CLIs | Tea, Superfile |
| Appearance | Ghostty's original theme collection |

The [Monolith Software guide](docs/software.md) explains how each app is installed
and updated, and what removing it keeps.

## System commands

`monolith help` lists Monolith's other commands, for updates, going back to an
earlier build, firmware, Secure Boot, logs and more. You'll also find them under
**System tools** when you run `monolith`. A few worth knowing:

```bash
monolith status             # check this computer and see what needs attention
monolith rollback           # go back to the build you had before the last update
monolith scheduler gaming   # switch on a CPU scheduler tuned for low latency
monolith report-bug         # start a bug report with your system details
```

[Monolith's commands](docs/commands.md) explains each one. The old names `mjust`
and `ujust` still work.

## Checking an image's signature

Every image is signed. To check one yourself, download [`cosign.pub`](cosign.pub)
and run:

```bash
cosign verify --key cosign.pub ghcr.io/monolithlinux/<edition>
```

## More

- [Monolith Software guide](docs/software.md): every app in `monolith`, in detail
- [What's built in](docs/desktop.md): the terminal, OpenDeck, Node.js, Waywallen,
  the update reminder and Monolith News
- [Report a problem](https://github.com/monolithlinux/monolith/issues)
