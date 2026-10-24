# Monolith Software

`monolith` installs and looks after apps that aren't part of the Monolith image:
games and mod managers, media players, the Crafting Apps, chat apps and coding
tools. Everything installs into your own account, so you don't need an
administrator password.

- [Using the dashboard](#using-the-dashboard)
- [From the command line](#from-the-command-line)
- [How updates work](#how-updates-work)
- [What Monolith keeps safe](#what-monolith-keeps-safe)
- [How apps are installed](#how-apps-are-installed)
- Apps: [Gaming](#gaming) · [Media](#media) · [Crafting Apps](#crafting-apps) ·
  [Communication](#communication) · [Appearance](#appearance) ·
  [AI coding and developer tools](#ai-coding-and-developer-tools)

## Using the dashboard

Open a terminal and run:

```bash
monolith
```

`mjust monolith`, `mjust software` and `mjust monolith-software` open the same
dashboard.

Apps are grouped into Developer CLIs, AI coding, Gaming, Media, Crafting Apps,
Communication and Appearance, and each one shows whether it's installed and which
version you have. Type to filter, press Enter to open an app, then choose what to
do: Install, Update, Repair, Adopt and update, or Remove. Esc takes you back.

**Update installed** updates everything Monolith manages. Inside a category, it
updates only that category.

Without fzf, or with `TERM=dumb`, the dashboard uses numbered menus instead. Set
`NO_COLOR` to turn colour off.

## From the command line

The same things work without the dashboard, which is handy for scripts:

```bash
monolith list                  # every app and its state
monolith install codex herdr   # install one or more apps
monolith update                # update everything Monolith manages
monolith update sidra          # update just one
monolith remove codex
```

`monolith help` lists the ID of every app, such as `prismlauncher` or
`epic-games`.

Each app is handled in its own step. If one fails, Monolith reports it, carries on
with the rest, and exits with an error at the end. Ctrl-C cancels the current app
and everything after it. Only one install, update or removal runs at a time.

## How updates work

`monolith update` first checks each app for a newer release. If an app already has
it and doesn't need repair, it shows `Up to date` and nothing is downloaded.
`monolith install` always installs the newest release again, except for a Flatpak
app that already has it.

`topgrade` runs `monolith update` as well, as its **Monolith software** step after
its other updates, so running `topgrade` or `mjust update` covers everything. If an
app needs an update but can't get one right now, such as WowUp-CF while it's open,
topgrade lists it as failed in its summary and still updates the rest.

At each login, Monolith links `~/.config/topgrade.d/monolith.toml` to the image's
copy, unless you already have a file there. To opt out, replace that link with an
empty file, or run
`ln -s /dev/null ~/.config/user-tmpfiles.d/monolith-topgrade.conf` and then delete
the link.

## What Monolith keeps safe

- Removing an app asks first, and keeps your settings, sign-ins, sessions, game
  addons and mods, Minecraft instances, media downloads and projects.
- Tea, Superfile and Oh My Pi, and every AppImage and Flatpak bundle Monolith
  takes from a GitHub release, are checked against the SHA256 checksum the project
  publishes. The Epic Games Launcher is the one exception, explained in
  [its section](#epic-games-launcher).
- Updates to Tea, Superfile, Oh My Pi and Herdr keep the old version in place
  until the new one is completely set up.
- Claude Code, Codex CLI and OpenCode use their official installers. If an
  installer fails, or the new version doesn't start, Monolith puts the previous
  version back.
- A command you replaced with your own is never updated over, and stays when you
  remove the app.

### Copies installed outside Monolith

Sometimes a command is a standalone copy of an app that Monolith didn't install,
for example a Herdr binary that older Monolith versions put straight into
`~/.local/bin/herdr`. The app then shows `needs repair`. Open it in `monolith` and
choose **Adopt this copy and update** to move it into Monolith's layout and update
it, or move the file aside and run `monolith install <app>`. After adoption,
`herdr update` updates the managed copy in place.

## How apps are installed

Monolith uses three kinds of installs, depending on what each project publishes.

**Flathub Flatpaks** (Prism Launcher, Fluxer). These stay ordinary Flatpaks, so
Bazaar, `flatpak update`, topgrade and the daily automatic Flatpak update all keep
them current, alongside `monolith update`.

**Flatpak bundles from GitHub** (Moonfin, Plezy, the Crafting Apps). These apps
aren't on Flathub, so Monolith downloads the Flatpak bundle from each project's
releases and installs it. Flatpak can't update a bundle by itself, so Bazaar,
`flatpak update` and the daily Flatpak update leave them alone. Only Monolith
updates them: use **Update** in the dashboard, `monolith update`, or `mjust update`.
If the app needs a runtime, Flatpak takes it from the system's Flatpaks when they
have the right version, and otherwise downloads it from Flathub. Monolith adds
Flathub to your account's Flatpak sources if it's missing.

**AppImages through Gear Lever** (WowUp-CF, r2modman, Sidra, the Epic Games
Launcher). Monolith downloads the official AppImage and hands it to Gear Lever,
which adds it to your application menu like any other app. If Gear Lever is
missing, Monolith asks before installing it from Flathub for your account. If you
already added the app to Gear Lever yourself, Monolith adopts that copy instead of
creating a second launcher.

For the Flatpak apps, a copy you already installed for your account is adopted.
A copy installed system-wide, for example through Bazaar, is left alone rather
than installed twice. To have Monolith manage the app instead, uninstall the
system-wide copy first. Both copies keep their data in the same place, so nothing
is lost.

## Gaming

### Epic Games Launcher

**Gaming → Epic Games Launcher** installs Epic's
[new launcher](https://launcher.epicgames.com/), which runs natively on Linux.
It's a preview from Epic, so expect rough edges; Epic ships fixes almost every day.
Your library, cloud saves and progress come from your Epic account, and
Windows-only games run through Proton, which the launcher downloads the first time
a game needs it.

Monolith downloads the AppImage from Epic's own download link. Epic doesn't publish
a checksum, so unlike the other apps, Monolith can't check the download against
one. It only accepts the file over HTTPS from Epic's build server and checks that
it's a real AppImage. If you already have the launcher in Gear Lever, Monolith
adopts it and replaces the AppImage with a fresh download from Epic.

After that, the launcher keeps itself up to date. `monolith update epic-games`,
**Update** in the dashboard and topgrade leave a working copy alone, and only
repair a missing launcher, icon or AppImage. `monolith install epic-games`
downloads Epic's current build again over whatever is installed.

Close the launcher, including its tray icon, before installing or removing it.
Removing it deletes only the AppImage and its launcher. Your sign-in, settings
and installed games stay where the launcher put them.

### WowUp-CF

**Gaming → WowUp-CF** installs the CurseForge edition of the WowUp World of
Warcraft addon manager, with World of Warcraft: Forever support. It comes from the
official AppImage on the
[WowUp-CF releases page](https://github.com/WowUp/WowUp.CF/releases).

Until a compatible stable release 2.24.0 or newer exists, Monolith follows only the
2.24.0 beta series, starting at beta.6. Once that stable release is out, Monolith
switches to stable releases for good and won't sign you up for later betas. It
sets WowUp-CF's own Application Release Channel to Stable and doesn't change your
addon release settings.

Use **Update** in the dashboard or `monolith update wowup-cf` to update the app.
Monolith turns off Gear Lever's update source for WowUp-CF, so only one updater is
in charge. Close WowUp-CF before updating or removing it. An update with nothing
new to install leaves it alone, so it can stay open then.

Removing it keeps your WowUp-CF settings, game installations and addons.

### r2modman

**Gaming → r2modman** installs [r2modman](https://github.com/ebkr/r2modmanPlus),
the mod manager for Thunderstore games such as Lethal Company, Risk of Rain 2 and
Valheim, from its official AppImage.

Monolith installs the newest stable release and skips prereleases. r2modman can
also update itself to newer stable releases, and Monolith recognizes those. If you
already have r2modman in Gear Lever and it's older than 3.2.2, which has no
published checksum, start it once so it updates itself, then let Monolith adopt it.

Close r2modman before updating or removing it. An update with nothing new to
install leaves it alone, so it can stay open then. Removing it keeps your
profiles, mods and settings in `~/.config/r2modmanPlus-local`.

### Prism Launcher

**Gaming → Prism Launcher** installs [Prism Launcher](https://prismlauncher.org),
the Minecraft launcher for separate instances, mod loaders and modpacks, as the
official [Flathub Flatpak](https://flathub.org/apps/org.prismlauncher.PrismLauncher).
The first install may also download the KDE runtime it uses.

A system-wide Prism Launcher already updates with the system's Flatpaks, so
Monolith leaves it alone. To have Monolith manage a per-user copy instead,
uninstall the system-wide one first; your instances stay.

You don't need to close Prism Launcher to update it, because an update takes
effect the next time it starts. Removing it keeps your instances, worlds, accounts
and settings in `~/.var/app/org.prismlauncher.PrismLauncher`.

Monolith doesn't move instances from a non-Flatpak Prism Launcher. To bring them
over, copy the folders in `~/.local/share/PrismLauncher/instances` to
`~/.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher/instances`.

## Media

### Sidra

**Media → Sidra** installs [Sidra](https://github.com/wimpysworld/sidra), an Apple
Music app with media keys, desktop notifications, themes and Last.fm scrobbling.
You need an Apple Music subscription. It comes from Sidra's official AppImage.

Monolith installs the newest stable release and skips prereleases. Sidra also
updates itself to newer stable releases and offers to restart, and Monolith
recognizes those updates. Close Sidra before updating or removing it. An update
with nothing new to install leaves it alone, so it can stay open then.

Removing it keeps your Apple Music sign-in and settings in `~/.config/Sidra`.

### Moonfin

**Media → Moonfin** installs [Moonfin](https://moonfin.io), a media client for
Jellyfin and Emby servers, from the Flatpak bundle in its
[releases](https://github.com/Moonfin-Client/Moonfin-Core/releases). It uses the
GNOME runtime.

If you already installed Moonfin for your account, Monolith adopts it and installs
the newest verified release over it, unless it already has that one. A
system-wide copy is left alone; uninstall it first with
`flatpak uninstall --system org.moonfin.linux` if you want Monolith to manage
Moonfin.

Moonfin tells you inside the app when a new release is out, but you don't need to
download it yourself; `monolith update` or `mjust update` takes care of it.
Moonfin can stay open while it updates, and the new version starts the next time
you open it. Removing it keeps your servers, sign-ins, settings and downloads in
`~/.var/app/org.moonfin.linux`.

### Plezy

**Media → Plezy** installs [Plezy](https://plezy.app), a media client for Plex,
Jellyfin and Emby servers, from the Flatpak bundle in its
[releases](https://github.com/edde746/plezy/releases). Plezy publishes no
AppImage. It uses the freedesktop runtime (`org.freedesktop.Platform` 25.08).

A system-wide copy is left alone; uninstall it first with
`flatpak uninstall --system com.edde746.plezy` if you want Monolith to manage
Plezy. Plezy can stay open while it updates. Removing it keeps your servers,
sign-ins, settings and downloads in `~/.var/app/com.edde746.plezy`.

## Crafting Apps

The **Crafting Apps** category has all thirteen
[Crafting Apps](https://getartcraft.com/apps), the ArtCraft team's open-source
desktop apps. Each one is a separate item, so install only the ones you want, for
example `monolith install photocraft pdfcraft`.

| App | For |
| --- | --- |
| PhotoCraft | photos and layered PSD documents |
| VectorCraft | vector illustration, SVG and PDF artwork |
| FilmCraft | video editing, colour and sound |
| LightCraft | a photo library and non-destructive raw development |
| PdfCraft | reading, organizing, combining, splitting and securing PDFs |
| EffectCraft | motion graphics and visual effects |
| DesignCraft | page layout for magazines, books and print documents |
| FigCraft | interface design and prototyping |
| WordCraft | word processing, including Word documents |
| SoundCraft | recording, editing and mixing audio and MIDI |
| DeckCraft | creating and presenting slide decks |
| CADCraft | 2D drafting, including DXF and DWG drawings |
| GridCraft | spreadsheets: calculating, analyzing and charting data |

The apps are young. Their developers call them pre-alpha, alpha or in development,
and new releases come out often, so expect missing features and rough edges, and
keep copies of important files.

Two things to know:

- Save SoundCraft sessions in `~/Music`. SoundCraft suggests a folder directly in
  your home folder, which its Flatpak can't write to, so a session saved there is
  lost when SoundCraft closes.
- FigCraft's Flatpak has no network access, so sharing a file or joining a live
  session fails until you allow it with
  `flatpak override --user --share=network ai.storyteller.figcraft`.

Each app comes from the Flatpak bundle in its own GitHub project, such as
[PhotoCraft's releases](https://github.com/storytold/photocraft/releases), and uses
the freedesktop runtime. A system-wide copy is left alone; to have Monolith manage
the app, uninstall it first, for example with
`flatpak uninstall --system ai.storyteller.photocraft`.

Every update downloads the app's whole bundle again, up to about 100 MB. When the
newest release is already installed, nothing is downloaded. The apps can stay open
while they update. Removing an app keeps its settings and other data in
`~/.var/app/ai.storyteller.<app>`, for example
`~/.var/app/ai.storyteller.photocraft`. Your own files stay where you saved them,
including LightCraft's photo library in `~/Pictures/LightCraft Library`.

You don't need the ArtCraft Launcher that the Crafting Apps site recommends. If
you use it anyway, set **Install new apps as** to Flatpak in its settings. Monolith
then finds the apps it installs and can adopt them. Apps it installs as AppImages
are separate copies that Monolith doesn't manage.

## Communication

### Fluxer

**Communication → Fluxer** installs [Fluxer](https://fluxer.app), an open-source
chat app with voice calls, as the official
[Flathub Flatpak](https://flathub.org/apps/app.fluxer.Fluxer).

**Communication → Fluxer Canary** installs Fluxer's early-access channel,
`app.fluxer.FluxerCanary`, which gets new features first. Flathub only carries the
stable channel, so Canary comes from
[Fluxer's own signed Flatpak repository](https://pkgs.fluxer.com/flatpak/fluxer-canary.flatpakref),
which Monolith adds to your account's Flatpak sources as `fluxer`.

You can install both side by side; they keep separate data. Removing them keeps
your sign-ins and settings in `~/.var/app/app.fluxer.Fluxer` or
`~/.var/app/app.fluxer.FluxerCanary`. The `fluxer` repository stays in your Flatpak
sources until you run `flatpak remote-delete --user fluxer`.

## Appearance

### Ghostty themes

Monolith already includes 554 Ghostty themes (see
[the terminal](desktop.md#themes)). **Appearance → Ghostty Themes**, or
`monolith install ghostty-themes`, adds the original collection that came with
Ghostty 1.3.1: Ghostty's official 463-theme bundle, including names such as
`Gruvbox Dark`, downloaded for your account.

Your own theme files and your selected theme are kept.
`monolith update ghostty-themes` repairs the collection,
`monolith install ghostty-themes` downloads it again, and
`monolith remove ghostty-themes` removes its files that you haven't changed while
keeping your edits.

## AI coding and developer tools

| App | What it is |
| --- | --- |
| Claude Code (`claude`) | Anthropic's coding agent |
| Codex CLI (`codex`) | OpenAI's coding agent |
| OpenCode (`opencode`) | a coding agent that works with any provider |
| Oh My Pi (`omp`) | a coding agent with built-in development tools |
| Herdr (`herdr`) | a terminal multiplexer for coding agents |
| Tea (`tea`) | the command-line client for Gitea and Forgejo |
| Superfile (`superfile`) | a terminal file manager, started with `spf` |

Oh My Pi comes as the standalone Linux binary from its
[official releases](https://github.com/can1357/oh-my-pi/releases), checked
against the release checksum, so you don't need Bun or npm. Removing it keeps your
settings, sign-ins, sessions and project files in `~/.omp/`.
