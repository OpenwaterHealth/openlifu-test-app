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
    property string activeTestKey: "short"

    property real shortOverallProgress: 0.0
    property real shortCaseProgress: 0.0
    property string shortTotalLabel: "Overall: waiting..."
    property string shortCaseLabel: "Status: idle"
    property string shortStatusColor: "#BDC3C7"
    property string shortLogPath: ""

    property real longOverallProgress: 0.0
    property real longCaseProgress: 0.0
    property string longTotalLabel: "Overall: waiting..."
    property string longCaseLabel: "Status: idle"
    property string longStatusColor: "#BDC3C7"
    property string longLogPath: ""

    property real indefiniteOverallProgress: 0.0
    property real indefiniteCaseProgress: 0.0
    property string indefiniteTotalLabel: "Overall: waiting..."
    property string indefiniteCaseLabel: "Status: idle"
    property string indefiniteStatusColor: "#BDC3C7"
    property string indefiniteLogPath: ""

    property real voltageOverallProgress: 0.0
    property real voltageCaseProgress: 0.0
    property string voltageTotalLabel: "Overall: waiting..."
    property string voltageCaseLabel: "Status: idle"
    property string voltageStatusColor: "#BDC3C7"
    property string voltageLogPath: ""

    function canStartTest() {
        return (LIFUConnector.state === 5 || LIFUConnector.state === 1 || LIFUConnector.state === 2 || LIFUConnector.state === 3) && LIFUConnector.state !== 4
    }

    function applyProgressToActiveTest(total_frac, case_frac, total_label, case_label, status_color, log_file_path) {
        if (activeTestKey === "short") {
            shortOverallProgress = total_frac
            shortCaseProgress = case_frac
            shortTotalLabel = total_label
            shortCaseLabel = case_label
            shortStatusColor = status_color
            shortLogPath = log_file_path
        } else if (activeTestKey === "long") {
            longOverallProgress = total_frac
            longCaseProgress = case_frac
            longTotalLabel = total_label
            longCaseLabel = case_label
            longStatusColor = status_color
            longLogPath = log_file_path
        } else if (activeTestKey === "indefinite") {
            indefiniteOverallProgress = total_frac
            indefiniteCaseProgress = case_frac
            indefiniteTotalLabel = total_label
            indefiniteCaseLabel = case_label
            indefiniteStatusColor = status_color
            indefiniteLogPath = log_file_path
        } else if (activeTestKey === "voltage") {
            voltageOverallProgress = total_frac
            voltageCaseProgress = case_frac
            voltageTotalLabel = total_label
            voltageCaseLabel = case_label
            voltageStatusColor = status_color
            voltageLogPath = log_file_path
        }
    }

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

        function onTestProgressUpdated(total_frac, case_frac, total_label, case_label, status_color, log_file_path) {
            applyProgressToActiveTest(total_frac, case_frac, total_label, case_label, status_color, log_file_path)
            testProgressSection.visible = true
            testProgressSection.totalProgressValue = total_frac
            testProgressSection.totalProgressLabelText = total_label
            testProgressSection.caseProgressValue = case_frac
            testProgressSection.caseProgressLabelText = case_label
            testProgressSection.progressColor = status_color
            testProgressSection.logFilePath = log_file_path
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
            Layout.maximumHeight: 500  // Prevent main box from taking all space
            color: "#1E1E20"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2

            RowLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 14

                // Verification Test Section
                ColumnLayout {
                    Layout.fillHeight: true
                    Layout.preferredWidth: parent.width * 0.60
                    spacing: 10

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 84
                        radius: 10
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        GridLayout {
                            anchors.fill: parent
                            anchors.margins: 10
                            columns: 2
                            columnSpacing: 10
                            rowSpacing: 8

                            Text { text: "Frequency (kHz):"; color: "white" }
                            TextField {
                                id: frequencyInput
                                Layout.fillWidth: true
                                Layout.preferredHeight: 26
                                font.pixelSize: 13
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
                                Layout.preferredHeight: 26
                                model: [1, 2]
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        columns: 2
                        columnSpacing: 10
                        rowSpacing: 10

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            radius: 10
                            color: "#1E1E20"
                            border.color: "#3E4E6F"
                            border.width: 2

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 6

                                Text { text: "Short Duration Verification"; color: "white"; font.bold: true; font.pixelSize: 13 }
                                Text { text: shortCaseLabel; color: "#BDC3C7"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                                Text { text: shortLogPath ? "Log: " + shortLogPath : "Log: --"; color: "#999999"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideLeft }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Button {
                                        text: "Start"
                                        Layout.fillWidth: true
                                        enabled: canStartTest()
                                        onClicked: {
                                            activeTestKey = "short"
                                            LIFUConnector.runThermalTest(frequencyInput.text, numModulesDropdown.currentText)
                                        }
                                    }
                                    Button {
                                        text: "Stop"
                                        Layout.fillWidth: true
                                        enabled: LIFUConnector.state === 4 && activeTestKey === "short"
                                        onClicked: LIFUConnector.stopVerificationTest()
                                    }
                                }
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            radius: 10
                            color: "#1E1E20"
                            border.color: "#3E4E6F"
                            border.width: 2

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 6

                                Text { text: "Long Verification"; color: "white"; font.bold: true; font.pixelSize: 13 }
                                Text { text: longTotalLabel; color: "#BDC3C7"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                                Text { text: longCaseLabel; color: "#BDC3C7"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                                Text { text: longLogPath ? "Log: " + longLogPath : "Log: --"; color: "#999999"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideLeft }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Button {
                                        text: "Start"
                                        Layout.fillWidth: true
                                        enabled: canStartTest()
                                        onClicked: {
                                            activeTestKey = "long"
                                            LIFUConnector.runLongVerificationTest(frequencyInput.text, numModulesDropdown.currentText)
                                        }
                                    }
                                    Button {
                                        text: "Stop"
                                        Layout.fillWidth: true
                                        enabled: LIFUConnector.state === 4 && activeTestKey === "long"
                                        onClicked: LIFUConnector.stopVerificationTest()
                                    }
                                }
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            radius: 10
                            color: "#1E1E20"
                            border.color: "#3E4E6F"
                            border.width: 2

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 6

                                Text { text: "Run Indefinitely"; color: "white"; font.bold: true; font.pixelSize: 13 }
                                Text { text: indefiniteCaseLabel; color: "#BDC3C7"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                                Text { text: indefiniteLogPath ? "Log: " + indefiniteLogPath : "Log: --"; color: "#999999"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideLeft }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Button {
                                        text: "Start"
                                        Layout.fillWidth: true
                                        enabled: canStartTest()
                                        onClicked: {
                                            activeTestKey = "indefinite"
                                            LIFUConnector.runIndefiniteTest(frequencyInput.text, numModulesDropdown.currentText)
                                        }
                                    }
                                    Button {
                                        text: "Stop"
                                        Layout.fillWidth: true
                                        enabled: LIFUConnector.state === 4 && activeTestKey === "indefinite"
                                        onClicked: LIFUConnector.stopVerificationTest()
                                    }
                                }
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            radius: 10
                            color: "#1E1E20"
                            border.color: "#3E4E6F"
                            border.width: 2

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 6

                                Text { text: "Voltage Accuracy"; color: "white"; font.bold: true; font.pixelSize: 13 }
                                Text { text: voltageCaseLabel; color: "#BDC3C7"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideRight }
                                Text { text: voltageLogPath ? "Log: " + voltageLogPath : "Log: --"; color: "#999999"; font.pixelSize: 10; Layout.fillWidth: true; elide: Text.ElideLeft }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Button {
                                        text: "Start"
                                        Layout.fillWidth: true
                                        enabled: canStartTest()
                                        onClicked: {
                                            activeTestKey = "voltage"
                                            LIFUConnector.runVoltageAccuracyTest(frequencyInput.text, numModulesDropdown.currentText)
                                        }
                                    }
                                    Button {
                                        text: "Stop"
                                        Layout.fillWidth: true
                                        enabled: LIFUConnector.state === 4 && activeTestKey === "voltage"
                                        onClicked: LIFUConnector.stopVerificationTest()
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
                    Layout.minimumWidth: 420
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
                            Layout.fillWidth: true
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
                            Layout.fillWidth: true
                            spacing: 8
                            Text { text: "Device ID:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            Text { text: deviceId; color: "#3498DB"; font.pixelSize: 14 }
                        }

                        // Display Firmware Version (Smaller Text)
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Text { text: "Firmware Version:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            Text { text: firmwareVersion; color: "#2ECC71"; font.pixelSize: 14 }
                        }

                        Item {
                            Layout.fillHeight: true
                        }


                        RowLayout {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignHCenter
                            spacing: 16

                            Item {
                                Layout.fillWidth: true
                            }

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

                            Item {
                                Layout.fillWidth: true
                            }
                        }

                        Item {
                            Layout.fillHeight: true
                        }
                    }
                }
            }
        }

        // --- Test Progress Section ---
        Rectangle {
            id: testProgressSection
            Layout.fillWidth: true
            Layout.preferredHeight: 176
            color: "#1E1E20"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2
            visible: true
            clip: true

            property string caseStatusColor: "#BDC3C7"
            property real totalProgressValue: 0.0
            property real caseProgressValue: 0.0
            property string totalProgressLabelText: "Overall: waiting..."
            property string caseProgressLabelText: ""
            property string progressColor: "#BDC3C7"
            property string logFilePath: ""

            onProgressColorChanged: caseStatusColor = progressColor

            ColumnLayout {
                id: progressColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6

                Text {
                    text: "Test Progress - " + activeTestKey
                    font.pixelSize: 14
                    font.weight: Font.Bold
                    color: "white"
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    id: totalProgressLabelItem
                    text: testProgressSection.totalProgressLabelText
                    color: "#BDC3C7"
                    font.pixelSize: 11
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                ProgressBar {
                    id: totalProgressBar
                    Layout.fillWidth: true
                    from: 0.0
                    to: 1.0
                    value: testProgressSection.totalProgressValue

                    background: Rectangle {
                        implicitHeight: 10
                        color: "#2A2F3B"
                        radius: 6
                        border.color: "#3E4E6F"
                    }
                    contentItem: Item {
                        implicitHeight: 10
                        Rectangle {
                            width: totalProgressBar.visualPosition * parent.width
                            height: parent.height
                            radius: 6
                            color: "#5DADE2"
                        }
                    }
                }

                Text {
                    id: caseProgressLabelItem
                    text: testProgressSection.caseProgressLabelText
                    color: "#BDC3C7"
                    font.pixelSize: 11
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }

                Text {
                    id: logFilePathItem
                    text: testProgressSection.logFilePath ? "Log: " + testProgressSection.logFilePath : ""
                    color: "#999999"
                    font.pixelSize: 11
                    Layout.fillWidth: true
                    elide: Text.ElideLeft
                    // selectByMouse: true
                }

                ProgressBar {
                    id: caseProgressBar
                    Layout.fillWidth: true
                    from: 0.0
                    to: 1.0
                    value: testProgressSection.caseProgressValue

                    background: Rectangle {
                        implicitHeight: 10
                        color: "#2A2F3B"
                        radius: 7
                        border.color: "#3E4E6F"
                    }
                    contentItem: Item {
                        implicitHeight: 10
                        Rectangle {
                            width: caseProgressBar.visualPosition * parent.width
                            height: parent.height
                            radius: 7
                            color: testProgressSection.caseStatusColor
                        }
                    }
                }
            }
        }

        FontLoader {
            id: iconFont
            source: "../assets/fonts/keenicons-outline.ttf"
        }
    }
}
