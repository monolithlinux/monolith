#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

for suffix in "" -nvidia; do
  recipe="recipes/recipe-gnome${suffix}.yml"
  grep -Fq -- '- from-file: vicinae.yml' "$recipe" || {
    echo "$recipe must include vicinae.yml" >&2
    exit 1
  }
done

for desktop in kde cosmic; do
  for suffix in "" -nvidia; do
    recipe="recipes/recipe-${desktop}${suffix}.yml"
    if grep -Fiq vicinae "$recipe"; then
      echo "$recipe must not include Vicinae" >&2
      exit 1
    fi
  done
done

if grep -Fiq vicinae recipes/common.yml recipes/kde.yml recipes/cosmic.yml; then
  echo "Vicinae must not be installed by common, KDE, or COSMIC modules" >&2
  exit 1
fi

grep -Fq 'dnf5 --enable-repo=terra -y install vicinae' recipes/vicinae.yml
grep -Fq 'systemctl --global enable vicinae.service' recipes/vicinae.yml
