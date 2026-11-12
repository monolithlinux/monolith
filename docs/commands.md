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
- [Trying a test build](#trying-a-test-build)
- [Checking this computer](#checking-this-computer)
- [The CPU scheduler](#the-cpu-scheduler)
- [The boot menu](#the-boot-menu)
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

`monolith update`, or just `update` or `upgrade`, updates everything else too:
apps from the catalog, Proton tools, AppImages, Distrobox containers, and
developer tools such as rustup, npm and Claude Code. It also checks for firmware
updates. Each step shows its progress, and one that fails doesn't stop the rest.
The step that's running lists what it's updating, such as each Flatpak or app,
with a bar for each download, and folds back to one line when it's done.
At the end you get a short summary of what changed. An app from the catalog that
couldn't update has its reason under the step.

You don't need a password to update. If there's a new build of Monolith, it asks
whether to restart now. The answer is no unless you type `y`, and the new build
waits for your next restart either way.

`update --list` shows the steps it would run on your computer. `--only` and
`--skip` take step names, such as `update --skip distrobox`. To skip a step
every time, or to add your own commands, put them in
`~/.config/monolith/update.toml`; `update --help` shows an example. The full
output of the last ten runs is kept in `~/.local/state/monolith/update/`.

`monolith update` followed by app names, such as `monolith update sidra`, updates
only those apps from the catalog.

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

If you change your mind before you've restarted, the same command cancels the
rollback, and you stay on the build you're running.

## Trying a test build

Changes to Monolith start as pull requests on GitHub. Each open pull request that
changes your edition gets its own test build, so you can try a change before it
reaches the regular builds:

```bash
monolith pr
```

This lists the open pull requests. The ones you can try come first, with when
their test build was made and whether it's behind or ahead of latest, the newest
regular build. Behind means it was built before latest, so it may lack that
build's newest packages. That's fine for trying something out. The others follow
with the reason, such as a build that's still running or one that failed. Type a
number to switch to that pull request, or press Enter to leave everything as it
is. `monolith pr list` only shows the list, and `monolith pr 63` switches to
pull request #63 directly.

Test builds can be broken. While you're on one, this computer gets that pull
request's builds instead of the daily ones, and a new one comes whenever the
pull request changes. Your apps, files and settings stay, and the switch takes
effect when you restart. Once the pull request is merged or closed, its test
build gets no more updates, and `monolith pr` tells you so. To return to the
newest build and the daily updates:

```bash
monolith pr latest
```

That's the same as `monolith rollback latest`, and before you've restarted it
cancels the switch instead.

A test build is the image `ghcr.io/monolithlinux/kde:pr-63-45`: your edition,
the pull request's number and its Fedora version. Switching is an
`rpm-ostree rebase` to it, and its signature is checked like any other build's.
The list comes from GitHub, which answers a limited number of lookups an hour
from one internet address. If you run out, `monolith pr` says when to try again.

## Checking this computer

`monolith status` shows your build, whether an update or a switch to another
build is waiting for a restart, Secure Boot, your graphics driver, when you last
updated everything, and the CPU scheduler. If anything needs your attention, it
says so and tells you how to fix it.

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

## The boot menu

The boot menu is the list of builds you see for a few seconds when the computer
starts. Monolith gives it a look of its own: the logo, the entries in the middle
of the screen, and five seconds to pick one.

```bash
monolith boot-menu       # is Monolith's look on?
monolith boot-menu off   # GRUB's plain list, shown for one second
monolith boot-menu on    # Monolith's look again
```

A change shows the next time the computer starts.
[What's built in](desktop.md#boot-menu) says how it works.

## Switching editions

Monolith comes in two editions. KDE is for AMD or Intel graphics, or an older
NVIDIA card on Nouveau/NVK. KDE NVIDIA has NVIDIA's open driver, for cards from
the GTX 16 or RTX 20 series onward.

`monolith edition` shows which one you're on, and `monolith edition kde-nvidia` or
`monolith edition kde` switches. Your apps, files and settings stay, and the
switch takes effect when you restart. To call it off before then, run
`monolith edition` with the edition you're still on.

## Setup

`monolith adopt` gives an account that existed before you switched to Monolith
the default settings it's missing, and rebuilds its font cache.

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
