#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
picker=.github/workflows/pick-editions.sh
all=$'kde\nkde-nvidia'

check() {
    local path="$1" expected="$2" actual
    actual="$(printf '%s\n' "$path" | bash "$picker" -)"
    if [[ "$actual" != "$expected" ]]; then
        printf 'Unexpected editions for %s: %s\n' "$path" "$actual" >&2
        exit 1
    fi
}

[[ "$(bash "$picker" all)" == "$all" ]]
# Every selectable edition must have a recipe, and no other recipes may remain.
recipes=(recipes/recipe-*.yml)
[[ "${#recipes[@]}" == 2 ]]
for edition in kde kde-nvidia; do
    [[ -f "recipes/recipe-${edition}.yml" ]]
    check "recipes/recipe-${edition}.yml" "$edition"
done
check recipes/kde.yml "$all"
check files/kde/usr/bin/monolith-ghostty "$all"
check recipes/nvidia.yml kde-nvidia
check recipes/common.yml "$all"
check files/system/usr/bin/monolith "$all"
check unknown-file "$all"
check README.md ''
check iso/build.sh ''
check .github/workflows/generate-iso.yml ''
check .github/workflows/generate_release.yml ''
check tests/pick-editions.sh ''
# Multiple changed paths are deduplicated and keep the canonical order.
check $'recipes/nvidia.yml\nrecipes/kde.yml\nrecipes/recipe-kde.yml' "$all"

printf 'Edition selection tests passed\n'
