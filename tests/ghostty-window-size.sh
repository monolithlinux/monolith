#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
helper="$repo_root/files/kde/usr/bin/monolith-ghostty-window-size"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT
mkdir -p "$test_root/bin"

# Mock KConfig, D-Bus and its short settling delay; use the real shell/flock.
cat > "$test_root/bin/kconfig-mock" <<'PY'
#!/usr/bin/env python3
import configparser
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
kind = Path(sys.argv[0]).name
with open(os.environ['GHOSTTY_RULES_TEST_CALLS'], 'a') as log:
    log.write(json.dumps([kind, *args]) + '\n')
if kind == 'gdbus':
    assert args == ['call', '--session', '--dest', 'org.kde.KWin',
                    '--object-path', '/KWin', '--method', 'org.kde.KWin.reconfigure']
    sys.exit(int(os.environ.get('GHOSTTY_RULES_TEST_DBUS_FAILURE', '0')))
if kind == 'sleep':
    assert args == ['0.25']
    sys.exit(0)

assert args[0] == '--file'
filename = args[1]
if filename == 'kwinrulesrc':
    path = Path(os.environ['GHOSTTY_RULES_TEST_CONFIG'])
elif filename == 'kwinrc':
    path = Path(os.environ['GHOSTTY_RULES_TEST_PLUGIN_CONFIG'])
else:
    path = Path(os.environ['XDG_STATE_HOME']) / 'monolith/ghostty-window-size.ini'
    assert filename == str(path), filename
    assert kind == 'kreadconfig6'
if path.suffix == '.ini':
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path)
    config = {group: dict(parser[group]) for group in parser.sections()}
else:
    config = json.loads(path.read_text()) if path.exists() else {}
group = args[args.index('--group') + 1]
key = args[args.index('--key') + 1]
if kind == 'kreadconfig6':
    print(config.get(group, {}).get(key, args[args.index('--default') + 1]))
else:
    if os.environ.get('GHOSTTY_RULES_TEST_WRITE_FAILURE'):
        sys.exit(1)
    config.setdefault(group, {})[key] = args[-1]
    path.write_text(json.dumps(config))
PY
chmod +x "$test_root/bin/kconfig-mock"
for program in kreadconfig6 kwriteconfig6 gdbus sleep; do
    ln -s kconfig-mock "$test_root/bin/$program"
done
export PATH="$test_root/bin:$PATH"
export XDG_CURRENT_DESKTOP=KDE WAYLAND_DISPLAY=wayland-test
export DBUS_SESSION_BUS_ADDRESS=unix:path=/test-bus
unset DISPLAY

new_case() {
    case_root="$test_root/$1"
    mkdir -p "$case_root"
    export XDG_CONFIG_HOME="$case_root/config with spaces"
    export XDG_STATE_HOME="$case_root/state with spaces"
    export GHOSTTY_RULES_TEST_CONFIG="$case_root/rules.json"
    export GHOSTTY_RULES_TEST_PLUGIN_CONFIG="$case_root/plugins.json"
    export GHOSTTY_RULES_TEST_CALLS="$case_root/calls.jsonl"
    marker="$XDG_STATE_HOME/monolith/ghostty-window-size-v1"
    window_size_state="$XDG_STATE_HOME/monolith/ghostty-window-size.ini"
}

save_size() {
    mkdir -p -- "$(dirname -- "$window_size_state")"
    printf '[Window]\nwidth=%s\nheight=%s\n' "$1" "$2" > "$window_size_state"
}

assert_no_mutations() {
    if [[ -e "$case_root/expected" ]]; then
        cmp "$case_root/expected" "$GHOSTTY_RULES_TEST_CONFIG"
    else
        [[ ! -e "$GHOSTTY_RULES_TEST_CONFIG" ]]
    fi
    python3 - <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ['GHOSTTY_RULES_TEST_CALLS'])
calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
assert all(call[0] == 'kreadconfig6' for call in calls), calls
PY
}

existing_rule() {
    new_case "$1"
    mkdir -p -- "$(dirname -- "$marker")"
    : > "$marker"
    cat > "$GHOSTTY_RULES_TEST_CONFIG" <<'JSON'
{"General":{"rules":"custom-ghostty,monolith-ghostty-window-size","count":"2"},"custom-ghostty":{"wmclass":"com.mitchellh.ghostty","sizerule":"2","size":"900,600"},"monolith-ghostty-window-size":{"Description":"Ghostty: remember window size","wmclass":"com.mitchellh.ghostty","wmclassmatch":"1","wmclasscomplete":"false","types":"1","sizerule":"4","size":"800,500"}}
JSON
    save_size 1234 789
}

