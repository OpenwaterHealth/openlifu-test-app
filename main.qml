// Copyright (C) 2021 The Qt Company Ltd.
// SPDX-License-Identifier: LicenseRef-Qt-Commercial OR GPL-3.0-only
import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

import "components"


Window {

    id: window
    visible: true
    width: 1200
    height: 800
    flags: Qt.FramelessWindowHint | Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint // Ensure it appears in the taskbar
    color: "transparent" // Make the window background transparent to apply rounded corners

    // State to track which content to show
    property int activeMenu: 0

    Rectangle {
        anchors.fill: parent
        color: "#1C1C1E" // Main background color
        radius: 20 // Rounded corners
        border.color: "transparent"

        // Properties
        property int activeButtonIndex: 0 // Define activeButtonIndex here

        // Header Section (with drag functionality)
        WindowMenu {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right

            // Set title and logo dynamically
            titleText: "Open-LIFU Engineering App"
            logoSource: "../assets/images/OpenwaterLogo.png" // Correct relative path
            appVerText: "v" + appVersion
            sdkVerText: "v" + LIFUConnector.sdkVersion
        }

        // Layout for Sidebar and Main Content
        RowLayout {
            anchors.fill: parent
            anchors.topMargin: 65
            anchors.rightMargin: 15
            anchors.bottomMargin: 15
            anchors.leftMargin: 15
            spacing: 20
            Layout.fillHeight: true

            // Sidebar Menu
            SidebarMenu {
                Layout.alignment: Qt.AlignLeft
                Layout.fillHeight: true
                color: "#1C1C1E" // Dark sidebar background

                // Explicitly pass the signal parameter to the function
                onButtonClicked: {
                    handleSidebarClick(arguments[0]);
                }
            }

            // Main Content
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 20

                Loader {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    source: activeMenu === 0 ? "pages/Demo.qml"
                        : activeMenu === 1 ? "pages/Transmitter.qml"
                        : activeMenu === 2 ? "pages/Console.qml"
                        : activeMenu === 3 ? "pages/Testing.qml"
                        : "pages/Settings.qml"

                }
            }
        }
    }

    // JavaScript function to handle sidebar button clicks
    function handleSidebarClick(index) {
        activeMenu = index; // Update the activeMenu property
        console.log("Button clicked with index:", index);
    }

}
