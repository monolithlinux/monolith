#!/usr/bin/env bash
# The monolith command: the catalog comes first, app commands go to the
# catalog, system commands run through just, and help, errors and tab
# completion describe the same commands. Needs just; checks fish and zsh
# completion when those shells are installed.
# Code for other shells and for stand-in programs is single-quoted on purpose.
# shellcheck disable=SC2016
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
command -v just >/dev/null || fail 'just is not installed'

# A copy of /usr with the real command, recipes and helpers, but stand-ins for
# the catalog and for the scripts behind system commands. Each logs its
# arguments.
usr="$test_root/usr"
log="$test_root/log"
mkdir -p "$usr/bin" "$usr/libexec/monolith" "$usr/share" "$log"
cp "$repo_root/files/system/usr/bin/"{monolith,mjust,ujust} "$usr/bin/"
cp -r "$repo_root/files/system/usr/share/monolith" "$usr/share/"
cp "$repo_root/files/system/usr/libexec/monolith/common.sh" "$usr/libexec/monolith/"
stand_in() {
    cat >"$1" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"$log/$2"
${3-}
EOF
    chmod +x "$1"
}
stand_in "$usr/libexec/monolith/software" catalog \
    '[ "${1-}" != __ids ] || printf "sidra\tSidra\nepic-games\tEpic Games Launcher\n"'
for script in status scheduler rollback edition report-bug; do
    stand_in "$usr/libexec/monolith/$script" "$script" \
        '[ "${1-}" != --help ] || echo "Usage: monolith '"$script"' [details]"'
done
bin="$test_root/bin"
mkdir -p "$bin"
stand_in "$bin/topgrade" topgrade
# Like the real topgrade binary, this stand-in must show up as "topgrade" in
# /proc/PID/comm, which a #!/usr/bin/env script would not. CALL_MONOLITH runs
# the old drop-in's `monolith update` once; a second topgrade would only log.
cat >"$bin/topgrade" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >>"$log/topgrade"
if [ -n "\${CALL_MONOLITH-}" ]; then CALL_MONOLITH= monolith update; fi
EOF
printf '#!/usr/bin/env bash\nexit 1\n' >"$bin/rpm-ostree"
chmod +x "$bin/rpm-ostree"
printf 'NAME=Monolith\nPRETTY_NAME="Monolith KDE"\n' >"$test_root/os-release"

export PATH="$usr/bin:$bin:$PATH" MONOLITH_OS_RELEASE="$test_root/os-release" NO_COLOR=1
unset MONOLITH_LIBEXEC
monolith() { "$usr/bin/monolith" "$@"; }
fresh() { rm -rf "$log"; mkdir -p "$log"; }
logged() { [ "$(cat "$log/$1" 2>/dev/null)" = "$2" ] || fail "$1 got '$(cat "$log/$1" 2>/dev/null)', expected '$2'"; }
not_called() { [ ! -e "$log/$1" ] || fail "$1 ran: $(cat "$log/$1")"; }

# On a terminal, monolith on its own opens the catalog.
fresh
script -qec "$usr/bin/monolith" /dev/null >/dev/null
logged catalog ''
# Without a terminal there's nothing to click, so it explains itself instead.
fresh
help="$(monolith </dev/null)"
not_called catalog
grep -q '^Run monolith on its own to open the app catalog' <<<"$help" || fail 'no help without a terminal'

# The help lists the apps first, then every system command in its group.
help="$(monolith help)"
[ "$(sed -n 1p <<<"$help")" = 'Monolith KDE' ] || fail "help header: $(sed -n 1p <<<"$help")"
groups="$(grep -E '^[A-Z][a-z]+$' <<<"$help" | tr '\n' ' ')"
[ "$groups" = 'Apps Updates System Setup Troubleshooting ' ] || fail "help groups: $groups"
for command in list 'install <app>...' 'remove <app>...' 'update <app>...' update changelogs 'rollback \[date\]' \
    'toggle-updates \[action\]' update-firmware status 'edition \[name\]' 'scheduler \[mode\]' clean-system \
    check-idle-power-draw bios bios-info adopt enroll-secure-boot-key setup-luks-tpm-unlock \
    remove-luks-tpm-unlock report-bug logs-this-boot logs-last-boot check-local-overrides; do
    grep -Eq "^  $command +[A-Z]" <<<"$help" || fail "help does not list $command"
done
! grep -Eiq 'justfile|recipe|alias' <<<"$help" || fail 'help talks about just'
[ "$(awk 'length > 80' <<<"$help")" = '' ] || fail 'help has lines over 80 characters'
[ "$(monolith --help)" = "$help" ] || fail '--help differs from help'
[ "$(monolith -h)" = "$help" ] || fail '-h differs from help'