new_case first-launch
"$helper"
[[ -f "$marker" && "$(stat -c '%a' "$marker")" == 600 ]]
python3 - <<'PY'
import json, os
from pathlib import Path
config = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
assert config == {
    'General': {'rules': 'monolith-ghostty-window-size', 'count': '1'},
    'monolith-ghostty-window-size': {
        'Description': 'Ghostty: remember window size',
        'wmclass': 'com.mitchellh.ghostty', 'wmclassmatch': '1',
        'wmclasscomplete': 'false', 'types': '1', 'sizerule': '4',
    },
}
calls = [json.loads(line) for line in Path(os.environ['GHOSTTY_RULES_TEST_CALLS']).read_text().splitlines()]
assert sum(call[0] == 'gdbus' for call in calls) == 1
PY

# Once the user deletes the default rule, subsequent launches leave it deleted.
printf '{"General":{"rules":"","count":"0"}}\n' > "$GHOSTTY_RULES_TEST_CONFIG"
cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
: > "$GHOSTTY_RULES_TEST_CALLS"
"$helper"
assert_no_mutations

# A fresh helper process restores the durable size even with the old marker.
# The custom rule ahead of the stock default keeps its original priority.
for format in rules order; do
    existing_rule "persisted-$format"
    if [[ "$format" == order ]]; then
        python3 - <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ['GHOSTTY_RULES_TEST_CONFIG'])
config = json.loads(path.read_text())
config['General']['order'] = config['General'].pop('rules')
path.write_text(json.dumps(config))
PY
    fi
    cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
    "$helper"
    python3 - "$case_root/expected" <<'PY'
import json, os, sys
from pathlib import Path
before = json.loads(Path(sys.argv[1]).read_text())
after = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
before['monolith-ghostty-window-size']['size'] = '1234,789'
assert after == before
calls = [json.loads(line) for line in Path(os.environ['GHOSTTY_RULES_TEST_CALLS']).read_text().splitlines()]
writes = [call for call in calls if call[0] == 'kwriteconfig6']
assert writes == [['kwriteconfig6', '--file', 'kwinrulesrc', '--group',
                   'monolith-ghostty-window-size', '--key', 'size', '1234,789']], writes
assert sum(call[0] == 'gdbus' for call in calls) == 1
PY

    # Repeated launches with the same persisted size do not rewrite/reload.
    cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
    : > "$GHOSTTY_RULES_TEST_CALLS"
    "$helper"
    assert_no_mutations
done

# Persistent restoration respects explicit plugin and rule opt-outs.
for disabled in false 0 no off FaLsE OFF; do
    for target in plugin rule; do
        existing_rule "disabled-$target-$disabled"
        python3 - "$target" "$disabled" <<'PY'
import json, os, sys
from pathlib import Path
if sys.argv[1] == 'plugin':
    path = Path(os.environ['GHOSTTY_RULES_TEST_PLUGIN_CONFIG'])
    config = {'Plugins': {'monolith-ghostty-window-sizeEnabled': sys.argv[2]}}
else:
    path = Path(os.environ['GHOSTTY_RULES_TEST_CONFIG'])
    config = json.loads(path.read_text())
    config['monolith-ghostty-window-size']['Enabled'] = sys.argv[2]
path.write_text(json.dumps(config))
PY
        cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
        "$helper"
        assert_no_mutations
    done
done

# Turning off the KWin plugin before the first launch also opts out of rule
# initialization; this must not create a completion marker or state directory.
new_case disabled-first-launch
printf '{"Plugins":{"monolith-ghostty-window-sizeEnabled":"false"}}\n' > "$GHOSTTY_RULES_TEST_PLUGIN_CONFIG"
"$helper"
[[ ! -e "$XDG_STATE_HOME" ]]
assert_no_mutations

# Deletion, removal from the active order, or changed matching/size policy
# means the stock rule is no longer ours to seed with a recorded size.
for change in deleted unlisted wmclass wmclassmatch wmclasscomplete types sizerule \
        titlematch windowrolematch clientmachinematch tagmatch hastransientparentmatch; do
    existing_rule "changed-$change"
    python3 - "$change" <<'PY'
