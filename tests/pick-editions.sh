#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
picker=.github/workflows/pick-editions.sh
editions=(kde kde-nvidia cosmic cosmic-nvidia)
all=$'kde\nkde-nvidia\ncosmic\ncosmic-nvidia'
kde=$'kde\nkde-nvidia'
cosmic=$'cosmic\ncosmic-nvidia'

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
[[ "${#recipes[@]}" == "${#editions[@]}" ]]
for edition in "${editions[@]}"; do
    [[ -f "recipes/recipe-${edition}.yml" ]]
    check "recipes/recipe-${edition}.yml" "$edition"
    # The recipe's name is the image's, which releases and computers go by.
    [[ "$(awk '/^name:/{print $2; exit}' "recipes/recipe-${edition}.yml")" == "$edition" ]] ||
        { printf 'recipe-%s.yml builds an image of another name\n' "$edition" >&2; exit 1; }
done
# A desktop's files rebuild its two editions, and the NVIDIA layer rebuilds
# every NVIDIA edition.
check recipes/kde.yml "$kde"
check files/kde/usr/bin/monolith-ghostty "$kde"
check recipes/cosmic.yml "$cosmic"
check files/cosmic/usr/share/cosmic/com.system76.CosmicTerm/v1/font_name "$cosmic"
check recipes/nvidia.yml $'kde-nvidia\ncosmic-nvidia'
check recipes/common.yml "$all"
check files/system/usr/bin/monolith "$all"
check unknown-file "$all"
check README.md ''
check iso/build.sh ''
check .github/workflows/generate-iso.yml ''
check .github/workflows/generate_release.yml ''
check .github/workflows/check-app-downloads.yml ''
check .github/workflows/check-app-downloads.py ''
check .github/workflows/check-app-downloads.sh ''
check tests/pick-editions.sh ''
check news/announcements.toml ''
check .github/ISSUE_TEMPLATE/bug_report.yml ''
check files/system/usr/libexec/monolith/software "$all"
# Multiple changed paths are deduplicated and keep the canonical order.
check $'recipes/nvidia.yml\nrecipes/kde.yml\nrecipes/recipe-kde.yml' $'kde\nkde-nvidia\ncosmic-nvidia'
check $'recipes/cosmic.yml\nrecipes/recipe-kde.yml' $'kde\ncosmic\ncosmic-nvidia'

printf 'Edition selection tests passed\n'
