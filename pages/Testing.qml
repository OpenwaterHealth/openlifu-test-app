import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

import "../components"

Rectangle {
    id: page1
    width: parent.width
    height: parent.height
    color: "#29292B"
    radius: 20
    opacity: 0.95

    // Properties for dynamic data
    property string firmwareVersion: "N/A"
    property string deviceId: "N/A"
    property real temperature1: 0.0
    property real temperature2: 0.0
    property string rgbState: "Off" // Add property for RGB state
    property string hvState: "Off" // Add property for HV state
    property string v12State: "Off" // Add property for 12V state

    function updateStates() {
        console.log("Updating all states...")
        LIFUConnector.queryHvInfo()
        LIFUConnector.queryHvTemperature()
        LIFUConnector.queryPowerStatus() // Query power status
        LIFUConnector.queryRGBState() // Query RGB state
    }

    // Run refresh logic immediately on page load if HV is already connected
    Component.onCompleted: {
        if (LIFUConnector.hvConnected) {
            console.log("Page Loaded - HV Already Connected. Fetching Info...")
            updateStates()
        }
    }

    Timer {
        id: infoTimer
        interval: 500   // Delay to ensure HV is stable before fetching info
        running: false
        onTriggered: {
            console.log("Fetching Firmware Version and Device ID...")
            updateStates()
        }
    }

    Connections {
        target: LIFUConnector

        // Handle HV Connected state
        function onHvConnectedChanged() {
            if (LIFUConnector.hvConnected) {
                infoTimer.start()          // One-time info fetch
            } else {
                console.log("HV Disconnected - Clearing Data...")
                firmwareVersion = "N/A"
                deviceId = "N/A"
                temperature1 = 0.0
                temperature2 = 0.0
                rgbState = "Off" // Reset RGB state
                // voltageState = "Off" // Reset voltage state
                pingResult.text = ""
                echoResult.text = ""
                toggleLedResult.text = ""
                rgbLedResult.text = ""

            }
        }

        // Handle device info response
        function onHvDeviceInfoReceived(fwVersion, devId) {
            firmwareVersion = fwVersion
            deviceId = devId
        }

        // Handle temperature updates
        function onTemperatureHvUpdated(temp1, temp2) {
            temperature1 = temp1
            temperature2 = temp2
        }

        // Handle voltage state updates
        function onPowerStatusReceived(v12_state, hv_state) {
            if(hv_state)
                hvState = "On"
            else
                hvState = "Off"

            if(v12_state)
                v12State = "On"
            else
                v12State = "Off"

            hvStatus.text = hvState // Update the UI with the new voltage state
            v12Status.text = v12State
        }

        function onRgbStateReceived(stateValue, stateText) {
            rgbState = stateText
            rgbLedResult.text = stateText  // Display the state as text
            rgbLedDropdown.currentIndex = stateValue  // Sync ComboBox to received state
        }

        function onTestProgressUpdated(total_frac, case_frac, total_label, case_label, status_color) {
            testProgressSection.visible = true
            testProgressSection.totalProgressValue = total_frac
            // testProgressSection.totalProgressLabelText = total_label
            testProgressSection.caseProgressValue = case_frac
            testProgressSection.caseProgressLabelText = case_label
            testProgressSection.progressColor = status_color
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Title
        Text {
            text: "LIFU PRD Testing Interface"
            font.pixelSize: 18
            font.weight: Font.Bold
            color: "white"
            horizontalAlignment: Text.AlignHCenter
            Layout.alignment: Qt.AlignHCenter
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#1E1E20"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2

            RowLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 10

                // Vertical Stack Section
                ColumnLayout {
                    Layout.fillHeight: true
                    Layout.preferredWidth: parent.width * 0.65
                    spacing: 10
                    
                    // Communication Tests Box
                    Rectangle {
                        // Layout.fillWidth: true
                        Layout.fillHeight: true
                        width: parent.width
                        height: parent.height
                        // height: 195
                        radius: 6
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        // Title at Top-Center with 5px Spacing
                        // Text {
                        //     text: "Verification Tests"
                        //     color: "#BDC3C7"
                        //     font.pixelSize: 18
                        //     anchors.top: parent.top
                        //     anchors.horizontalCenter: parent.horizontalCenter
                        //     // anchors.topMargin: 5  // 5px spacing from the top
                        // }

                        GroupBox {
                            title: "Short Duration Hardware/Software Test"
                            Layout.fillWidth: true

                            GridLayout {
                                columns: 2
                                Layout.fillWidth: true
                                columnSpacing: 12
                                rowSpacing: 8

                                Text { text: "Frequency (kHz):"; color: "white" }
                                TextField { 
                                    id: frequencyInput; 
                                    Layout.fillWidth: true; 
                                    Layout.preferredHeight: 32; 
                                    font.pixelSize: 14; 
                                    text: "400"
                                    validator: IntValidator { bottom: 100; top: 500 }
                                    onEditingFinished: {
                                        var val = parseInt(text)
                                        if (val < 100) text = "100"
                                        else if (val > 500) text = "500"
                                    }
                                }

                                Text { text: "Number of Modules:"; color: "white" }
                                ComboBox {
                                    id: numModulesDropdown
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 32
                                    model: [1, 2]
                                    
                                    onActivated: {
                                        var selectedIndex = numModulesDropdown.currentText;
                                        console.log("Selected " + selectedIndex);
                                        
                                    }
                                }

                                Button {
                                    text: "Start"
                                    Layout.fillWidth: true
                                    enabled: LIFUConnector.state === 5 || LIFUConnector.state === 1  // TX_CONNECTED or TEST_SCRIPT_READY
                                    background: Rectangle {
                                        color: "#3A3F4B"
                                        radius: 4
                                        border.color: "#BDC3C7"
                                    }
                                    onClicked: {
                                        console.log("Running thermal test...");
                                        LIFUConnector.runThermalTest(frequencyInput.text, numModulesDropdown.currentText);
                                    }
                                }

                                Button {
                                    text: "Stop"
                                    Layout.fillWidth: true
                                    enabled: LIFUConnector.state === 4  // RUNNING
                                    background: Rectangle {
                                        color: "#3A3F4B"
                                        radius: 4
                                        border.color: "#BDC3C7"
                                    }
                                    onClicked: {
                                        console.log("Stopping thermal test...");
                                        LIFUConnector._stop_thermal_test();
                                        // LIFUConnector.setAsyncMode(false)
                                    }
                                }
                            }
                        }
                    }
                }

                // Large Third Column
                Rectangle {
                    Layout.fillHeight: true
                    Layout.fillWidth: true
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 20
                        spacing: 10

                        // HV Status Indicator
                        RowLayout {
                            spacing: 8

                            Text { text: "HV"; font.pixelSize: 16; color: "#BDC3C7" }
                        
                            Rectangle {
                                width: 20
                                height: 20
                                radius: 10
                                color: LIFUConnector.hvConnected ? "green" : "red"
                                border.color: "black"
                                border.width: 1
                            }

                            Text {
                                text: LIFUConnector.hvConnected ? "Connected" : "Not Connected"
                                font.pixelSize: 16
                                color: "#BDC3C7"
                            }
                        
                        // Spacer to push the Refresh Button to the right
                            Item {
                                Layout.fillWidth: true
                            }

                            // Refresh Button
                            Rectangle {
                                width: 30
                                height: 30
                                radius: 15
                                color: enabled ? "#2C3E50" : "#7F8C8D"  // Dim when disabled
                                Layout.alignment: Qt.AlignRight  
                                enabled: LIFUConnector.hvConnected

                                // Icon Text
                                Text {
                                    text: "\u21BB"  // Unicode for the refresh icon
                                    anchors.centerIn: parent
                                    font.pixelSize: 20
                                    font.family: iconFont.name  // Use the loaded custom font
                                    color: enabled ? "white" : "#BDC3C7"  // Dim icon text when disabled
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: parent.enabled  // MouseArea also disabled when button is disabled
                                    onClicked: {
                                        console.log("Manual Refresh Triggered")
                                        LIFUConnector.queryHvInfo()
                                        LIFUConnector.queryHvTemperature()
                                    }

                                    onEntered: if (parent.enabled) parent.color = "#34495E"  // Highlight only when enabled
                                    onExited: parent.color = enabled ? "#2C3E50" : "#7F8C8D"
                                }
                            }
                        }

                        // Divider Line
                        Rectangle {
                            Layout.fillWidth: true
                            height: 2
                            color: "#3E4E6F"
                        }

                        // Display Device ID (Smaller Text)
                        RowLayout {
                            spacing: 8
                            Text { text: "Device ID:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            Text { text: deviceId; color: "#3498DB"; font.pixelSize: 14 }
                        }

                        // Display Firmware Version (Smaller Text)
                        RowLayout {
                            spacing: 8
                            Text { text: "Firmware Version:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            Text { text: firmwareVersion; color: "#2ECC71"; font.pixelSize: 14 }
                        }


                        ColumnLayout {
                            Layout.alignment: Qt.AlignHCenter 
                            spacing: 25  

                            // TEMP #1 Widget
                            TemperatureWidget {
                                id: tempWidget1
                                temperature: temperature1
                                tempName: "Temperature #1"
                                Layout.alignment: Qt.AlignHCenter
                            }

                            // TEMP #2 Widget
                            TemperatureWidget {
                                id: tempWidget2
                                temperature: temperature2
                                tempName: "Temperature #2"
                                Layout.alignment: Qt.AlignHCenter
                            }
                        }

                        // Soft Reset Button
                        // Rectangle {
                        //     Layout.fillWidth: true
                        //     height: 40
                        //     radius: 10
                        //     color: enabled ? "#E74C3C" : "#7F8C8D"  // Red when enabled, gray when disabled
                        //     enabled: LIFUConnector.hvConnected  // Enable/disable based on HV connection

                        //     Text {
                        //         text: "Soft Reset"
                        //         anchors.centerIn: parent
                        //         color: parent.enabled ? "white" : "#BDC3C7"  // White when enabled, light gray when disabled
                        //         font.pixelSize: 18
                        //         font.weight: Font.Bold
                        //     }

                        //     MouseArea {
                        //         anchors.fill: parent
                        //         enabled: parent.enabled  // Disable MouseArea when the button is disabled
                        //         onClicked: {
                        //             console.log("Soft Reset Triggered")
                        //             LIFUConnector.softResetHV()
                        //         }

                        //         onEntered: {
                        //             if (parent.enabled) {
                        //                 parent.color = "#C0392B"  // Darker red on hover (only when enabled)
                        //             }
                        //         }
                        //         onExited: {
                        //             if (parent.enabled) {
                        //                 parent.color = "#E74C3C"  // Restore original color (only when enabled)
                        //             }
                        //         }
                        //     }

                        //     Behavior on color {
                        //         ColorAnimation { duration: 200 }
                        //     }
                        // }
                    }
                }
            }
        }

        // --- Test Progress Section ---
        Rectangle {
            id: testProgressSection
            Layout.fillWidth: true
            height: progressColumn.implicitHeight + 32
            color: "#1E1E20"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2
            visible: false

            property string caseStatusColor: "#BDC3C7"
            property real totalProgressValue: 0.0
            property real caseProgressValue: 0.0
            // property string totalProgressLabelText: "Overall: waiting..."
            property string caseProgressLabelText: "Test case: waiting..."
            property string progressColor: "#BDC3C7"

            onProgressColorChanged: caseStatusColor = progressColor

            ColumnLayout {
                id: progressColumn
                anchors {
                    top: parent.top
                    left: parent.left
                    right: parent.right
                    margins: 16
                }
                spacing: 10

                Text {
                    text: "Test Progress"
                    font.pixelSize: 14
                    font.weight: Font.Bold
                    color: "white"
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    id: caseProgressLabelItem
                    text: testProgressSection.caseProgressLabelText
                    color: "#BDC3C7"
                    font.pixelSize: 12
                    Layout.fillWidth: true
                }

                ProgressBar {
                    id: caseProgressBar
                    Layout.fillWidth: true
                    from: 0.0
                    to: 1.0
                    value: testProgressSection.caseProgressValue

                    background: Rectangle {
                        implicitHeight: 14
                        color: "#2A2F3B"
                        radius: 7
                        border.color: "#3E4E6F"
                    }
                    contentItem: Item {
                        implicitHeight: 14
                        Rectangle {
                            width: caseProgressBar.visualPosition * parent.width
                            height: parent.height
                            radius: 7
                            color: testProgressSection.caseStatusColor
                        }
                    }
                }

                // Text {
                //     id: totalProgressLabelItem
                //     text: testProgressSection.totalProgressLabelText
                //     color: "#BDC3C7"
                //     font.pixelSize: 12
                //     Layout.fillWidth: true
                // }

                // ProgressBar {
                //     id: totalProgressBar
                //     Layout.fillWidth: true
                //     from: 0.0
                //     to: 1.0
                //     value: testProgressSection.totalProgressValue

                //     background: Rectangle {
                //         implicitHeight: 14
                //         color: "#2A2F3B"
                //         radius: 7
                //         border.color: "#3E4E6F"
                //     }
                //     contentItem: Item {
                //         implicitHeight: 14
                //         Rectangle {
                //             width: totalProgressBar.visualPosition * parent.width
                //             height: parent.height
                //             radius: 7
                //             color: totalProgressBar.value >= 1.0 ? "#2ECC71" : "#4A90E2"
                //         }
                //     }
                // }
            }
        }

        FontLoader {
            id: iconFont
            source: "../assets/fonts/keenicons-outline.ttf"
        }
    }
}