import json, os, sys
from pathlib import Path
path = Path(os.environ['GHOSTTY_RULES_TEST_CONFIG'])
config = json.loads(path.read_text())
change = sys.argv[1]
if change == 'deleted':
    del config['monolith-ghostty-window-size']
elif change == 'unlisted':
    config['General'] = {'rules': 'custom-ghostty', 'count': '1'}
else:
    config['monolith-ghostty-window-size'][change] = {
        'wmclass': 'another.application', 'wmclassmatch': '2',
        'wmclasscomplete': 'true', 'types': '0', 'sizerule': '3',
        'titlematch': '1', 'windowrolematch': '2', 'clientmachinematch': '3',
        'tagmatch': '1', 'hastransientparentmatch': '1',
    }[change]
path.write_text(json.dumps(config))
PY
    cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
    "$helper"
    assert_no_mutations
done

# Missing, malformed and out-of-range state must not replace a working size.
for dimension in width height; do
    for invalid in '' 0 -1 +900 01 12.5 abc 32768 999999999999999999999999; do
        existing_rule "invalid-$dimension-${invalid:-empty}"
        if [[ "$dimension" == width ]]; then
            save_size "$invalid" 789
        else
            save_size 1234 "$invalid"
        fi
        cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
        "$helper"
        assert_no_mutations
    done
done
existing_rule missing-state
rm -- "$window_size_state"
cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
"$helper"
assert_no_mutations

existing_rule valid-boundaries
save_size 1 32767
"$helper"
python3 - <<'PY'
import json, os
from pathlib import Path
config = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
assert config['monolith-ghostty-window-size']['size'] == '1,32767'
PY

# Append below existing rules so explicit user choices retain priority. Cover
# the current format and the newer order key without rewriting either rule.
for format in rules order; do
    new_case "existing-$format"
    python3 - "$format" <<'PY'
import json, os, sys
from pathlib import Path
config = {
    'General': {sys.argv[1]: 'brave,custom-ghostty', 'count': '2'},
    'brave': {'Description': 'Keep \\ literal, commas', 'adaptivesyncrule': '2'},
    'custom-ghostty': {'wmclass': 'com.mitchellh.ghostty', 'sizerule': '2', 'size': '900,600'},
}
Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).write_text(json.dumps(config))
PY
    cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
    "$helper"
    python3 - "$case_root/expected" "$format" <<'PY'
import json, os, sys
from pathlib import Path
before = json.loads(Path(sys.argv[1]).read_text())
after = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
assert after['brave'] == before['brave']
assert after['custom-ghostty'] == before['custom-ghostty']
assert after['General'][sys.argv[2]] == 'brave,custom-ghostty,monolith-ghostty-window-size'
assert after['General']['count'] == ('3' if sys.argv[2] == 'rules' else '2')
assert ('rules' not in after['General']) if sys.argv[2] == 'order' else ('order' not in after['General'])
PY
done

new_case existing-disabled
cat > "$GHOSTTY_RULES_TEST_CONFIG" <<'JSON'
{"General":{"rules":"monolith-ghostty-window-size","count":"1"},"monolith-ghostty-window-size":{"Description":"My rule","wmclass":"com.mitchellh.ghostty","wmclassmatch":"1","wmclasscomplete":"false","types":"1","sizerule":"0","size":"1200,840","Enabled":"false"}}
JSON
cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
"$helper"
python3 - "$case_root/expected" <<'PY'
import json, os, sys
from pathlib import Path
assert json.loads(Path(sys.argv[1]).read_text()) == json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
PY

new_case legacy-count
printf '{"General":{"count":"2"},"1":{"Description":"first"},"2":{"Description":"second"}}\n' > "$GHOSTTY_RULES_TEST_CONFIG"
"$helper"
python3 - <<'PY'
import json, os
from pathlib import Path
config = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
assert config['General'] == {'rules': '1,2,monolith-ghostty-window-size', 'count': '3'}
assert config['1'] == {'Description': 'first'} and config['2'] == {'Description': 'second'}
PY

# Non-window actions and non-KDE/headless environments create no state and
# never call KConfig or D-Bus. Child command options still open a window.
new_case non-graphical
for action in +list-themes +show-config +validate-config --help --version -h -v; do
    "$helper" "$action"
