// SPDX-License-Identifier: Apache-2.0
import QtQuick
import QtCore
import org.kde.kwin 3.0
import "../code/persistence.js" as Persistence

Item {
    id: root
    property var recorder: null
    property var pendingSize: null

    Settings {
        id: saved
        location: StandardPaths.writableLocation(StandardPaths.GenericStateLocation)
                  + "/monolith/ghostty-window-size.ini"
        category: "Window"
    }

    // Coalesce drag events; close and plugin unload flush any pending size.
    Timer {
        id: flushTimer
        interval: 200
        onTriggered: root.flush()
    }

    function flush() {
        if (pendingSize === null)
            return;
        saved.setValue("width", pendingSize.width);
        saved.setValue("height", pendingSize.height);
        saved.sync();
        pendingSize = null;
    }

    function record(width, height, immediate) {
        pendingSize = { width: width, height: height };
        if (immediate)
            flush();
        else
            flushTimer.restart();
    }

    Connections {
        target: Workspace
        function onWindowAdded(window) {
            if (root.recorder !== null)
                root.recorder.track(window);
        }
    }

    Component.onCompleted: {
        recorder = Persistence.create(record, function(callback) { Qt.callLater(callback); });
        for (var index = 0; index < Workspace.windows.length; index++)
            recorder.track(Workspace.windows[index]);
    }

    Component.onDestruction: {
        if (recorder !== null)
            recorder.stop();
        flush();
    }
}
