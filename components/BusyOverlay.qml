import QtQuick
import QtQuick.Controls

// Semi-transparent full-area overlay shown while a slow QML-blocking
// action is running. The page sets ``visible: true`` right before the
// action and ``false`` immediately after. Because Qt processes one event
// loop iteration between the property change and the (synchronous,
// Qt.callLater-deferred) action, the overlay is guaranteed to paint
// before the GUI thread is blocked -- giving the user a visual "the app
// is working" cue even when nothing else on screen can update.
//
// The overlay also captures pointer events so background controls are
// effectively disabled while busy.
//
// NOTE: BusyIndicator's spin animation runs on the GUI thread, so it
// will freeze mid-rotation if the action does not yield back. That's OK
// -- the appearance of the overlay itself is the indicator we care
// about; the spinner is bonus motion when work is asynchronous.
Item {
    id: root
    anchors.fill: parent

    // Public knobs. Defaults are reasonable for a "Working..." pause.
    property string text: "Working\u2026"
    property color backdropColor: "#000000"
    property real backdropOpacity: 0.55

    z: 1000  // sit above almost anything on the page
    visible: false

    // Block clicks on whatever is behind us.
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
        onWheel: function (wheel) { wheel.accepted = true }
        // Empty onClicked / onPressed swallows events without effect.
        onClicked: function (mouse) { mouse.accepted = true }
    }

    Rectangle {
        anchors.fill: parent
        color: root.backdropColor
        opacity: root.backdropOpacity
    }

    Rectangle {
        anchors.centerIn: parent
        width: 200
        height: 110
        radius: 10
        color: "#1E1E20"
        border.color: "#3E4E6F"
        border.width: 2

        Column {
            anchors.centerIn: parent
            spacing: 10

            BusyIndicator {
                anchors.horizontalCenter: parent.horizontalCenter
                running: root.visible
                width: 48
                height: 48
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.text
                color: "white"
                font.pixelSize: 14
                font.weight: Font.Medium
            }
        }
    }
}
