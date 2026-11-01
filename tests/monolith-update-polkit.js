// The polkit rule that lets people update without a password: who gets YES
// for which action, and that it stays plain ES5 for polkit's engine.
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const file = path.join(__dirname, "..", "files/system/usr/share/polkit-1/rules.d/50-monolith-updates.rules");
const source = fs.readFileSync(file, "utf8");

// polkit runs rules with Duktape, an ES5 engine.
const code = source.replace(/\/\/.*$/gm, "");
for (const modern of [/\blet\s/, /\bconst\s/, /=>/, /`/, /\.includes\(/, /\bclass\s/]) {
    assert(!modern.test(code), `the rule uses ${modern}, which polkit's ES5 engine can't run`);
}

const rules = [];
const Result = { YES: "yes", NO: "no", AUTH_ADMIN: "auth_admin", NOT_HANDLED: null };
vm.runInNewContext(source, { polkit: { Result, addRule: (rule) => rules.push(rule) } });
assert.strictEqual(rules.length, 1, "the file adds one rule");

function decide(id, { groups = [], local = true, active = true } = {}) {
    const subject = { user: "max", local, active, isInGroup: (group) => groups.includes(group) };
    for (const rule of rules) {
        const result = rule({ id }, subject);
        if (result) return result;
    }
    return null;
}

const updates = [
    "org.projectatomic.rpmostree1.upgrade",
    "org.projectatomic.rpmostree1.repo-refresh",
    "org.freedesktop.Flatpak.app-update",
    "org.freedesktop.Flatpak.runtime-update",
    "org.freedesktop.Flatpak.appstream-update",
    "org.freedesktop.Flatpak.metadata-update",
];
const admin = { groups: ["wheel"] };
const overSsh = { local: false, active: false };

for (const id of updates) {
    assert.strictEqual(decide(id, admin), "yes", `${id}: an admin at the machine`);
    assert.strictEqual(decide(id, { ...admin, ...overSsh }), "yes", `${id}: an admin over SSH`);
    assert.strictEqual(decide(id), "yes", `${id}: anyone at the machine`);
    assert.strictEqual(decide(id, overSsh), null, `${id}: a non-admin over SSH still asks`);
    assert.strictEqual(decide(id, { active: false }), null, `${id}: a non-admin's inactive session still asks`);
}

// Everything that changes more than the next build still asks.
for (const id of [
    "org.projectatomic.rpmostree1.rebase",
    "org.projectatomic.rpmostree1.deploy",
    "org.projectatomic.rpmostree1.rollback",
    "org.projectatomic.rpmostree1.install-uninstall-packages",
    "org.projectatomic.rpmostree1.override",
    "org.projectatomic.rpmostree1.bootconfig",
    "org.projectatomic.rpmostree1.cleanup",
    "org.freedesktop.Flatpak.app-install",
    "org.freedesktop.Flatpak.app-uninstall",
    "org.freedesktop.Flatpak.modify-repo",
    "org.freedesktop.login1.reboot",
]) {
    assert.strictEqual(decide(id, admin), null, `${id} must not be granted`);
}

console.log("update polkit rule tests passed");
