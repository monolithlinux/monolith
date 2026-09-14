// SPDX-License-Identifier: Apache-2.0

// Record only dimensions. KWin's native rule applies them on the next launch,
// so user rules keep their normal precedence and matching behavior.
function create(save, defer) {
    var tracked = new Map();

    function sample(window, immediate) {
        if (!tracked.has(window) || window.fullScreen || window.minimized
                || window.maximizeMode !== 0)
            return;
        var width = Math.round(window.frameGeometry.width);
        var height = Math.round(window.frameGeometry.height);
        if (width > 0 && width <= 32767 && height > 0 && height <= 32767)
            save(width, height, immediate);
    }

    function untrack(window) {
        var callbacks = tracked.get(window);
        if (!callbacks)
            return;
        window.frameGeometryChanged.disconnect(callbacks.changed);
        window.maximizedChanged.disconnect(callbacks.changed);
        window.fullScreenChanged.disconnect(callbacks.changed);
        window.minimizedChanged.disconnect(callbacks.changed);
        window.closed.disconnect(callbacks.closed);
        tracked.delete(window);
    }

    function track(window) {
        if (tracked.has(window) || !window.normalWindow || window.transient
                || String(window.resourceClass) !== "com.mitchellh.ghostty")
            return;
        var changed = function() {
            // Wait until KWin finishes updating fullscreen/maximize flags.
            defer(function() { sample(window, false); });
        };
        var closed = function() {
            sample(window, true);
            untrack(window);
        };
        tracked.set(window, { changed: changed, closed: closed });
        window.frameGeometryChanged.connect(changed);
        window.maximizedChanged.connect(changed);
        window.fullScreenChanged.connect(changed);
        window.minimizedChanged.connect(changed);
        window.closed.connect(closed);
        changed();
    }

    function stop() {
        Array.from(tracked.keys()).forEach(untrack);
    }

    return { track: track, stop: stop };
}
