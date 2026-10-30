#!/usr/bin/env bash
# The system commands behind `monolith status`, `scheduler`, `rollback`,
# `edition` and `report-bug`, run against stand-ins for rpm-ostree, mokutil,
# systemctl, the registry and /sys.
# Code for other shells and for stand-in programs is single-quoted on purpose.
# shellcheck disable=SC2016
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/..")" && repo_root="$(pwd -P)"
libexec="$repo_root/files/system/usr/libexec/monolith"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
has() { grep -qF -- "$2" <<<"$1" || fail "expected '$2' in:"$'\n'"$1"; }
lacks() { ! grep -qF -- "$2" <<<"$1" || fail "did not expect '$2' in:"$'\n'"$1"; }

bin="$test_root/bin"
log="$test_root/log"
sys="$test_root/sys"
etc="$test_root/etc"
vendor="$test_root/usr-share"
mkdir -p "$bin" "$log"
# The config scx-tools 1.1.3 ships as /usr/share/scx_loader/config.toml.
shipped_config="$test_root/shipped-config.toml"
cat >"$shipped_config" <<'EOF'
# This field specifies the scheduler that will be started automatically when scx_loader starts (e.g., on boot).
#default_sched = "scx_cosmos"

# This field specifies the mode which will be used when scx_loader starts (e.g., on boot).
#default_mode = "Auto"

# This "structure" allows configuring flags for each scheduler mode of particular scx scheduler
#[scheds.'scheduler']
#auto_mode = []
#gaming_mode = []
#lowlatency_mode = []
#powersave_mode = []
#server_mode = []
EOF

cat >"$bin/rpm-ostree" <<EOF
#!/usr/bin/env bash
if [ "\$1" = status ]; then
    [ -e "$test_root/status.json" ] || exit 1
    cat "$test_root/status.json"
else
    printf '%s\n' "\$*" >>"$log/rpm-ostree"
fi
EOF
cat >"$bin/mokutil" <<'EOF'
#!/usr/bin/env bash
case "$1" in
    --sb-state) printf '%s\n' "${SB_STATE-SecureBoot enabled}" ;;
    --test-key) printf '%s %s\n' "$2" "${KEY_STATE-is already enrolled}" ;;
esac
EOF
# systemctl restarts scx_loader the way the real one starts: it reads the first
# config it finds and runs default_sched, or nothing.
cat >"$bin/systemctl" <<EOF
#!/usr/bin/env bash
case "\$*" in
    "-q is-enabled rpm-ostreed-automatic.timer") [ -z "\${UPDATES_OFF-}" ] ;;
    "-q is-enabled scx_loader.service") true ;;
    "restart scx_loader.service")
        printf '%s\n' "\$*" >>"$log/systemctl"
        sched="\$(python3 -c 'import sys, tomllib, pathlib
for path in sys.argv[1:]:
    if pathlib.Path(path).exists():
        print(tomllib.load(open(path, "rb")).get("default_sched", ""))
        break' "$etc/scx_loader/config.toml" "$etc/scx_loader.toml" \\
            "$vendor/scx_loader/config.toml" "$vendor/scx_loader.toml")"
        if [ -n "\$sched" ] && [ -z "\${SCHEDULER_FAILS-}" ]; then
            echo enabled >"$sys/kernel/sched_ext/state"
            echo "\${sched#scx_}" >"$sys/kernel/sched_ext/root/ops"
        else
            echo disabled >"$sys/kernel/sched_ext/state"
        fi
        ;;
    *) printf '%s\n' "\$*" >>"$log/systemctl" ;;
esac
EOF
cat >"$bin/busctl" <<'EOF'
#!/usr/bin/env bash
case "$*" in
    *CurrentScheduler*) printf 's "%s"\n' "${LOADER_SCHEDULER-scx_lavd}" ;;
    *SchedulerMode*) printf 'u %s\n' "${LOADER_MODE-1}" ;;
    *) exit 1 ;;
