#!/usr/bin/env python3
"""Build release notes from the previous and current RPM package lists."""

import argparse
import os
import re
import subprocess

# (Display name, RPM name) rows for each edition's major-package table.
MAJOR_PACKAGES = [
    ("Kernel", "kernel"),
    ("Mesa", "mesa-dri-drivers"),
    ("NVIDIA driver", "nvidia-driver"),
    ("Plasma", "plasma-workspace"),
    ("systemd", "systemd"),
    ("Brave", "brave-origin-nightly"),
    ("Steam", "steam"),
    ("Gamescope", "gamescope"),
    ("GameMode", "gamemode"),
    ("MangoHud", "mangohud"),
    ("Mesa Vulkan", "mesa-vulkan-drivers"),
    ("Tailscale", "tailscale"),
    ("Fish", "fish"),
]

EPOCH_PATTERN = re.compile(r"^(?:\d+|\(none\)):")
FEDORA_PATTERN = re.compile(r"\.fc\d+")

# GitHub refuses release notes longer than 125,000 characters. A move to a new
# Fedora release changes nearly every package: the notes for Fedora 44 to 45
# came to about 208,000. Notes longer than BODY_LIMIT, a little under GitHub's
# limit, leave rows out (see main).
BODY_LIMIT = 120_000


def clean_version(version: str) -> str:
    """Drop the leading epoch and the trailing `.fcNN` dist tag for readability."""
    version = EPOCH_PATTERN.sub("", version)
    version = FEDORA_PATTERN.sub("", version)
    return version


def parse_packages(path: str) -> dict[str, str]:
    """Read a `name<TAB>EVR` list, collapsing identical multilib entries."""
    packages: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or "\t" not in line:
                continue
            name, version = line.split("\t", 1)
            packages[name] = clean_version(version)
    return packages


def major_table(prev: dict[str, str], curr: dict[str, str]) -> str:
    rows = ""
    for display, pkg in MAJOR_PACKAGES:
        if pkg not in curr:
            continue
        new = curr[pkg]
        old = prev.get(pkg)
        if old and old != new:
            value = f"{old} ➡️ {new}"
        else:
            value = new
        rows += f"\n| **{display}** | {value} |"
    if not rows:
        return ""
    return "### Major packages\n| Name | Version |\n| --- | --- |" + rows + "\n\n"


def changes_table(prev: dict[str, str], curr: dict[str, str], detail: str = "full") -> str:
    """The package table. detail "short" leaves out the changed packages and
    "counts" every row, for notes that would be too long otherwise."""
    # Avoid a multi-thousand-row diff for a brand-new edition.
    if not prev:
        return f"_New edition in this build, shipping {len(curr)} packages._\n\n"

    added = sorted(set(curr) - set(prev))
    removed = sorted(set(prev) - set(curr))
    changed = sorted(p for p in set(prev) & set(curr) if prev[p] != curr[p])

    if not (added or removed or changed):
        return "_No package changes._\n\n"

    out = "### Package changes\n"
    if detail != "full":
        out += (f"_{len(changed):,} packages changed, {len(added):,} were added and "
                f"{len(removed):,} removed. That's too many to list here")
        if detail == "counts" or not (added or removed):
            return out + "._\n\n"
        out += ", so the table shows only the added and removed ones._\n\n"
        changed = []
    out += "| | Name | Previous | New |\n| --- | --- | --- | --- |"
    for p in changed:
        out += f"\n| 🔄 | {p} | {prev[p]} | {curr[p]} |"
    for p in added:
        out += f"\n| ✨ | {p} | | {curr[p]} |"
    for p in removed:
        out += f"\n| ❌ | {p} | {prev[p]} | |"
    return out + "\n\n"


