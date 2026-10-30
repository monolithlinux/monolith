# Monolith's commands

`monolith` on its own opens Monolith Software, the app catalog. Everything else
Monolith can do for you is a `monolith` command too. Run `monolith help` to see
them all, or pick **System tools** in the catalog to run one from a menu.

`monolith help` followed by a command, such as `monolith help rollback`, explains
that command. Tab completion works in fish, bash and zsh. The old names `mjust`
and `ujust` still work.

- [Apps](#apps)
- [Updates](#updates)
- [Going back to an earlier build](#going-back-to-an-earlier-build)
- [Checking this computer](#checking-this-computer)
- [The CPU scheduler](#the-cpu-scheduler)
- [Switching editions](#switching-editions)
- [Setup](#setup)
- [When something goes wrong](#when-something-goes-wrong)

## Apps

```bash
monolith list
monolith install sidra
monolith update sidra
monolith remove sidra
```

The [Monolith Software guide](software.md) covers these.

## Updates

The system and your Flatpaks update by themselves every day. A system update
takes effect the next time you restart.

`monolith update` updates everything else too: apps from the catalog, Distrobox
containers, and developer tools such as rustup. It runs topgrade, so running
`topgrade` does the same.

`monolith changelogs` lists the packages that the last system update changed.

`monolith toggle-updates disable` stops the daily system and Flatpak updates, and
`monolith toggle-updates enable` starts them again. On its own, it tells you
whether they're on.

`monolith update-firmware` installs firmware updates for your computer and
devices through fwupd.

## Going back to an earlier build

If an update breaks something, go back to the build you had before it:

```bash
monolith rollback
```

You can also pick a day. `monolith rollback list` shows the builds you can go back
to, and `monolith rollback 20261027` goes back to the one from that day. Your apps,
files and settings stay, and the change takes effect when you restart.

While you're on an older build, system updates are paused, so the build you left
doesn't come back with the next automatic update. Flatpaks still update. Once
the problem is fixed, return to the newest build:

```bash
monolith rollback latest
```

## Checking this computer

`monolith status` shows your build, whether an update is waiting for a restart,
Secure Boot, your graphics driver, when you last updated everything, and the CPU
scheduler. If anything needs your attention, it says so and tells you how to fix
it.

`monolith clean-system` frees up space by removing unused container images,
unused Flatpak runtimes and old builds. It asks before removing anything.

`monolith check-idle-power-draw` measures how much power the computer draws
while it sits idle.

`monolith bios` restarts into your computer's UEFI firmware setup, and
`monolith bios-info` shows your board and firmware version.

## The CPU scheduler

Monolith's kernel can run sched-ext schedulers, which take over from the kernel's
own CPU scheduler while they run. Monolith doesn't turn one on by itself. To try
one:

```bash
monolith scheduler gaming      # tuned for low latency
monolith scheduler powersave   # tuned for battery life
monolith scheduler auto        # adjusts itself to the load
monolith scheduler off         # back to the kernel's own scheduler
```

These modes use scx_lavd, which started out as a scheduler for gaming on the
Steam Deck and has a setting for each mode. Your choice takes effect right away
and stays after you restart. `monolith scheduler` on its own shows what's running
now.

To use another scheduler that scx_loader supports, name it after the mode, such
as `monolith scheduler gaming bpfland`. Your choice goes into
`/etc/scx_loader/config.toml`, and anything else you've set there stays.

## Switching editions

Monolith comes in two editions. KDE is for AMD or Intel graphics, or an older
NVIDIA card on Nouveau/NVK. KDE NVIDIA has NVIDIA's open driver, for cards from
the GTX 16 or RTX 20 series onward.

`monolith edition` shows which one you're on, and `monolith edition kde-nvidia` or
`monolith edition kde` switches. Your apps, files and settings stay, and the
switch takes effect when you restart.

## Setup

`monolith adopt` gives an account that existed before you switched to Monolith
the default settings it's missing, and rebuilds its font cache. If you have your
own topgrade settings, they're saved as `topgrade.toml.pre-monolith` first.

`monolith enroll-secure-boot-key` lets Secure Boot trust Monolith's kernel and
NVIDIA driver. Restart afterwards, choose **Enroll MOK → Continue** in the
MokManager screen, and type `monolith` as the password.

`monolith setup-luks-tpm-unlock` lets your computer unlock its encrypted disk with
the TPM at boot, so you don't type the disk password every time.
`monolith remove-luks-tpm-unlock` turns that off again.

## When something goes wrong

`monolith report-bug` collects your build, kernel, graphics and Secure Boot
details and opens a new bug report on GitHub with them filled in. Nothing is
sent until you describe the problem and submit it yourself.

`monolith logs-this-boot` and `monolith logs-last-boot` show the system log from
this boot or the one before. The second one helps after a crash or a freeze.

`monolith check-local-overrides` shows where your `/etc` differs from Monolith's
defaults.