esac
EOF
cat >"$bin/curl" <<EOF
#!/usr/bin/env bash
[ -z "\${OFFLINE-}" ] || exit 7
case "\$*" in
    *"/token?scope=repository:monolithlinux/"*) echo '{"token": "anonymous"}' ;;
    *"/tags/list"*) cat "$test_root/tags.json" ;;
    *) exit 22 ;;
esac
EOF
printf '#!/bin/sh\nexec "$@"\n' >"$bin/sudo"
printf '#!/bin/sh\nprintf "%%s\\n" "$1" >>"%s/xdg-open"\n' "$log" >"$bin/xdg-open"
printf '#!/bin/sh\necho "01:00.0 VGA compatible controller: NVIDIA Corporation AD104 [GeForce RTX 4070] (rev a1)"\n' >"$bin/lspci"
printf '#!/bin/sh\nprintf "org.example.One\\norg.example.Two\\n"\n' >"$bin/flatpak"
printf '#!/bin/sh\ncase "$*" in *mesa-dri-drivers*) echo 26.2.5 ;; *plasma-workspace*) echo 6.7.5 ;; esac\n' >"$bin/rpm"
printf '#!/bin/sh\n' >"$bin/sleep"
for scheduler in scx_lavd scx_bpfland; do printf '#!/bin/sh\n' >"$bin/$scheduler"; done
chmod +x "$bin"/*

printf 'NAME=Monolith\nPRETTY_NAME="Monolith KDE (NVIDIA)"\n' >"$test_root/os-release"
cat >"$test_root/tags.json" <<'EOF'
{"name": "monolithlinux/kde", "tags": ["20261025", "20261026", "20261027", "20261028", "20261029",
 "20261029-44", "latest", "44", "pr-50-44", "sha256-0123.sig"]}
EOF

export PATH="$bin:$PATH" NO_COLOR=1 MONOLITH_SYSFS="$sys" MONOLITH_ETC="$etc" MONOLITH_VENDOR="$vendor" \
    MONOLITH_OS_RELEASE="$test_root/os-release" MONOLITH_MOK_KEY="$test_root/monolith.der" \
    XDG_STATE_HOME="$test_root/state"
unset MONOLITH_YES DISPLAY WAYLAND_DISPLAY

# reset: a healthy kde-nvidia computer with an RTX card, NVIDIA's driver,
# Secure Boot on, a full update yesterday and no sched-ext scheduler.
reset() {
    rm -rf "$log" "$sys" "$etc" "$vendor" "$XDG_STATE_HOME"
    mkdir -p "$log" "$sys/kernel/sched_ext/root" "$sys/bus/pci/devices/0000:01:00.0" "$sys/module/nvidia" \
        "$etc" "$vendor/scx_loader" "$XDG_STATE_HOME/monolith/update-reminder"
    cp "$shipped_config" "$vendor/scx_loader/config.toml"
    echo disabled >"$sys/kernel/sched_ext/state"
    echo 0x10de >"$sys/bus/pci/devices/0000:01:00.0/vendor"
    echo 0x030000 >"$sys/bus/pci/devices/0000:01:00.0/class"
    echo 615.78.08 >"$sys/module/nvidia/version"
    touch -d '1 day ago' "$XDG_STATE_HOME/monolith/update-reminder/last-update"
    deployments signed kde-nvidia latest 44.20261029.0
    unset SB_STATE KEY_STATE UPDATES_OFF SCHEDULER_FAILS OFFLINE LOADER_SCHEDULER LOADER_MODE
}
no_nvidia() { rm -rf "$sys/bus/pci/devices/0000:01:00.0" "$sys/module/nvidia"; }

# deployments SIGNED EDITION TAG VERSION [STAGED] [PREVIOUS] [LAYERED]
deployments() {
    local ref
    case "$1" in
        signed) ref="ostree-image-signed:docker://ghcr.io/monolithlinux/$2:$3" ;;
        unsigned) ref="ostree-unverified-registry:ghcr.io/monolithlinux/$2:$3" ;;
        other) ref="ostree-unverified-registry:ghcr.io/someone/$2:$3" ;;
    esac
    jq -n --arg ref "$ref" --arg version "$4" --arg staged "${5-}" --arg previous "${6-}" --arg layered "${7-}" '
        {deployments: (
            (if $staged != "" then [{booted: false, staged: true, version: $staged, "container-image-reference": $ref}] else [] end)
            + [{booted: true, staged: false, version: $version, "container-image-reference": $ref,
                "requested-packages": ($layered | split(" ") | map(select(. != ""))), "requested-local-packages": []}]
            + (if $previous != "" then [{booted: false, staged: false, version: $previous, "container-image-reference": $ref}] else [] end)),
         transaction: null, "cached-update": null, "update-driver": null}' >"$test_root/status.json"
}
run() { "$libexec/$1" "${@:2}" </dev/null 2>&1; }
yes_run() { MONOLITH_YES=1 "$libexec/$1" "${@:2}" </dev/null 2>&1; }
rebased() { [ "$(cat "$log/rpm-ostree" 2>/dev/null)" = "rebase ostree-image-signed:docker://ghcr.io/monolithlinux/$1" ] ||
    fail "expected a rebase to $1, got: $(cat "$log/rpm-ostree" 2>/dev/null)"; }
not_rebased() { [ ! -e "$log/rpm-ostree" ] || fail "rebased: $(cat "$log/rpm-ostree")"; }

for script in status scheduler rollback edition report-bug; do
    run "$script" --help | grep -q "^Usage: monolith $script" || fail "$script --help"
done

# status ---------------------------------------------------------------------
reset
out="$(run status)" || fail "a healthy computer needs attention: $out"
has "$out" 'Monolith KDE (NVIDIA)'
has "$out" 'Build        44.20261029.0 from kde-nvidia:latest, signed'
has "$out" 'Updates      up to date'
has "$out" 'Secure Boot  on'
has "$out" 'NVIDIA       driver 615.78.08 is running'
has "$out" 'Full update  yesterday'
has "$out" "Scheduler    the kernel's own (try: monolith scheduler gaming)"
has "$out" 'Everything looks fine.'

reset
deployments unsigned kde latest 44.20261029.0
no_nvidia
out="$(run status)" && fail 'an unsigned image passed'
has "$out" 'from kde:latest, not signed'
has "$out" 'rpm-ostree rebase ostree-image-signed:docker://ghcr.io/monolithlinux/kde:latest'
lacks "$out" '  NVIDIA  '

reset
deployments signed kde-nvidia 20261027 44.20261027.0
out="$(run status)" && fail 'paused updates passed'
has "$out" 'Updates      paused on the build from Tue, Oct 27, 2026'
has "$out" 'monolith rollback latest'

reset
deployments signed kde-nvidia latest 44.20261029.0 44.20261030.0 44.20261028.0 'htop nvtop'
out="$(run status)" || fail "a downloaded update needs attention: $out"
has "$out" 'build 44.20261030.0 is downloaded; restart to use it'
has "$out" 'Layered      htop, nvtop'

reset
rm -rf "$sys/module/nvidia"
out="$(run status)" && fail 'a missing NVIDIA driver passed'
has "$out" "NVIDIA's driver didn't load"
has "$out" 'journalctl -b -k --grep=nvidia'

reset
deployments signed kde latest 44.20261029.0
rm -rf "$sys/module/nvidia"
out="$(run status)" || fail "an NVIDIA card on the KDE edition needs attention: $out"
has "$out" 'NVIDIA       card found, running on Nouveau/NVK'
has "$out" 'Tip: For NVIDIA'
has "$out" 'monolith edition kde-nvidia'

reset
no_nvidia
out="$(run status)" && fail 'the NVIDIA edition without a card passed'
has "$out" 'monolith edition kde'

reset
touch -d '10 days ago' "$XDG_STATE_HOME/monolith/update-reminder/last-update"
out="$(run status)" && fail 'an old update passed'
has "$out" 'Full update  10 days ago'
has "$out" "haven't been updated for 10 days"

reset
out="$(SB_STATE='SecureBoot disabled' KEY_STATE='is not enrolled' run status)" ||
    fail "Secure Boot off needs attention: $out"
has "$out" 'off (before you turn it on: monolith enroll-secure-boot-key)'
out="$(SB_STATE='SecureBoot disabled' KEY_STATE='is already enrolled' run status)" || fail "$out"
has "$out" "off; Monolith's key is enrolled, so you can turn it on"
out="$(KEY_STATE='is already in the enrollment request' run status)" && fail 'a waiting key enrollment passed'
has "$out" 'choose Enroll MOK'

reset
echo enabled >"$sys/kernel/sched_ext/state"
echo lavd >"$sys/kernel/sched_ext/root/ops"
out="$(run status)" || fail "$out"
has "$out" 'Scheduler    scx_lavd in gaming mode'
out="$(UPDATES_OFF=1 run status)" || fail "$out"
has "$out" 'Auto updates off (turn them on with: monolith toggle-updates enable)'

reset
rm "$test_root/status.json"
out="$(run status)" || true
has "$out" "Build        unknown, rpm-ostree didn't answer"
run status extra >/dev/null && fail 'status took an argument'

# scheduler ------------------------------------------------------------------
config="$etc/scx_loader/config.toml"
toml() { python3 -c 'import sys, tomllib; c = tomllib.load(open(sys.argv[1], "rb")); print(c.get("default_sched"), c.get("default_mode"))' "$1"; }

reset
out="$(run scheduler)"
has "$out" "Running now: the kernel's own scheduler."
has "$out" "At boot: the kernel's own scheduler."
out="$(run scheduler gaming)" || fail "$out"
has "$out" 'Gaming mode is on (scx_lavd). It stays on after you restart.'
[ "$(toml "$config")" = 'scx_lavd Gaming' ] || fail "config: $(toml "$config")"
[ "$(cat "$log/systemctl")" = 'restart scx_loader.service' ] || fail "systemctl: $(cat "$log/systemctl")"
out="$(run scheduler)"
has "$out" 'Running now: the sched-ext scheduler lavd.'
has "$out" 'At boot: scx_lavd in Gaming mode.'
# The new file in /etc starts as a copy of the shipped one, which scx_loader
# stops reading once /etc has a config. The shipped file stays as it was.
has "$(cat "$config")" '#default_sched = "scx_cosmos"'
cmp -s "$shipped_config" "$vendor/scx_loader/config.toml" || fail 'changed the shipped config'

# Settings from a shipped config carry over too.
reset
cat >"$vendor/scx_loader/config.toml" <<'EOF'
default_sched = "scx_bpfland"

[scheds.scx_lavd]
gaming_mode = ["--performance", "--slice-max-us", "4000"]
EOF
has "$(run scheduler)" 'At boot: scx_bpfland in Auto mode.'
run scheduler gaming >/dev/null || fail 'gaming over a shipped config'
[ "$(toml "$config")" = 'scx_lavd Gaming' ] || fail "config: $(toml "$config")"
has "$(cat "$config")" 'gaming_mode = ["--performance", "--slice-max-us", "4000"]'

# Your own settings in the file stay, and running it again doesn't pile up.
reset
mkdir -p "$etc/scx_loader"
cat >"$config" <<'EOF'
# My scx_loader settings.
default_sched = "scx_bpfland"
default_mode = "Auto"

[scheds.scx_lavd]
gaming_mode = ["--performance"]
default_mode = "this is inside a table and stays"
EOF
run scheduler powersave >/dev/null || fail 'powersave'
run scheduler powersave bpfland >/dev/null || fail 'powersave bpfland'
[ "$(toml "$config")" = 'scx_bpfland PowerSave' ] || fail "config: $(toml "$config")"
[ "$(grep -c '^default_sched' "$config")" = 1 ] || fail "duplicate keys: $(cat "$config")"
[ "$(grep -c '^# Set by monolith scheduler' "$config")" = 1 ] || fail "duplicate markers: $(cat "$config")"
has "$(cat "$config")" '# My scx_loader settings.'
has "$(cat "$config")" 'gaming_mode = ["--performance"]'
has "$(cat "$config")" 'default_mode = "this is inside a table and stays"'

# off leaves a file scx_loader can still start with: an empty one stops it.
out="$(run scheduler off)" || fail "$out"
has "$out" "The kernel's own scheduler is back"
[ "$(toml "$config")" = 'None None' ] || fail "off config: $(toml "$config")"
[ -s "$config" ] || fail 'off left an empty config'
[ "$(cat "$sys/kernel/sched_ext/state")" = disabled ] || fail 'off left a scheduler running'

# The older /etc/scx_loader.toml is used when it's the only config.
reset
printf 'default_sched = "scx_bpfland"\n' >"$etc/scx_loader.toml"
run scheduler auto >/dev/null || fail 'auto with the older config'
[ "$(toml "$etc/scx_loader.toml")" = 'scx_lavd Auto' ] || fail "older config: $(toml "$etc/scx_loader.toml")"
[ ! -e "$config" ] || fail 'created a second config'

# refused WORDS... EXPECTED: the scheduler command fails with that message.
refused() {
    local out
    out="$(run scheduler "${@:1:$#-1}")" && fail "scheduler ${*:1:$#-1} succeeded"
    has "$out" "${@: -1}"
}
reset
refused turbo "There's no turbo mode"
refused gaming nosuch "scx_loader can't run scx_nosuch"
refused gaming flash "scx_flash isn't installed"
refused off lavd "doesn't take a scheduler name"
[ ! -e "$config" ] || fail 'a bad request wrote the config'

# A config that isn't valid TOML is left alone.
reset
mkdir -p "$etc/scx_loader"
printf '[scheds.scx_lavd\n' >"$config"
out="$(run scheduler gaming)" && fail 'wrote over a broken config'
has "$out" "isn't valid TOML, so nothing was changed"
[ "$(cat "$config")" = '[scheds.scx_lavd' ] || fail 'changed a broken config'

reset
out="$(SCHEDULER_FAILS=1 run scheduler gaming)" && fail 'a scheduler that failed to start passed'
has "$out" "scx_lavd didn't start"
has "$out" 'journalctl -b -u scx_loader'

reset
rm "$sys/kernel/sched_ext/state"
refused gaming "can't run sched-ext schedulers"

# rollback -------------------------------------------------------------------
reset
deployments signed kde latest 44.20261029.0 '' 44.20261027.0
out="$(run rollback list)"
has "$out" 'Builds of kde you can go back to, newest first:'
has "$out" '  20261029  Thu, Oct 29, 2026  (running now)'
has "$out" '  20261025  Sun, Oct 25, 2026'
lacks "$out" 'latest'
lacks "$out" 'pr-50'
[ "$(grep -c '^  2026' <<<"$out")" = 5 ] || fail "list: $out"

out="$(yes_run rollback)" || fail "$out"
has "$out" 'pauses system updates'
rebased kde:20261027
reset
deployments signed kde latest 44.20261029.0 '' 44.20261027.0
yes_run rollback 2026-10-26 >/dev/null
rebased kde:20261026

# Without a yes, nothing happens.
reset
deployments signed kde latest 44.20261029.0 '' 44.20261027.0
out="$(run rollback 20261026)"
has "$out" 'Nothing changed.'
not_rebased

reset
deployments signed kde latest 44.20261029.0
out="$(yes_run rollback 20261001)" && fail 'went back to a missing build'
has "$out" "There's no kde build from Thu, Oct 1, 2026"
out="$(yes_run rollback last-tuesday)" && fail 'took a bad date'
has "$out" 'Dates look like 20261027'
out="$(yes_run rollback)" && fail 'went back without an earlier build'
has "$out" "There's no earlier build on this computer"
not_rebased
out="$(OFFLINE=1 yes_run rollback 20261027)" && fail 'went back while offline'
has "$out" "Couldn't reach ghcr.io"

# latest resumes updates; on latest already, there's nothing to do.
reset
deployments signed kde 20261027 44.20261027.0
yes_run rollback latest >/dev/null
rebased kde:latest
reset
out="$(yes_run rollback latest)"
has "$out" "You're already on the newest build"
not_rebased
reset
deployments signed kde 20261027 44.20261027.0
has "$(yes_run rollback 20261027)" "You're already on the build from Tue, Oct 27, 2026."
not_rebased

reset
deployments other kde latest 44.20261029.0
out="$(yes_run rollback)" && fail 'rolled back an image that is not Monolith'
has "$out" "You're not on one of Monolith's images (ghcr.io/someone/kde)"

# edition --------------------------------------------------------------------
reset
deployments signed kde latest 44.20261029.0
out="$(run edition)"
has "$out" "You're on KDE, for AMD or Intel graphics"
has "$out" 'monolith edition kde-nvidia'
out="$(yes_run edition kde-nvidia)" || fail "$out"
lacks "$out" "doesn't seem to have an NVIDIA card"
rebased kde-nvidia:latest

reset
no_nvidia
deployments signed kde latest 44.20261029.0
out="$(yes_run edition kde-nvidia)"
has "$out" "doesn't seem to have an NVIDIA card"
rebased kde-nvidia:latest

reset
out="$(yes_run edition kde)"
has "$out" 'Your NVIDIA card will run on Nouveau/NVK'
rebased kde:latest

reset
has "$(yes_run edition kde-nvidia)" "You're already on the kde-nvidia edition."
not_rebased
deployments signed kde-nvidia 20261027 44.20261027.0
has "$(yes_run edition kde-nvidia)" 'System updates come back on'
rebased kde-nvidia:latest

reset
out="$(yes_run edition gnome)" && fail 'switched to an edition that does not exist'
has "$out" "Monolith's editions are kde kde-nvidia."
has "$(run edition kde)" 'Nothing changed.'
not_rebased

# The editions here are the ones CI builds.
editions="$(bash -c '. "$1"; echo "${MONOLITH_EDITIONS[*]}"' _ "$libexec/common.sh")"
[ "$editions" = "$(bash "$repo_root/.github/workflows/pick-editions.sh" all | tr '\n' ' ' | sed 's/ $//')" ] ||
    fail "common.sh editions ($editions) differ from pick-editions.sh"

# report-bug -----------------------------------------------------------------
reset
deployments signed kde-nvidia latest 44.20261029.0 '' '' htop
out="$(WAYLAND_DISPLAY=wayland-0 run report-bug)" || fail "$out"
has "$out" 'Build: 44.20261029.0 from kde-nvidia:latest (signed: yes)'
has "$out" 'Layered packages: htop'
has "$out" 'NVIDIA Corporation AD104 [GeForce RTX 4070]'
has "$out" 'NVIDIA driver: 615.78.08'
has "$out" 'Flatpak apps: 2 system, 2 for this account'
has "$out" 'Opening a new bug report in your browser'
url="$(cat "$log/xdg-open")"
[[ $url == 'https://github.com/monolithlinux/monolith/issues/new?template=bug_report.yml&system='* ]] ||
    fail "issue link: $url"
lacks "$url" 'labels='
body="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.unquote(sys.argv[1].split("system=", 1)[1]))' "$url")"
has "$body" 'Build: 44.20261029.0 from kde-nvidia:latest (signed: yes)'
has "$body" "Kernel: $(uname -r)"
lacks "$body" "$(hostname)"
lacks "$body" "$(id -un)"
((${#url} < 6000)) || fail "the link is ${#url} characters, too long for GitHub"
# The issue form must have the field the link fills in.
grep -q '^    id: system$' "$repo_root/.github/ISSUE_TEMPLATE/bug_report.yml" || fail 'the issue form has no system field'

reset
out="$(run report-bug)"
has "$out" 'Open this link to start a bug report'
has "$out" 'https://github.com/monolithlinux/monolith/issues/new?template=bug_report.yml&system='
[ ! -e "$log/xdg-open" ] || fail 'opened a browser without a desktop'

printf 'system command tests passed\n'
