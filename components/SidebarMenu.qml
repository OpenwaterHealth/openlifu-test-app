import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

Rectangle {
    id: sidebarMenu
    width: 60
    height: parent.height
    radius: 0
    color: "#2C3E50" // Dark sidebar background

    // Current active button index
    property int activeButtonIndex: 0

    // Signal to handle button clicks
    signal buttonClicked(int index)

    // Reusable function for button handling
    function handleButtonClick(index) {
        activeButtonIndex = index;
        buttonClicked(index);
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 20
        Layout.alignment: Qt.AlignVCenter

        // Demo Button
        IconButton {
            buttonIcon: "\ueb34"
            buttonText: "Demo"
            Layout.alignment: Qt.AlignHCenter
            backgroundColor: sidebarMenu.activeButtonIndex === 0 ? "white" : "transparent"
            iconColor: sidebarMenu.activeButtonIndex === 0 ? "#2C3E50" : "#BDC3C7"
            onClicked: {
                sidebarMenu.handleButtonClick(0); // Call the global function
            }
        }

        // Test Button
        IconButton {
            buttonIcon: "\ueab9"
            buttonText: "Transmitter"
            enabled: true
            Layout.alignment: Qt.AlignHCenter
            backgroundColor: sidebarMenu.activeButtonIndex === 1 ? "white" : "transparent"
            iconColor: sidebarMenu.activeButtonIndex === 1 ? "#2C3E50" : "#BDC3C7"
            onClicked: {
                sidebarMenu.handleButtonClick(1); // Call the global function
            }
        }

        // Console Button
        IconButton {
            buttonIcon: "\ueaae"
            buttonText: "Console"
            enabled: true
            Layout.alignment: Qt.AlignHCenter
            backgroundColor: sidebarMenu.activeButtonIndex === 2 ? "white" : "transparent"
            iconColor: sidebarMenu.activeButtonIndex === 2 ? "#2C3E50" : "#BDC3C7"
            onClicked: {
                sidebarMenu.handleButtonClick(2); // Call the global function
            }
        }

        
        IconButton {
            buttonIcon: "\ueb34"
            buttonText: "Testing"
            Layout.alignment: Qt.AlignHCenter
            backgroundColor: sidebarMenu.activeButtonIndex === 3 ? "white" : "transparent"
            iconColor: sidebarMenu.activeButtonIndex === 0 ? "#2C3E50" : "#BDC3C7"
            onClicked: {
                sidebarMenu.handleButtonClick(3); // Call the global function
            }
        }

        // Settings Button
        IconButton {
            buttonIcon: "\ueabf"
            buttonText: "Settings"
            enabled: true
            Layout.alignment: Qt.AlignHCenter
            backgroundColor: sidebarMenu.activeButtonIndex === 4 ? "white" : "transparent"
            iconColor: sidebarMenu.activeButtonIndex === 3 ? "#2C3E50" : "#BDC3C7"
            onClicked: {
                sidebarMenu.handleButtonClick(4); // Call the global function
            }
        }
    }
}
