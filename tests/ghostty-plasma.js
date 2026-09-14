#!/usr/bin/env node
// Exercise the shipped migration without changing the running Plasma session.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const migration = fs.readFileSync(path.join(__dirname,
    '../files/kde/usr/share/plasma/shells/org.kde.plasma.desktop/contents/updates/monolith-20260914-ghostty.js'), 'utf8');
const oldLauncher = 'applications:org.gnome.Ptyxis.desktop';
const newLauncher = 'applications:com.mitchellh.ghostty.desktop';
const copy = (value) => JSON.parse(JSON.stringify(value));

function migrate(desktopWidgets, panelWidgets, preferences, repetitions = 1) {
    const desktops = copy(desktopWidgets);
    const panels = copy(panelWidgets);
    const config = copy(preferences);
    const widgetWrites = [];
    const preferenceWrites = [];

    for (const widget of [...desktops, ...panels]) {
        widget.readConfig = (key, fallback) => {
            assert.deepEqual(copy(widget.currentConfigGroup), ['General']);
            assert.equal(key, 'launchers');
            // KConfig uses the default's type to parse escaped list entries.
            assert.ok(Array.isArray(fallback));
            return widget.launchers === undefined ? fallback : widget.launchers;
        };
        widget.writeConfig = (key, value) => {
            assert.deepEqual(copy(widget.currentConfigGroup), ['General']);
            assert.equal(key, 'launchers');
            assert.ok(Array.isArray(value));
            widget.launchers = copy(value);
            widgetWrites.push(widget);
        };
        widget.reloadConfig = () => {
            widget.reloads = (widget.reloads || 0) + 1;
        };
    }

    const context = vm.createContext({
        desktops: () => [{widgets: () => desktops}],
        panels: () => [{widgets: () => panels}],
        ConfigFile(file, group) {
            assert.equal(file, 'kdeglobals');
            assert.equal(group, 'General');
            return {
                readEntry: (key) => config[key] || '',
                writeEntry(key, value) {
                    config[key] = value;
                    preferenceWrites.push(key);
                },
            };
        },
    });
    for (let i = 0; i < repetitions; i++) {
        vm.runInContext(migration, context, {filename: 'monolith-20260914-ghostty.js'});
    }
    return {desktops, panels, config, widgetWrites, preferenceWrites};
}

// Real launcher formats include plain IDs, application URLs, and per-activity
// pins. Other pins keep their original order and arbitrary punctuation.
const desktopWidgets = [{
    type: 'org.kde.plasma.icontasks',
    launchers: ['applications:custom.desktop', oldLauncher, 'applications:steam.desktop'],
}];
const panelWidgets = [{
    type: 'org.kde.plasma.taskmanager',
    launchers: [
        '[activity-a,activity-b]\n' + oldLauncher,
        '/usr/share/applications/org.gnome.Ptyxis.desktop',
        'file:///usr/share/applications/org.gnome.Ptyxis.desktop',
        'org.gnome.Ptyxis.desktop',
    ],
}, {
    type: 'org.kde.plasma.clock',
    launchers: [oldLauncher],
}, {
    type: 'org.kde.plasma.icontasks',
    launchers: [
        'applications:org.gnome.Ptyxis.desktop-custom',
        'applications:org.gnome.Ptyxis.desktop?custom=1',
        'file:///home/user/org.gnome.Ptyxis.desktop',
        'applications:comma,name.desktop',
        'file:///home/user/back\\slash.desktop',
        newLauncher,
    ],
}, {
    type: 'org.kde.plasma.icontasks',
}];
const result = migrate(desktopWidgets, panelWidgets, {
    TerminalService: 'org.gnome.Ptyxis.desktop',
    TerminalApplication: 'ptyxis --new-window',
    Other: 'untouched',
}, 2);

assert.deepEqual(result.desktops[0].launchers,
    ['applications:custom.desktop', newLauncher, 'applications:steam.desktop']);
assert.deepEqual(result.panels[0].launchers, [
    '[activity-a,activity-b]\n' + newLauncher,
    '/usr/share/applications/com.mitchellh.ghostty.desktop',
    'file:///usr/share/applications/com.mitchellh.ghostty.desktop',
    'com.mitchellh.ghostty.desktop',
]);
assert.deepEqual(result.panels[1].launchers, [oldLauncher]);
assert.deepEqual(result.panels[2].launchers, panelWidgets[2].launchers);
assert.equal(result.panels[3].launchers, undefined);
assert.equal(result.widgetWrites.length, 2, 'second run must not rewrite pins');
assert.equal(result.desktops[0].reloads, 1);
assert.equal(result.panels[0].reloads, 1);
assert.deepEqual(result.config, {
    TerminalService: 'com.mitchellh.ghostty.desktop',
    TerminalApplication: 'monolith-ghostty',
    Other: 'untouched',
});
assert.equal(result.preferenceWrites.length, 2, 'second run must not rewrite preferences');

// Empty profiles inherit system defaults. Existing alternatives, custom Ptyxis
// commands, and already-migrated profiles must never acquire new preferences.
for (const preferences of [
    {},
    {TerminalService: 'kitty.desktop', TerminalApplication: 'kitty'},
    {TerminalService: 'org.kde.konsole.desktop', TerminalApplication: 'konsole'},
    {TerminalApplication: 'ptyxis --custom-arguments'},
    {TerminalApplication: '/home/user/bin/ptyxis'},
    {TerminalService: 'com.mitchellh.ghostty.desktop', TerminalApplication: 'monolith-ghostty'},
]) {
    const unchanged = migrate([], [], preferences);
    assert.deepEqual(unchanged.config, preferences);
    assert.deepEqual(unchanged.preferenceWrites, []);
    assert.deepEqual(unchanged.widgetWrites, []);
}

for (const command of ['ptyxis', '/usr/bin/ptyxis', 'ptyxis --new-window', '/usr/bin/ptyxis --new-window']) {
    const migrated = migrate([], [], {TerminalApplication: command});
    assert.equal(migrated.config.TerminalApplication, 'monolith-ghostty');
    assert.equal(migrated.config.TerminalService, undefined);
}
for (const service of ['org.gnome.Ptyxis', 'org.gnome.Ptyxis.desktop']) {
    const migrated = migrate([], [], {TerminalService: service});
    assert.equal(migrated.config.TerminalService, 'com.mitchellh.ghostty.desktop');
    assert.equal(migrated.config.TerminalApplication, undefined);
}

console.log('PASS: Plasma Ghostty migration preserves launcher layouts, activities, and custom terminal choices');