done
"$helper" --working-directory=/tmp --help
XDG_CURRENT_DESKTOP=GNOME "$helper"
WAYLAND_DISPLAY= DISPLAY= "$helper"
DBUS_SESSION_BUS_ADDRESS= "$helper"
PATH=/nonexistent /usr/bin/bash "$helper"
[[ ! -e "$XDG_STATE_HOME" && ! -e "$GHOSTTY_RULES_TEST_CALLS" ]]
"$helper" -e bash --help
[[ -f "$marker" ]]

# Failed writes and failed reloads leave no success marker and are retryable.
for failure in WRITE DBUS; do
    new_case "failure-$failure"
    if env "GHOSTTY_RULES_TEST_${failure}_FAILURE=1" "$helper"; then
        printf 'Expected %s failure\n' "$failure" >&2
        exit 1
    fi
    [[ ! -e "$marker" ]]
    "$helper"
    [[ -f "$marker" ]]
    python3 - <<'PY'
import json, os
from pathlib import Path
config = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
assert config['General'] == {'rules': 'monolith-ghostty-window-size', 'count': '1'}
PY
done

# After initialization, interrupted writes and reloads must still be retryable.
# A successful size write followed by failed D-Bus leaves the same-size retry
# responsible for reloading KWin before clearing its pending marker.
for failure in WRITE DBUS; do
    existing_rule "restore-failure-$failure"
    if env "GHOSTTY_RULES_TEST_${failure}_FAILURE=1" "$helper"; then
        printf 'Expected restore %s failure\n' "$failure" >&2
        exit 1
    fi
    [[ -f "$marker" && -f "$XDG_STATE_HOME/monolith/ghostty-window-size-reload" ]]
    "$helper"
    [[ ! -e "$XDG_STATE_HOME/monolith/ghostty-window-size-reload" ]]
    python3 - "$failure" <<'PY'
import json, os, sys
from pathlib import Path
config = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
assert config['monolith-ghostty-window-size']['size'] == '1234,789'
assert config['custom-ghostty']['size'] == '900,600'
calls = [json.loads(line) for line in Path(os.environ['GHOSTTY_RULES_TEST_CALLS']).read_text().splitlines()]
assert sum(call[0] == 'gdbus' for call in calls) == (2 if sys.argv[1] == 'DBUS' else 1)
PY
    cp "$GHOSTTY_RULES_TEST_CONFIG" "$case_root/expected"
    : > "$GHOSTTY_RULES_TEST_CALLS"
    "$helper"
    assert_no_mutations
done

new_case concurrent-launches
pids=()
for index in {1..8}; do
    "$helper" &
    pids+=("$!")
done
for pid in "${pids[@]}"; do
    wait "$pid"
done
python3 - <<'PY'
import json, os
from pathlib import Path
calls = [json.loads(line) for line in Path(os.environ['GHOSTTY_RULES_TEST_CALLS']).read_text().splitlines()]
assert sum(call[0] == 'gdbus' for call in calls) == 1
config = json.loads(Path(os.environ['GHOSTTY_RULES_TEST_CONFIG']).read_text())
assert config['General'] == {'rules': 'monolith-ghostty-window-size', 'count': '1'}
PY

# Run the actual launcher with a failing helper and a terminal stand-in to
# prove that failure neither prevents launch nor changes command arguments.
new_case launcher-failure
mkdir -p "$XDG_CONFIG_HOME/ghostty"
: > "$XDG_CONFIG_HOME/ghostty/config.ghostty"
printf '#!/usr/bin/env bash\nexit 23\n' > "$case_root/failing-helper"
cat > "$case_root/terminal" <<'SH'
#!/usr/bin/env bash
printf '%s\0' "$@" > "$GHOSTTY_RULES_TEST_ARGS"
SH
chmod +x "$case_root/failing-helper" "$case_root/terminal"
sed -e "s|/usr/bin/monolith-ghostty-window-size|$case_root/failing-helper|g" \
    -e "s|/usr/bin/ghostty.real|$case_root/terminal|g" \
    "$repo_root/files/kde/usr/bin/monolith-ghostty" > "$case_root/launcher"
export GHOSTTY_RULES_TEST_ARGS="$case_root/args"
bash "$case_root/launcher" -e fish --help
printf '%s\0' -e fish --help > "$case_root/expected-args"
cmp "$case_root/expected-args" "$GHOSTTY_RULES_TEST_ARGS"

printf 'Ghostty window-size initialization tests passed\n'
