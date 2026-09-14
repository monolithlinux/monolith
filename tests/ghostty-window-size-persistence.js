// Run with Node in CI, or evaluate after persistence.js in Qt's QJSEngine.
(function() {
    let factory;
    if (typeof require === 'function') {
        const fs = require('node:fs');
        const path = require('node:path');
        const vm = require('node:vm');
        const source = fs.readFileSync(path.join(__dirname,
            '../files/kde/usr/share/kwin/scripts/monolith-ghostty-window-size/contents/code/persistence.js'), 'utf8');
        const context = vm.createContext({});
        vm.runInContext(source, context);
        factory = context.create;
    } else {
        factory = create;
    }

    function assert(condition, message) {
        if (!condition)
            throw new Error(message);
    }
    function equal(actual, expected, message) {
        assert(JSON.stringify(actual) === JSON.stringify(expected), message);
    }
    function signal() {
        const callbacks = new Set();
        return {
            connect: callback => callbacks.add(callback),
            disconnect: callback => callbacks.delete(callback),
            emit: () => Array.from(callbacks).forEach(callback => callback()),
            count: () => callbacks.size
        };
    }
    const signalNames = ['frameGeometryChanged', 'maximizedChanged',
        'fullScreenChanged', 'minimizedChanged', 'closed'];
    function window(overrides) {
        const result = Object.assign({
            resourceClass: 'com.mitchellh.ghostty', normalWindow: true,
            transient: false, fullScreen: false, minimized: false,
            maximizeMode: 0, frameGeometry: {width: 930, height: 710}
        }, overrides || {});
        signalNames.forEach(name => { result[name] = signal(); });
        return result;
    }
    const queue = [];
    const saved = [];
    const recorder = factory((width, height, immediate) => saved.push({width, height, immediate}),
        callback => queue.push(callback));
    function flushEvents() {
        while (queue.length)
            queue.shift()();
    }

    // Unrelated applications and Ghostty's transient/dialog windows are never
    // connected, recorded, resized, or otherwise changed.
    for (const candidate of [window({resourceClass: 'org.kde.kate'}),
            window({normalWindow: false}), window({transient: true})]) {
        recorder.track(candidate);
        equal(signalNames.map(name => candidate[name].count()), [0, 0, 0, 0, 0],
            'unrelated window was connected');
    }
    flushEvents();
    equal(saved, [], 'unrelated window was recorded');

    const terminal = window();
    recorder.track(terminal);
    recorder.track(terminal);
    flushEvents();
    equal(saved.pop(), {width: 930, height: 710, immediate: false}, 'initial normal size not captured');
    equal(signalNames.map(name => terminal[name].count()), [1, 1, 1, 1, 1], 'duplicate connections');
    terminal.frameGeometry = {width: 1140.6, height: 800.3};
    terminal.frameGeometryChanged.emit();
    flushEvents();
    equal(saved.pop(), {width: 1141, height: 800, immediate: false}, 'resize not recorded');
    equal(terminal.frameGeometry, {width: 1140.6, height: 800.3}, 'recorder changed window geometry');

    // Resize notifications can arrive before state flags settle. Deferring
    // sampling prevents maximized/fullscreen geometry replacing normal size.
    terminal.frameGeometry = {width: 1920, height: 1080};
    terminal.frameGeometryChanged.emit();
    terminal.maximizeMode = 3;
    terminal.maximizedChanged.emit();
    flushEvents();
    equal(saved, [], 'maximized transition was recorded');
    for (const flags of [{maximizeMode: 1}, {maximizeMode: 2}, {fullScreen: true}, {minimized: true}]) {
        Object.assign(terminal, {maximizeMode: 0, fullScreen: false, minimized: false}, flags);
        terminal.frameGeometryChanged.emit();
        flushEvents();
        equal(saved, [], 'non-normal dimensions were recorded');
    }
    Object.assign(terminal, {maximizeMode: 0, fullScreen: false, minimized: false});
    for (const geometry of [{width: 0, height: 900}, {width: 900, height: NaN}, {width: 32768, height: 900}]) {
        terminal.frameGeometry = geometry;
        terminal.frameGeometryChanged.emit();
        flushEvents();
        equal(saved, [], 'invalid dimensions were recorded');
    }

    terminal.frameGeometry = {width: 1280, height: 840};
    terminal.frameGeometryChanged.emit();
    terminal.closed.emit();
    flushEvents();
    equal(saved.pop(), {width: 1280, height: 840, immediate: true}, 'close did not flush final normal size');
    equal(saved, [], 'deferred callback ran after window closed');
    equal(signalNames.map(name => terminal[name].count()), [0, 0, 0, 0, 0], 'closed window kept callbacks');

    // Unloading a script detaches from live windows and cancels deferred work.
    const second = window();
    recorder.track(second);
    recorder.stop();
    flushEvents();
    second.frameGeometryChanged.emit();
    equal(saved, [], 'unloaded recorder kept saving');
    equal(signalNames.map(name => second[name].count()), [0, 0, 0, 0, 0], 'unload kept callbacks');
    if (typeof console !== 'undefined')
        console.log('Ghostty persistence signal tests passed');
    return true;
})();
