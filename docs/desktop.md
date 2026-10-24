# What's built in

These are part of the Monolith image itself, so there's nothing to install and
they update along with Monolith.

- [The terminal](#the-terminal)
- [OpenDeck](#opendeck)
- [Node.js](#nodejs)
- [Waywallen wallpapers](#waywallen-wallpapers)
- [Update reminder](#update-reminder)
- [Monolith News](#monolith-news)

## The terminal

Monolith uses Ghostty with Fish, the Pure prompt and JetBrainsMono Nerd Font at 12
points. The first time you open Ghostty, Monolith creates its defaults in
`~/.config/ghostty/`, unless you already have a Ghostty configuration, which is
kept as it is. Your existing shell settings and the tools you installed keep
working.

### No Konsole

Konsole isn't included. If you want it, install
[Konsole from Flathub](https://flathub.org/apps/org.kde.konsole) through Bazaar. If
Konsole was your default terminal, either install it that way or pick Ghostty under
**System Settings → Default Applications**. The terminal panels in Dolphin (F4)
and Kate keep working.

DrKonqi, KDE's crash report window, is left out too, because Fedora's package for
it needs Konsole. `coredumpctl list` still shows programs that crashed.

### Themes

Ghostty comes with 554 themes: the full Tinted Terminal catalog, including 19
Gruvbox variants, and the four official Catppuccin themes. Terra's Ghostty package
leaves out the upstream theme collection, so Monolith adds these catalogs, along
with their MIT licenses.

Browse and preview them with `ghostty +list-themes`. To use one, add a line like
this to your Ghostty configuration, then reload with Ctrl+Shift+Comma:

```
theme = base16-gruvbox-dark-medium
```

Want Ghostty's own original collection, with names like `Gruvbox Dark`? Install
[Ghostty Themes](software.md#ghostty-themes) from `monolith`.

### Window size

Ghostty remembers its last normal window size across logins and reboots. A KWin
script saves the size, and a window rule restores it when Ghostty opens. Your own
window rules take priority.

To stop saving the size, turn off **Remember Ghostty Window Size** in Plasma's KWin
Scripts settings. To change or stop restoring it, edit or remove the
**Ghostty: remember window size** rule in Window Rules. Monolith won't recreate the
rule once you've removed it.

## OpenDeck

[OpenDeck](https://github.com/nekename/OpenDeck) is an app for the Elgato Stream
Deck that runs most plugins made for Elgato's own Stream Deck software. Monolith
builds OpenDeck's official release into the image, pinned to a version and checked
against its checksum.

Plug in your Stream Deck and start OpenDeck; there's no setup. Monolith includes
the device rules OpenDeck needs, so every Stream Deck model it supports works,
including the newer MK.2 with scissor keys, the Plus XL and the modules.
**Start at login** in OpenDeck's settings works too.

Plugins built on Node.js use the [Node.js](#nodejs) that Monolith includes, and
plugins built only for Windows use Wine, which is included as well. OpenDeck keeps
its profiles, plugins and settings in `~/.config/opendeck` and its logs in
`~/.local/share/opendeck/logs`.

New OpenDeck releases arrive with Monolith updates. OpenDeck may tell you about a
release before it reaches you; turn off **Check for updates** in its settings to
stop that notice.

**Used the OpenDeck Flatpak from Monolith Software before?** Monolith moves you
over at your next login. It copies the Flatpak's profiles, plugins and settings to
the built-in OpenDeck, keeps **Start at login** on if you had it on, and uninstalls
the Flatpak. The Flatpak's own copy of your data stays in
`~/.var/app/me.amankhanna.opendeck` until you delete it. An OpenDeck Flatpak you
installed some other way, for example through Bazaar, is left alone; uninstall it
if you only want one OpenDeck.

## Node.js

Node.js 24 and npm are included. `npm install -g` installs packages into your own
account, in `~/.local` with their commands in `~/.local/bin`, so you don't need an
administrator password, and topgrade keeps them updated. To put them somewhere
else, set `prefix` in `~/.npmrc`. A Node.js from nvm, fnm or mise still comes
first in your shell.

## Waywallen wallpapers

Monolith includes the
[Waywallen KDE companion plugin](https://github.com/waywallen/waywallen-display),
built in from a pinned upstream package that's checked against its checksum. The
Waywallen app itself is optional: install `org.waywallen.waywallen` from Flathub
through Bazaar.

To use it, open Waywallen, right-click the desktop, choose
**Configure Desktop and Wallpaper…**, pick **Waywallen** as the wallpaper type and
apply it. Do this for each screen you want Waywallen to control. Monolith doesn't
change your current wallpaper or autostart settings.

If you installed the `org.waywallen.kde` plugin yourself, your copy takes priority
over Monolith's. To use the built-in one, remove just your copy with
`kpackagetool6 --type Plasma/Wallpaper --remove org.waywallen.kde` (without `sudo`
or `--global`), then log out and back in.

## Update reminder

The system and your Flatpaks update by themselves every day. `topgrade`, which
`mjust update` also runs, updates the rest: apps from `monolith`, Distrobox
containers, and developer tools such as rustup and Claude Code.

If topgrade hasn't finished a run for a week, a notification at login reminds you,
at most once a week. **Update now** opens Ghostty and runs `topgrade`, and keeps the
window open afterwards so you can read the summary. A new account gets its first
reminder a week after its first login at the earliest.

Every finished run counts, even one where some steps failed. topgrade records it
through the post command in `~/.config/topgrade.d/monolith.toml`, so if you opted
out of that file, turn the reminder off too. To turn it off, disable
**Monolith Update Reminder** in Plasma's Autostart settings. The reminder keeps its
timestamps in `~/.local/state/monolith/update-reminder/`.

## Monolith News

Monolith News shows project announcements, such as things you need to do and
notable changes. It lists all announcements beside the one you're reading and marks
the ones you haven't opened yet.

At login it downloads [`news/announcements.toml`](../news/announcements.toml) from
the `main` branch and opens only if there's an announcement you haven't dismissed.
Closing the window dismisses what it showed. To read older announcements, open
**Monolith News** from the application menu.

To stop the login check, turn off **Show at login** in the window, or disable
Monolith News in Plasma's Autostart settings. What you've read and dismissed is
saved in `~/.local/state/monolith/news/`.

### Publishing an announcement

Add an entry with a new `id` at the top of `news/announcements.toml`. The comment
at the top of that file explains every field, including the optional category,
**Get it** steps and note. Changes reach users as soon as they're merged, without
an image rebuild. Editing an existing entry doesn't show it again to people who
dismissed it; publish a new `id` for that.

Preview your change before opening a pull request:

```bash
files/kde/usr/bin/monolith-news --preview news/announcements.toml
```