# App commands go to the catalog, unchanged.
for words in 'list' 'install sidra' 'remove sidra epic-games' 'uninstall sidra' 'update sidra'; do
    fresh
    # shellcheck disable=SC2086
    monolith $words
    logged catalog "$words"
done
# Old ways to open the catalog still do.
for word in software monolith-software menu; do
    fresh
    monolith "$word"
    logged catalog ''
done

# update without app names updates everything with topgrade...
fresh
monolith update
logged topgrade ''
not_called catalog
# ...except when topgrade itself asks, as an older drop-in does: then only
# the apps update, or topgrade would start itself again.
fresh
CALL_MONOLITH=1 topgrade
logged topgrade ''
logged catalog update

# System commands run their scripts with their arguments.
fresh
monolith status
logged status ''
fresh
monolith scheduler gaming
logged scheduler 'gaming '
fresh
monolith scheduler gaming bpfland
logged scheduler 'gaming bpfland'
fresh
monolith rollback 20261027
logged rollback 20261027
fresh
monolith report-bug
logged report-bug ''
# Old names of commands still work.
fresh
monolith device-info
logged report-bug ''
fresh
monolith upgrade
logged topgrade ''

# Mistakes get a plain answer, without running anything else.
fresh
out="$(monolith stauts 2>&1)" && fail 'an unknown command succeeded'
grep -q "monolith doesn't have a command called stauts" <<<"$out" || fail "unknown command: $out"
grep -q 'Did you mean: monolith status' <<<"$out" || fail "no suggestion: $out"
out="$(monolith sidra 2>&1)" && fail 'an app name as a command succeeded'
grep -q 'To install it, run: monolith install sidra' <<<"$out" || fail "app hint: $out"
out="$(monolith status update 2>&1)" && fail 'extra arguments were accepted'
grep -q "monolith status doesn't take arguments" <<<"$out" || fail "extra arguments: $out"
out="$(monolith rollback 20261027 status 2>&1)" && fail 'a second command ran'
grep -q 'monolith rollback takes at most one argument' <<<"$out" || fail "too many arguments: $out"
not_called status
not_called rollback

# monolith help COMMAND explains one command.
out="$(monolith help scheduler)"
grep -q '^monolith scheduler \[mode\]$' <<<"$out" || fail "help scheduler: $out"
grep -q 'Usage: monolith scheduler' <<<"$out" || fail 'help scheduler skipped the details'
out="$(monolith help device-info)"
grep -q '^monolith report-bug$' <<<"$out" || fail "help for an old name: $out"

# mjust and ujust are old names: on their own they list the commands.
[ "$(mjust)" = "$help" ] || fail 'mjust on its own does not show help'
[ "$(ujust)" = "$help" ] || fail 'ujust on its own does not show help'
fresh
ujust update
logged topgrade ''
fresh
mjust monolith
logged catalog ''

# The System tools menu in the catalog lists every system command, with
# choices for the ones that take an argument.
menu="$(monolith __menu)"
[ "$(cut -f1 <<<"$menu" | tr '\n' ' ')" = "$(just --justfile "$usr/share/monolith/justfile" --summary --unsorted) " ] ||
    fail "menu commands: $(cut -f1 <<<"$menu" | tr '\n' ' ')"
grep -q $'^scheduler\t[^\t]*\t-=Show the current scheduler;gaming=' <<<"$menu" || fail 'menu lacks scheduler choices'
grep -q $'^edition\t[^\t]*\t-=Show your edition;kde=[^;]*;kde-nvidia=' <<<"$menu" || fail 'menu lacks edition choices'
grep -q $'^status\t[^\t]*\t$' <<<"$menu" || fail 'status should not have choices'

# Tab completion.
words() { monolith __complete "$@" | cut -f1 | tr '\n' ' '; }
first="$(words)"
for command in list install remove update help status scheduler rollback edition report-bug; do
    [[ " $first " == *" $command "* ]] || fail "completion lacks $command"
done
[ "$(grep -o ' update ' <<<" $first " | wc -l)" = 1 ] || fail 'update is offered twice'
[ "$(words install)" = 'sidra epic-games ' ] || fail "install completion: $(words install)"
[ "$(words update)" = 'sidra epic-games ' ] || fail "update completion: $(words update)"
[ "$(words scheduler)" = 'gaming powersave auto off lowlatency ' ] || fail "scheduler completion: $(words scheduler)"
[ "$(words edition)" = 'kde kde-nvidia ' ] || fail "edition completion: $(words edition)"
[ "$(words rollback)" = 'list latest ' ] || fail "rollback completion: $(words rollback)"
[ "$(words status)" = '' ] || fail 'status completes arguments'

