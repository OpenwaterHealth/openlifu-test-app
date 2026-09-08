import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import QtQuick.Window 6.0

Rectangle {
    id: windowMenu
    width: parent.width
    height: 60
    color: "#1E1E20" // Header background color
    radius: 20

    // Properties to configure the title and logo
    property string titleText: "Default Title" // Default title
    property string logoSource: "" // Default to no logo
    property string appVerText: "v0.0.0" // Default
    property string sdkVerText: "v0.0.0" // Default
    // Bound by main.qml, like the other inputs above.
    property bool safetyBypassActive: false

    // Drag functionality
    //
    // Use Window.startSystemMove() instead of manually adjusting window.x/y in
    // onPositionChanged. The manual approach feeds back on itself: as the
    // window is repositioned, this MouseArea (anchored inside the window)
    // moves with it, so the next mouse event reports new local coordinates
    // and the computed delta no longer reflects actual cursor motion. The
    // window then runs away across the screen until Windows clamps the
    // geometry at INT16_MIN (-32768), producing the
    //   "QWindowsWindow::setGeometry: Unable to set geometry ..."
    // warnings. startSystemMove() hands the drag to the OS, which tracks
    // the cursor in screen coordinates and avoids the feedback loop.
    MouseArea {
        id: headerMouseArea
        anchors.fill: parent
        cursorShape: Qt.SizeAllCursor

        onPressed: function(mouse) {
            if (mouse.button === Qt.LeftButton && window.visibility !== Window.Maximized) {
                window.startSystemMove()
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 10

        // Logo
        Rectangle {
            width: 185
            height: 42
            color: "transparent" // No background color
            radius: 6

            Image {
                source: windowMenu.logoSource // Use the configurable logo source
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                smooth: true
                visible: windowMenu.logoSource !== "" // Show only if a logo is provided
            }
        }

        // In the header, not on a page: the override is device-global, so
        // the warning follows the operator across every tab.
        Rectangle {
            id: safetyBypassBadge
            objectName: "safetyBypassBadge"
            visible: windowMenu.safetyBypassActive
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 30
            implicitHeight: 30
            radius: 5
            color: "#7A1F1F"
            border.color: "#E74C3C"
            border.width: 2

            Text {
                anchors.centerIn: parent
                text: "!"
                color: "#FF6B6B"
                font.pixelSize: 20
                font.bold: true
            }

            MouseArea {
                id: safetyBypassBadgeArea
                objectName: "safetyBypassBadgeArea"
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.NoButton
                ToolTip.visible: containsMouse
                ToolTip.delay: 200
                ToolTip.text: "Safety limits are BYPASSED.\n\n"
                              + "Configure skips the SDK's duty-cycle, voltage and "
                              + "sequence-duration checks, so the array can be driven at up "
                              + "to 100% duty cycle. Bench testing only.\n\n"
                              + "Turn this off under Settings → Engineering Overrides."
            }
        }

        // Spacer before title
        Item {
            Layout.fillWidth: true
        }

        // Title and Version Container
        RowLayout {
            spacing: 8
            Layout.alignment: Qt.AlignHCenter

            // Title
            Text {
                text: windowMenu.titleText // Use the configurable title text
                color: "white"
                font.pixelSize: 24
                font.weight: Font.Bold // Make the text bold
                verticalAlignment: Text.AlignVCenter
                horizontalAlignment: Text.AlignHCenter
            }

            // Version Info (App + SDK stacked vertically)
            ColumnLayout {
                spacing: 2
                Layout.alignment: Qt.AlignVCenter

                // App Version
                RowLayout {
                    spacing: 0
                    Layout.alignment: Qt.AlignLeft
                    
                    Text {
                        text: "APP: v"
                        color: "#AAAAAA"
                        font.pixelSize: 12
                        font.weight: Font.Medium
                    }
                    
                    TextField {
                        text: windowMenu.appVerText
                        color: "#AAAAAA"
                        font.pixelSize: 12
                        font.weight: Font.Medium
                        readOnly: true
                        selectByMouse: true
                        leftPadding: 0
                        rightPadding: 0
                        topPadding: 0
                        bottomPadding: 0
                        background: Rectangle {
                            color: "transparent"
                            border.color: "transparent"
                        }
                    }
                }

                // SDK Version
                RowLayout {
                    spacing: 0
                    Layout.alignment: Qt.AlignLeft
                    
                    Text {
                        text: "SDK: v"
                        color: "#AAAAAA"
                        font.pixelSize: 12
                        font.weight: Font.Medium
                    }
                    
                    TextField {
                        text: windowMenu.sdkVerText
                        color: "#AAAAAA"
                        font.pixelSize: 12
                        font.weight: Font.Medium
                        readOnly: true
                        selectByMouse: true
                        leftPadding: 0
                        rightPadding: 0
                        topPadding: 0
                        bottomPadding: 0
                        background: Rectangle {
                            color: "transparent"
                            border.color: "transparent"
                        }
                    }
                }
            }
        }
        
        // Spacer after title
        Item {
            Layout.fillWidth: true
        }

        // Window control buttons
        RowLayout {
            spacing: 10
            Layout.alignment: Qt.AlignRight

            // Minimize Button
            IconWindowButton {
                iconType: 1
                Layout.alignment: Qt.AlignHCenter
                onClicked: {
                    window.showMinimized(); // Minimize the window
                }
            }
/*
            // Maximize/Restore Button
            IconWindowButton {
                buttonIcon: "\ueb18" // Maximize/restore icon
                Layout.alignment: Qt.AlignHCenter
                onClicked: {
                    if (window.visibility === Window.Maximized) {
                        window.showNormal(); // Restore to normal size
                    } else {
                        window.showMaximized(); // Maximize the window
                    }
                }
            }
*/
            // Exit Button
            IconWindowButton {
                iconType: 2
                Layout.alignment: Qt.AlignHCenter
                onClicked: {
                    Qt.quit(); // Close the application
                }
            }
        }
    }
}
