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
            titleText: "OpenLIFU Test App"
            logoSource: "../assets/images/OpenwaterLogo.png" // Correct relative path
            appVerText: appVersion
            sdkVerText: LIFUConnector.sdkVersion
            safetyBypassActive: LIFUConnector.safetyBypassEnabled
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

            // Sidebar Menu.
            SidebarMenu {
                Layout.alignment: Qt.AlignLeft
                Layout.fillHeight: true
                color: "#1C1C1E" // Dark sidebar background
                visible: (typeof appTabs !== "undefined" && appTabs && appTabs.length > 1)

                // Keep highlighted tab in sync with whichever tab is
                // actually showing (so blocked navigations don't visually
                // jump the highlight ahead of the page).
                activeButtonIndex: window.activeMenu

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

                // StackLayout (instead of a single dynamic Loader) so each
                // tab's page is instantiated once and its state survives
                // tab switches. This makes the sonication progress UI on
                // Controller persist when the user pops over to Transmitter or
                // Console and returns.
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: activeMenu

                    Repeater {
                        model: (typeof appTabs !== "undefined" && appTabs) ? appTabs : []

                        Loader {
                            active: true
                            source: modelData.page
                        }
                    }
                }
            }
        }
    }

    // JavaScript function to handle sidebar button clicks
    function handleSidebarClick(index) {
        if (!appTabs || index < 0 || index >= appTabs.length) return
        var targetId = appTabs[index].id
        var currentId = (activeMenu >= 0 && activeMenu < appTabs.length)
                        ? appTabs[activeMenu].id : ""

        // Block switching to Settings while sonication is running. The
        // user must Stop first.
        if (targetId === "settings" && LIFUConnector.state === 3) {
            console.log("Cannot switch to Settings while sonication is running.")
            return
        }

        // Switching from the Controller (sonication) page to Settings while
        // configured (or having been stopped/finished): force a Reset so
        // the user has to re-Program/Configure before the next run, and
        // make sure HV is dropped.
        if (targetId === "settings"
            && currentId === "controller"
            && LIFUConnector.state >= 2
            && LIFUConnector.state !== 3) {
            if (LIFUConnector.hvEnableMode === 1) {
                LIFUConnector.setHvEnableMode(2)
            }
            LIFUConnector.reset_configuration()
        }

        activeMenu = index
        console.log("Tab selected:", targetId, "(index", index + ")")
    }

    // Global device-error popup.  Shown whenever LIFUConnector.deviceError is
    // emitted, e.g. when an SDK call times out or otherwise returns a sentinel
    // failure value instead of raising an exception.
    Dialog {
        id: deviceErrorDialog
        modal: true
        focus: true
        title: "Device Error"
        width: 480
        x: (window.width - width) / 2
        y: (window.height - height) / 2

        property string errorTitle: ""
        property string errorMessage: ""

        background: Rectangle {
            color: "#1E1E20"
            border.color: "#7A2E2E"
            border.width: 2
            radius: 8
        }

        contentItem: ColumnLayout {
            spacing: 10

            Text {
                text: deviceErrorDialog.errorTitle
                color: "#F5B5B5"
                font.pixelSize: 15
                font.bold: true
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }

            Text {
                text: deviceErrorDialog.errorMessage
                color: "#FFD3D3"
                font.pixelSize: 13
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }

        footer: RowLayout {
            spacing: 10

            Item { Layout.fillWidth: true }

            Button {
                text: "OK"
                onClicked: deviceErrorDialog.close()
            }

            Item { Layout.preferredWidth: 10 }
        }
    }

    Connections {
        target: LIFUConnector

        function onDeviceError(title, message) {
            console.error("Device error [" + title + "]: " + message)
            deviceErrorDialog.errorTitle = title
            deviceErrorDialog.errorMessage = message
            if (!deviceErrorDialog.visible) {
                deviceErrorDialog.open()
            }
        }
    }

}
