#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
helper="$repo_root/files/kde/usr/bin/monolith-ghostty-window-size"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT
mkdir -p "$test_root/bin"

# Mock only KConfig and D-Bus; run the installed shell logic and real flock.
cat > "$test_root/bin/kconfig-mock" <<'PY'
#!/usr/bin/env python3
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

path = Path(os.environ['GHOSTTY_RULES_TEST_CONFIG'])
config = json.loads(path.read_text()) if path.exists() else {}
assert args[:2] == ['--file', 'kwinrulesrc']
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
for program in kreadconfig6 kwriteconfig6 gdbus; do
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
    export GHOSTTY_RULES_TEST_CALLS="$case_root/calls.jsonl"
    marker="$XDG_STATE_HOME/monolith/ghostty-window-size-v1"
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
cp "$GHOSTTY_RULES_TEST_CALLS" "$case_root/expected-calls"
"$helper"
cmp "$case_root/expected" "$GHOSTTY_RULES_TEST_CONFIG"
cmp "$case_root/expected-calls" "$GHOSTTY_RULES_TEST_CALLS"

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
{"General":{"rules":"monolith-ghostty-window-size","count":"1"},"monolith-ghostty-window-size":{"Description":"My rule","wmclass":"com.mitchellh.ghostty","wmclassmatch":"1","wmclasscomplete":"false","types":"1","sizerule":"0","size":"1200,840","enabled":"false"}}
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
