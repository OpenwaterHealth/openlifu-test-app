import QtQuick
import QtQuick.Controls

// Subtle "working" cue shown while a slow QML-blocking action runs.
// The page sets ``visible: true`` right before the action and ``false``
// immediately after. Because Qt processes one event-loop iteration
// between the property change and the (Timer-deferred) action, the
// overlay is guaranteed to paint before the GUI thread is blocked --
// giving the user a small visual indication even when nothing else on
// screen can update.
//
// Pointer events are still captured so accidental double-clicks during
// the busy window don't queue up; but the visual treatment is
// deliberately understated (no full-screen dim, no "Working..." card)
// so brief operations like Start/Stop don't feel jarring.
//
// NOTE: BusyIndicator's spin animation runs on the GUI thread, so it
// will freeze mid-rotation if the action does not yield back. That's
// OK -- the appearance of the spinner itself is the indicator we care
// about; the rotation is bonus motion when work is asynchronous.
Item {
    id: root
    anchors.fill: parent

    // Public knobs. Set ``showBackdrop`` true for the old heavier look.
    property bool showBackdrop: false
    property color backdropColor: "#000000"
    property real backdropOpacity: 0.18
    property int spinnerSize: 36

    z: 1000  // sit above almost anything on the page
    visible: false

    // Block clicks on whatever is behind us so stray input during the
    // brief busy window doesn't queue up.
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
        onWheel: function (wheel) { wheel.accepted = true }
        onClicked: function (mouse) { mouse.accepted = true }
    }

    Rectangle {
        anchors.fill: parent
        visible: root.showBackdrop
        color: root.backdropColor
        opacity: root.backdropOpacity
    }

    BusyIndicator {
        anchors.centerIn: parent
        running: root.visible
        width: root.spinnerSize
        height: root.spinnerSize
        opacity: 0.85
    }
}