def commits_section(workdir: str, prev_rev: str, curr_rev: str, repo: str) -> str:
    try:
        if not prev_rev:
            return ""
        # Both revisions must resolve in this checkout.
        for rev in (prev_rev, curr_rev):
            subprocess.run(
                ["git", "-C", workdir, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        log = subprocess.run(
            ["git", "-C", workdir, "log", "--no-merges",
             "--pretty=format:%H\x1f%h\x1f%an\x1f%s", f"{prev_rev}..{curr_rev}"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("utf-8")
    except subprocess.CalledProcessError:
        return ""

    rows = ""
    for line in log.split("\n"):
        if not line:
            continue
        full, short, author, subject = line.split("\x1f")
        rows += f"\n| [`{short}`](https://github.com/{repo}/commit/{full}) | {subject} | {author} |"
    if not rows:
        return ""
    return "### Commits\n| Hash | Subject | Author |\n| --- | --- | --- |" + rows + "\n\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages-dir", required=True, help="dir holding <edition>.prev.txt / <edition>.curr.txt")
    parser.add_argument("--images", required=True, help="comma-separated full image refs, one per edition")
    parser.add_argument("--prev-tag", required=True, help="previous image tag, e.g. 20260614")
    parser.add_argument("--curr-tag", required=True, help="current image tag, e.g. 20260615")
    parser.add_argument("--prev-release-tag", default="", help="previous release/git tag, e.g. 2026-06-14 (defaults to --prev-tag)")
    parser.add_argument("--curr-release-tag", default="", help="current release/git tag, e.g. 2026-06-15 (defaults to --curr-tag)")
    parser.add_argument("--repo", required=True, help="owner/name for commit links")
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--prev-rev", default="", help="git ref of the previous release (for commit range)")
    parser.add_argument("--curr-rev", default="HEAD")
    parser.add_argument("--handwritten", default="")
    parser.add_argument("--output", required=True, help="env file for title/tag")
    parser.add_argument("--changelog", required=True, help="markdown output path")
    args = parser.parse_args()

    images = [i.strip() for i in args.images.split(",") if i.strip()]
    editions = [img.rsplit("/", 1)[-1] for img in images]
    ref_by_name = dict(zip(editions, images))

    # Missing current lists are skipped; missing previous lists are new editions.
    prev: dict[str, dict[str, str]] = {}
    curr: dict[str, dict[str, str]] = {}
    for name in editions:
        curr_path = os.path.join(args.packages_dir, f"{name}.curr.txt")
        if not os.path.exists(curr_path):
            continue
        curr[name] = parse_packages(curr_path)
        prev_path = os.path.join(args.packages_dir, f"{name}.prev.txt")
        prev[name] = parse_packages(prev_path) if os.path.exists(prev_path) else {}

    present = [n for n in editions if n in curr]

    prev_release = args.prev_release_tag or args.prev_tag
    curr_release = args.curr_release_tag or args.curr_tag

    title = curr_release
    commits = commits_section(args.workdir, args.prev_rev, args.curr_rev, args.repo)

    def render(detail: str) -> str:
        body = ""
        if args.handwritten:
            body += args.handwritten + "\n\n"
        body += (
            f"Changes from the previous build [`{prev_release}`]"
            f"(https://github.com/{args.repo}/releases/tag/{prev_release}) "
            f"to [`{curr_release}`](https://github.com/{args.repo}/releases/tag/{curr_release}).\n\n"
        )
        if present:
            names = ", ".join(f"`{e}`" for e in present)
            body += f"Editions in this build: {names}.\n\n"

        # One package section per published edition.
        for name in present:
            body += f"## `{name}`\n\n"
            body += major_table(prev.get(name, {}), curr[name])
            body += changes_table(prev.get(name, {}), curr[name], detail)

        body += commits

        body += "### How to rebase\nPick your edition. Rebase to this exact build, or track the latest:\n\n"
        for name in present:
            img = ref_by_name[name]
            body += (
                f"**`{name}`**\n\n"
                "```bash\n"
                "# this exact build\n"
                f"rpm-ostree rebase ostree-image-signed:docker://{img}:{args.curr_tag}\n"
                "# or track the latest build\n"
                f"rpm-ostree rebase ostree-image-signed:docker://{img}:latest\n"
                "```\n\n"
            )
        return body

    # Leave out the changed packages first, then every row, until the notes fit.
    for detail in ("full", "short", "counts"):
        body = render(detail)
        if len(body) <= BODY_LIMIT:
            break

    with open(args.changelog, "w") as f:
        f.write(body)
    with open(args.output, "w") as f:
        f.write(f"TITLE={title}\nTAG={curr_release}\n")

    print(f"Title: {title}")
    print(body)


if __name__ == "__main__":
    main()