completions="$repo_root/files/system/usr/share"
bash_complete() {
    bash -c '. "$1"; shift; COMP_WORDS=("$@"); COMP_CWORD=$((${#COMP_WORDS[@]} - 1)); _monolith; echo "${COMPREPLY[*]}"' \
        _ "$completions/bash-completion/completions/monolith" "$@"
}
[ "$(bash_complete monolith sta)" = status ] || fail "bash: $(bash_complete monolith sta)"
[ "$(bash_complete mjust scheduler p)" = powersave ] || fail "bash mjust: $(bash_complete mjust scheduler p)"
[ "$(bash_complete ujust install si)" = sidra ] || fail "bash ujust: $(bash_complete ujust install si)"
bash -c '. "$1"; complete -p monolith mjust ujust' _ "$completions/bash-completion/completions/monolith" >/dev/null ||
    fail 'bash completion is not registered for every name'

if command -v fish >/dev/null; then
    fish_complete() {
        fish --no-config -c 'for f in $argv[1]/*.fish; source $f; end; complete -C $argv[2]' \
            "$completions/fish/vendor_completions.d" "$1" | cut -f1 | tr '\n' ' '
    }
    [ "$(fish_complete 'monolith sta')" = 'status ' ] || fail "fish: $(fish_complete 'monolith sta')"
    [ "$(fish_complete 'mjust edition ')" = 'kde kde-nvidia ' ] || fail "fish mjust: $(fish_complete 'mjust edition ')"
    [ "$(fish_complete 'ujust rollback l')" = 'latest list ' ] || fail "fish ujust: $(fish_complete 'ujust rollback l')"
else
    printf 'skipped fish completion: fish is not installed\n'
fi

if command -v zsh >/dev/null; then
    # _describe needs a live completion widget; this stand-in lists what it got.
    zsh_complete() {
        zsh -f -c '_describe() { local name=${@[-1]}; print -l -- "${(@P)name}"; }
            words=("${@[1,-1]}"); CURRENT=${#words}; source "$ZSH_COMPLETION"' zsh "$@" | cut -d: -f1 | tr '\n' ' '
    }
    export ZSH_COMPLETION="$completions/zsh/site-functions/_monolith"
    [ "$(zsh_complete monolith edition '')" = 'kde kde-nvidia ' ] || fail "zsh: $(zsh_complete monolith edition '')"
    [ "$(zsh_complete ujust scheduler '')" = 'gaming powersave auto off lowlatency ' ] ||
        fail "zsh ujust: $(zsh_complete ujust scheduler '')"
    head -n 1 "$ZSH_COMPLETION" | grep -qx '#compdef monolith mjust ujust' || fail 'zsh completion misses a name'
else
    printf 'skipped zsh completion: zsh is not installed\n'
fi

# The catalog's System tools entry sits below the app categories and runs the
# system commands, offering choices for those that take one. Drive the real
# catalog with numbers on a terminal; a stand-in sudo keeps anything from
# asking for a password.
menu_usr="$test_root/menu/usr"
mkdir -p "$menu_usr/bin" "$menu_usr/libexec/monolith" "$menu_usr/share" "$test_root/menu/home" "$test_root/menu/bin"
cp "$repo_root/files/system/usr/bin/monolith" "$menu_usr/bin/"
cp "$repo_root/files/system/usr/libexec/monolith/"{software,common.sh,gear-lever-app,ghostty-themes} "$menu_usr/libexec/monolith/"
cp -r "$repo_root/files/system/usr/share/monolith" "$menu_usr/share/"
for script in status scheduler rollback edition report-bug; do
    stand_in "$menu_usr/libexec/monolith/$script" "$script"
done
stand_in "$test_root/menu/bin/sudo" sudo 'exit 1'
fresh
# 10 System tools, 8 scheduler, 2 gaming, Enter, 21 Back, 11 Exit.
printf '10\n8\n2\n\n21\n11\n' | PATH="$test_root/menu/bin:$PATH" HOME="$test_root/menu/home" TERM=dumb \
    MONOLITH_SELECTOR=numbered timeout 60 script -qec "$menu_usr/bin/monolith" /dev/null >"$test_root/menu.out" 2>&1 ||
    fail "the catalog menu did not finish: $(tail -n 5 "$test_root/menu.out")"
menu_out="$(tr -d '\r' <"$test_root/menu.out")"
grep -Eq '^  10\) System tools +Status, updates, scheduler, rollback and more$' <<<"$menu_out" ||
    fail 'System tools is not the tenth entry, right after the catalog'
grep -Eq '^  11\) Exit$' <<<"$menu_out" || fail 'Exit is not last'
grep -Eq '^   2\) Gaming: scx_lavd tuned for low latency$' <<<"$menu_out" || fail 'scheduler choices missing'
logged scheduler 'gaming '
not_called sudo

printf 'monolith command tests passed\n'
