// Monolith fork of plasma-desktop's default panel template (as of Plasma
// 6.7.2). Fedora's org.fedoraproject.fedora* look-and-feel layouts all load
// this template by name, so overwriting it here changes what a first login
// creates. Two deviations from upstream, both at the bottom:
//   - the icons-only task manager gets an explicit launcher list matching the
//     GNOME edition's favorite-apps (Files is Dolphin here), instead of the
//     applet default that pins Discover (removed from the image) and System
//     Settings;
//   - Kickoff gets the same list as its initial favorites (kactivitymanagerd
//     imports the key on first login), replacing the browser/Discover/System
//     Settings default.
// The start-menu button also gets the Monolith mark, set on the Kickoff below.
// Existing users keep their plasma-org.kde.plasma.desktop-appletsrc; this only
// shapes new logins. Re-diff against upstream when rebasing to a new Plasma.

var panel = new Panel
var panelScreen = panel.screen

// No need to set panel.location as ShellCorona::addPanel will automatically pick one available edge

// For an Icons-Only Task Manager on the bottom, *3 is too much, *2 is too little
// Round up to next highest even number since the Panel size widget only displays
// even numbers
panel.height = 2 * Math.ceil(gridUnit * 2.5 / 2)

// Restrict horizontal panel to a maximum size of a 21:9 monitor
const maximumAspectRatio = 21/9;
if (panel.formFactor === "horizontal") {
    const geo = screenGeometry(panelScreen);
    const maximumWidth = Math.ceil(geo.height * maximumAspectRatio);

    if (geo.width > maximumWidth) {
        panel.alignment = "center";
        panel.minimumLength = maximumWidth;
        panel.maximumLength = maximumWidth;
    }
}

var kickoff = panel.addWidget("org.kde.plasma.kickoff")
//panel.addWidget("org.kde.plasma.showActivityManager")
panel.addWidget("org.kde.plasma.pager")
var tasks = panel.addWidget("org.kde.plasma.icontasks")
panel.addWidget("org.kde.plasma.marginsseparator")

/* Next up is determining whether to add the Input Method Panel
 * widget to the panel or not. This is done based on whether
 * the system locale's language id is a member of the following
 * white list of languages which are known to pull in one of
 * our supported IME backends when chosen during installation
 * of common distributions. */

var langIds = ["as",    // Assamese
               "bn",    // Bengali
               "bo",    // Tibetan
               "brx",   // Bodo
               "doi",   // Dogri
               "gu",    // Gujarati
               "hi",    // Hindi
               "ja",    // Japanese
               "kn",    // Kannada
               "ko",    // Korean
               "kok",   // Konkani
               "ks",    // Kashmiri
               "lep",   // Lepcha
               "mai",   // Maithili
               "ml",    // Malayalam
               "mni",   // Manipuri
               "mr",    // Marathi
               "ne",    // Nepali
               "or",    // Odia
               "pa",    // Punjabi
               "sa",    // Sanskrit
               "sat",   // Santali
               "sd",    // Sindhi
               "si",    // Sinhala
               "ta",    // Tamil
               "te",    // Telugu
               "th",    // Thai
               "ur",    // Urdu
               "vi",    // Vietnamese
               "zh_CN", // Simplified Chinese
               "zh_TW"] // Traditional Chinese

if (langIds.indexOf(languageId) != -1) {
    panel.addWidget("org.kde.plasma.kimpanel");
}

panel.addWidget("org.kde.plasma.systemtray")
panel.addWidget("org.kde.plasma.digitalclock")
panel.addWidget("org.kde.plasma.showdesktop")

// Monolith pins, mirroring the GNOME edition's favorite-apps in
// zzz-monolith.gschema.override: Brave, Ptyxis, file manager (Dolphin here,
// Nautilus there), Steam, Bazaar.
tasks.currentConfigGroup = ["General"]
tasks.writeConfig("launchers",
    "applications:brave-origin-nightly.desktop," +
    "applications:org.gnome.Ptyxis.desktop," +
    "applications:org.kde.dolphin.desktop," +
    "applications:steam.desktop," +
    "applications:io.github.kolunmi.Bazaar.desktop")

kickoff.currentConfigGroup = ["General"]
// Brand the start-menu button with the Monolith "M" mark. On Plasma 6 the
// kickoff applet is a compiled plugin (no contents/config/main.xml to patch),
// and the icon default (start-here-kde) is baked into the .so; the only
// override is the applet's own "icon" config, the same key Fedora's
// look-and-feel setup script rewrites. Breeze Dark (this image's default global
// theme) ships no such setup script, so set it here on the Kickoff this
// template creates. start-here-monolith is the Monolith mark (logo-menu.png,
// squared) installed as a themed icon in hicolor/places; referencing it by
// name instead of an absolute pixmap path keeps it listed in the icon picker,
// so a user who switches the button icon can pick the mark back. Shipped
// white for the dark panel.
kickoff.writeConfig("icon", "start-here-monolith")
kickoff.writeConfig("favorites",
    "brave-origin-nightly.desktop," +
    "org.gnome.Ptyxis.desktop," +
    "org.kde.dolphin.desktop," +
    "steam.desktop," +
    "io.github.kolunmi.Bazaar.desktop")
