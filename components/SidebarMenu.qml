import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

Rectangle {
    id: sidebarMenu
    width: 60
    height: parent.height
    radius: 0
    color: "#2C3E50" // Dark sidebar background

    // Current active button index. The parent owns this value (binds it
    // from its own active-tab state); we just emit buttonClicked and let
    // the parent decide whether to honor the click. Don't reassign this
    // property internally or the binding will break.
    property int activeButtonIndex: 0

    // Signal to handle button clicks
    signal buttonClicked(int index)

    // Reusable function for button handling
    function handleButtonClick(index) {
        buttonClicked(index);
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 20
        Layout.alignment: Qt.AlignVCenter

        Repeater {
            model: (typeof appTabs !== "undefined" && appTabs) ? appTabs : []

            IconButton {
                buttonIcon: modelData.icon
                buttonText: modelData.label
                enabled: true
                Layout.alignment: Qt.AlignHCenter
                backgroundColor: sidebarMenu.activeButtonIndex === index ? "white" : "transparent"
                iconColor: sidebarMenu.activeButtonIndex === index ? "#2C3E50" : "#BDC3C7"
                onClicked: sidebarMenu.handleButtonClick(index)
            }
        }
    }
}
