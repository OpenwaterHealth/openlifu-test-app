import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

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

    // Drag functionality
    MouseArea {
        id: headerMouseArea
        anchors.fill: parent
        cursorShape: Qt.SizeAllCursor
        onPressed: function(mouse) {
            if (mouse.button === Qt.LeftButton) {
                window.startSystemMove(); // Allow window dragging
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
