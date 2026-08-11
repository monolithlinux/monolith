#!/usr/bin/env bash
# Map changed files to image editions for build.yml and pr-rebase-hint.yml.
# Add new editions to ALL, the case map, and build.yml's matrix entries.
# Modes:
#   pick-editions.sh all            every edition
#   pick-editions.sh <diff-range>   editions affected by `git diff <diff-range>`
#   pick-editions.sh -              same, but the file list comes from stdin
# Output: one edition key per line; no output means no image changes.
set -euo pipefail

ALL=(gnome gnome-nvidia kde kde-nvidia cosmic cosmic-nvidia)

arg="${1:?usage: pick-editions.sh all|-|<git diff range>}"

if [ "${arg}" = all ]; then
  printf '%s\n' "${ALL[@]}"
  exit 0
fi

changed_files() {
  if [ "${arg}" = - ]; then cat; else git diff --name-only "${arg}"; fi
}

declare -A want=()
mark() { local e; for e in "$@"; do want[$e]=1; done; }

while IFS= read -r f; do
  case "$f" in
    recipes/recipe-gnome.yml) mark gnome ;;
    recipes/recipe-gnome-nvidia.yml) mark gnome-nvidia ;;
    recipes/recipe-kde.yml) mark kde ;;
    recipes/recipe-kde-nvidia.yml) mark kde-nvidia ;;
    recipes/recipe-cosmic.yml) mark cosmic ;;
    recipes/recipe-cosmic-nvidia.yml) mark cosmic-nvidia ;;
    recipes/gnome.yml|files/gnome/*) mark gnome gnome-nvidia ;;
    files/gschema-overrides/*) mark gnome gnome-nvidia ;;
    recipes/kde.yml|files/kde/*) mark kde kde-nvidia ;;
    recipes/cosmic.yml|files/cosmic/*) mark cosmic cosmic-nvidia ;;
    recipes/base-main.yml) mark cosmic cosmic-nvidia ;;
    recipes/nvidia.yml) mark gnome-nvidia kde-nvidia cosmic-nvidia ;;
    # These files do not change an image.
    *.md|LICENSE|.gitignore|justfile|iso/*) ;;
    .github/workflows/generate-iso.yml) ;;
    .github/workflows/generate_release.yml|.github/workflows/changelog.py) ;;
    .github/workflows/prune-buildkit.yml|.github/workflows/pr-rebase-hint.yml) ;;
    # Unknown paths fail safe by rebuilding every edition.
    *) mark "${ALL[@]}" ;;
  esac
done < <(changed_files)

for e in "${ALL[@]}"; do
  if [ -n "${want[$e]:-}" ]; then echo "$e"; fi
done
