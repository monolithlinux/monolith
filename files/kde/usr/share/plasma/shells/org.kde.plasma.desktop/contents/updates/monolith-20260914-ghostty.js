// Plasma runs update scripts once per user when plasmashell starts.
// Replace only the old terminal; retain each user's panels and launcher order.
// Use a list default so KConfig preserves escaping and per-activity launchers.
var containments = desktops().concat(panels());
containments.forEach(function(containment) {
    containment.widgets().forEach(function(widget) {
        if (widget.type !== "org.kde.plasma.icontasks"
                && widget.type !== "org.kde.plasma.taskmanager") {
            return;
        }

        widget.currentConfigGroup = ["General"];
        var launchers = widget.readConfig("launchers", []);
        var changed = false;
        var migrated = [];
        for (var i = 0; i < launchers.length; i++) {
            // Activity-specific pins start with "[activity IDs]\n". Preserve
            // that prefix, as well as the application's URL representation.
            var launcher = String(launchers[i]);
            var replacement = launcher.replace(
                /(^|\n)(applications:|file:\/\/\/usr\/share\/applications\/|\/usr\/share\/applications\/)?org\.gnome\.Ptyxis\.desktop$/,
                "$1$2com.mitchellh.ghostty.desktop");
            migrated.push(replacement);
            changed = changed || replacement !== launcher;
        }
        if (changed) {
            widget.writeConfig("launchers", migrated);
            widget.reloadConfig();
        }
    });
});

// Follow an explicit old terminal preference, leaving other terminal choices
// and commands intact. Unset preferences inherit Monolith's system defaults.
var general = ConfigFile("kdeglobals", "General");
var terminalService = general.readEntry("TerminalService");
if (terminalService === "org.gnome.Ptyxis.desktop"
        || terminalService === "org.gnome.Ptyxis") {
    general.writeEntry("TerminalService", "com.mitchellh.ghostty.desktop");
}
var terminalApplication = general.readEntry("TerminalApplication");
if (terminalApplication === "ptyxis" || terminalApplication === "/usr/bin/ptyxis"
        || terminalApplication === "ptyxis --new-window"
        || terminalApplication === "/usr/bin/ptyxis --new-window") {
    general.writeEntry("TerminalApplication", "monolith-ghostty");
}

// Kickoff's existing favorites live in KActivities, not the applet's legacy
// "favorites" key. The hidden-from-menus Ptyxis desktop compatibility entry
// keeps those favorites working without rewriting users' activity databases.
