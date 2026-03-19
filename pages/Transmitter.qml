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
    property var modules: []

    function updateStates() {
        console.log("Updating all states...")
        LIFUConnector.queryNumModules()
        LIFUConnector.queryTxInfo()
        LIFUConnector.queryTxTemperature()
        LIFUConnector.queryTriggerInfo()
    }

    // Run refresh logic immediately on page load if TX is already connected
    Component.onCompleted: {
        if (LIFUConnector.txConnected) {
            console.log("Page Loaded - TX Already Connected. Fetching Info...")
            updateStates()
        }
    }

    Timer {
        id: infoTimer
        interval: 1500   // Delay to ensure TX is stable before fetching info
        running: false
        onTriggered: {
            console.log("Fetching Firmware Version and Device ID...")
            updateStates()
        }
    }

    Connections {
        target: LIFUConnector

        // Handle TX Connected state
        onTxConnectedChanged: {
        if (LIFUConnector.txConnected) {
            infoTimer.start()  // One-time info fetch
        } else {
            console.log("TX Disconnected - Clearing Data...")
            modules = []

            pingResult.text = ""
            echoResult.text = ""
            toggleLedResult.text = ""
        }
        }

        // Handle device info response
        onTxDeviceInfoReceived: (modulesList) => {
            // Build modules array, preserving existing temperature data
            modules = modulesList.map(function(m, i) {
                let existing = modules[i]
                return {
                    firmwareVersion: m.firmwareVersion,
                    deviceId: m.deviceId,
                    tx_temperature: existing ? existing.tx_temperature : 0.0,
                    amb_temperature: existing ? existing.amb_temperature : 0.0
                }
            })
        }

        // Handle temperature updates
        onTemperatureTxUpdated: (module, tx_temp, amb_temp) => {
            if (module < modules.length) {
                let updated = modules.slice()
                updated[module] = Object.assign({}, updated[module], {
                    tx_temperature: tx_temp,
                    amb_temperature: amb_temp
                })
                modules = updated
            }
        }

        onTriggerStateChanged: (state) => {
            triggerStatus.text = state ? "On" : "Off";
            triggerStatus.color = state ? "green" : "red";
        }

        onTxConfigStateChanged: (state) => {
            txconfigStatus.text = state ? "Configured" : "NOT Configured";
            txconfigStatus.color = state ? "green" : "red";
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Title
        Text {
            text: "LIFU Transmitter Unit Tests"
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
                    // Layout.preferredWidth: parent.width * 0.65
                    Layout.preferredWidth: 1
                    spacing: 10
                    
                    // Communication Tests Box
                    Rectangle {
                        width: 500
                        // Layout.preferredWidth: 2
                        height: 195
                        radius: 6
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        // Title at Top-Center with 5px Spacing
                        Text {
                            text: "Communication Tests"
                            color: "#BDC3C7"
                            font.pixelSize: 18
                            anchors.top: parent.top
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.topMargin: 5  // 5px spacing from the top
                        }

                        // Content for comms tests
                        GridLayout {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.leftMargin: 20
                            anchors.rightMargin: 20   
                            anchors.topMargin: 60    
                            columns: 5
                            rowSpacing: 10
                            columnSpacing: 10

                            // Row 1
                            // Ping Button and Result
                            Button {
                                id: pingButton
                                text: "Ping"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 50
                                hoverEnabled: true  // Enable hover detection
                                enabled: LIFUConnector.txConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: pingButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    if(LIFUConnector.sendPingCommand("TX", moduleTabBar.currentIndex)){                                        
                                        pingResult.text = "Ping SUCCESS"
                                        pingResult.color = "green"
                                    }else{
                                        pingResult.text = "Ping FAILED"
                                        pingResult.color = "red"
                                    }
                                }
                            }
                            Text {
                                id: pingResult
                                Layout.preferredWidth: 80
                                text: ""
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Item {
                                Layout.fillWidth: true
                            }


                            Button {
                                id: ledButton
                                text: "Toggle LED"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 50
                                hoverEnabled: true  // Enable hover detection
                                enabled: LIFUConnector.txConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: ledButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    if(LIFUConnector.sendLedToggleCommand("TX", moduleTabBar.currentIndex))
                                    {
                                        toggleLedResult.text = "LED Toggled"
                                        toggleLedResult.color = "green"
                                    }
                                    else{
                                        toggleLedResult.text = "LED Toggle FAILED"
                                        toggleLedResult.color = "red"
                                    }
                                }
                            }
                            

                            Text {
                                id: toggleLedResult
                                Layout.preferredWidth: 80
                                color: "#BDC3C7"
                                text: ""
                            }

                            // Row 2
                            // Echo Button and Result
                            Button {
                                id: echoButton
                                text: "Echo"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 50
                                hoverEnabled: true  // Enable hover detection
                                enabled: LIFUConnector.txConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"  // Gray out text when disabled
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: echoButtonBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    if(LIFUConnector.sendEchoCommand("TX", moduleTabBar.currentIndex))
                                    {
                                        echoResult.text = "Echo SUCCESS"
                                        echoResult.color = "green"
                                    }
                                    else{
                                        echoResult.text = "Echo FAILED"
                                        echoResult.color = "red"
                                    }
                                } 
                            }
                            Text {
                                id: echoResult
                                Layout.preferredWidth: 80
                                text: ""
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            Item {
                                
                            }
                            

                            Item {
                                
                            }
                        }
                    }
                    
                    // Trigger Tests
                    Rectangle {
                        // width: 500
                        Layout.fillWidth: true
                        height: 390
                        radius: 6
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        // Title at Top-Center with 5px Spacing
                        Text {
                            text: "Trigger and TX Output Tests"
                            color: "#BDC3C7"
                            font.pixelSize: 18
                            anchors.top: parent.top
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.topMargin: 5  // 5px spacing from the top
                        }
                        

                        // Content for comms tests
                        GridLayout {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.leftMargin: 20
                            anchors.topMargin: 45
                            columns: 4
                            rowSpacing: 5
                            columnSpacing: 15

                            // Row 1: Frequency & Pulse Count
                            Text {
                                font.pixelSize: 13
                                text: "Frequency (Hz):"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            TextField {
                                id: triggerFrequency
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                text: "10"
                                enabled: LIFUConnector.txConnected
                                color: "#BDC3C7"
                                font.pixelSize: 12
                                background: Rectangle {
                                    color: parent.enabled ? "#2C3E50" : "#3A3F4B"
                                    radius: 4
                                    border.color: "#BDC3C7"
                                }
                            }
                            Text {
                                font.pixelSize: 13
                                text: "Pulse Count:"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            TextField {
                                id: triggerPulseCount
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                text: "5"
                                enabled: LIFUConnector.txConnected
                                color: "#BDC3C7"
                                font.pixelSize: 12
                                background: Rectangle {
                                    color: parent.enabled ? "#2C3E50" : "#3A3F4B"
                                    radius: 4
                                    border.color: "#BDC3C7"
                                }
                            }

                            // Row 2: Pulse Width & Train Interval
                            Text {
                                font.pixelSize: 13
                                text: "Pulse Width (µs):"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            TextField {
                                id: triggerPulseWidth
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                text: "20"
                                enabled: LIFUConnector.txConnected
                                color: "#BDC3C7"
                                font.pixelSize: 12
                                background: Rectangle {
                                    color: parent.enabled ? "#2C3E50" : "#3A3F4B"
                                    radius: 4
                                    border.color: "#BDC3C7"
                                }
                            }
                            Text {
                                font.pixelSize: 13
                                text: "Train Interval:"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            TextField {
                                id: triggerTrainInterval
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                text: "1000000"
                                enabled: LIFUConnector.txConnected
                                color: "#BDC3C7"
                                font.pixelSize: 12
                                background: Rectangle {
                                    color: parent.enabled ? "#2C3E50" : "#3A3F4B"
                                    radius: 4
                                    border.color: "#BDC3C7"
                                }
                            }

                            // Row 3: Trigger Mode & Train Count
                            Text {
                                font.pixelSize: 13
                                text: "Trigger Mode:"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            ComboBox {
                                id: triggerModeDropdown
                                Layout.preferredWidth: 120
                                Layout.preferredHeight: 30
                                model: ["Sequence", "Continuous", "Single"]
                                currentIndex: 1  // Default to Continuous
                                enabled: LIFUConnector.txConnected
                                font.pixelSize: 11
                            }
                            Text {
                                font.pixelSize: 13
                                text: "Train Count:"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            TextField {
                                id: triggerTrainCount
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                text: "5"
                                enabled: LIFUConnector.txConnected
                                color: "#BDC3C7"
                                font.pixelSize: 12
                                background: Rectangle {
                                    color: parent.enabled ? "#2C3E50" : "#3A3F4B"
                                    radius: 4
                                    border.color: "#BDC3C7"
                                }
                            }

                            // Row 4: Set Trigger Button & Toggle Trigger Button
                            Item { }
                            Button {
                                id: setTriggerButton
                                text: "Set Trigger"
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                hoverEnabled: true
                                enabled: LIFUConnector.txConnected

                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 11
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    color: {
                                        if (!parent.enabled) return "#3A3F4B"
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) return "#7F8C8D"
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                    }
                                }

                                onClicked: {
                                    var json_trigger_data = {
                                        "TriggerFrequencyHz": parseInt(triggerFrequency.text),
                                        "TriggerPulseCount": parseInt(triggerPulseCount.text),
                                        "TriggerPulseWidthUsec": parseInt(triggerPulseWidth.text),
                                        "TriggerPulseTrainInterval": parseInt(triggerTrainInterval.text),
                                        "TriggerPulseTrainCount": parseInt(triggerTrainCount.text),
                                        "TriggerMode": triggerModeDropdown.currentIndex,
                                        "ProfileIndex": 0,
                                        "ProfileIncrement": 0
                                    };

                                    var jsonString = JSON.stringify(json_trigger_data);
                                    var success = LIFUConnector.setTrigger(jsonString);
                                    if (success) {
                                        console.log("Trigger configured:", jsonString);
                                    } else {
                                        console.log("Failed to set trigger configuration");
                                    }
                                }
                            }
                            Item { }
                            Button {
                                id: triggerEnable
                                text: "Toggle Trigger"
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                hoverEnabled: true
                                enabled: LIFUConnector.txConnected

                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 11
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    color: {
                                        if (!parent.enabled) return "#3A3F4B"
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B"
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) return "#7F8C8D"
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7"
                                    }
                                }

                                onClicked: {
                                    var success = LIFUConnector.toggleTrigger();
                                    if (success) {
                                        console.log("Trigger toggled successfully.");
                                    } else {
                                        console.log("Failed to toggle trigger.");
                                    }
                                }
                            }

                            // Row 5: Trigger Status
                            Text {
                                font.pixelSize: 13
                                text: "Trigger Status:"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            Text {
                                id: triggerStatus
                                text: ""
                                color: "#BDC3C7"
                                font.pixelSize: 13
                                Layout.columnSpan: 3
                            }
                            
                            // Spacer row
                            Item {
                                Layout.columnSpan: 4
                                Layout.preferredHeight: 10
                            }
                            
                            // Row 6: TX Config
                            Text {
                                font.pixelSize: 13
                                text: "TX Config:"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            ComboBox {
                                id: txconfigDropdown
                                Layout.preferredWidth: 120
                                Layout.preferredHeight: 30
                                model: ["100KHz", "200KHz", "400KHz"]
                                enabled: LIFUConnector.txConnected
                                font.pixelSize: 11

                                onActivated: {
                                    if(LIFUConnector.triggerEnabled){
                                        LIFUConnector.toggleTrigger();
                                        txconfigStatus.text = ""
                                    }
                                }
                            }

                            Item { }

                            Button {
                                id: setTxConfig
                                text: "Set TX Config"
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                hoverEnabled: true
                                enabled: LIFUConnector.txConnected 

                                contentItem: Text {
                                    text: parent.text
                                    font.pixelSize: 11
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    id: setTxConfigBackground
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";  // Disabled color
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";  // Blue on hover, default otherwise
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";  // Disabled border color
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";  // White border on hover, default otherwise
                                    }
                                }

                                onClicked: {
                                    // Set configuration of transmitter
                                    
                                    if(LIFUConnector.triggerEnabled){
                                        LIFUConnector.toggleTrigger();
                                    }
                                    txconfigStatus.text = ""
                                    var selectedIndex = txconfigDropdown.currentIndex;
                                    let frequency = 400000
                                    let pulse_count = 5
                                    let durationMS = 2e-5

                                    // Update frequency and pulse width based on the selected index
                                    switch (selectedIndex) {
                                        case 0: // 100KHz 
                                            frequency = 100000
                                            pulse_count = 4
                                            durationMS = 2e-5
                                            break;
                                        case 1: // 200KHz 
                                            frequency = 200000
                                            pulse_count = 8
                                            durationMS = 2e-5
                                            break;
                                        case 2: // 400KHz 
                                            frequency = 400000
                                            pulse_count = 10
                                            durationMS = 2e-5
                                            break;
                                        default:
                                            console.log("Invalid selection");
                                            return;
                                    }

                                    // Call your function with the selected index
                                    LIFUConnector.configure_transmitter(0,0,25,frequency,12.0,5.0,1.0,0,1,durationMS,"continuous");
                                }
                            }

                            // Row 7: TX Config Status
                            Text {
                                font.pixelSize: 13
                                text: "TX Config Status:"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }
                            Text {
                                id: txconfigStatus
                                text: ""
                                color: "#BDC3C7"
                                font.pixelSize: 13
                                Layout.columnSpan: 3
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

                        // TX Status Indicator
                        RowLayout {
                            spacing: 8

                            Rectangle {
                                width: 20
                                height: 20
                                radius: 10
                                color: LIFUConnector.txConnected ? "green" : "red"
                                border.color: "black"
                                border.width: 1
                            }

                            Text {
                                text: LIFUConnector.txConnected
                                      ? modules.length + " Module" + (modules.length !== 1 ? "s" : "") + " Connected"
                                      : "Not Connected"
                                font.pixelSize: 16
                                color: "#BDC3C7"
                            }

                            // Spacer to push the Refresh Button to the right
                            Item { Layout.fillWidth: true }

                            // Refresh Button
                            Rectangle {
                                width: 30
                                height: 30
                                radius: 15
                                color: enabled ? "#2C3E50" : "#7F8C8D"
                                Layout.alignment: Qt.AlignRight
                                enabled: LIFUConnector.txConnected

                                Text {
                                    text: "\u21BB"
                                    anchors.centerIn: parent
                                    font.pixelSize: 20
                                    font.family: iconFont.name
                                    color: enabled ? "white" : "#BDC3C7"
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    enabled: parent.enabled
                                    onClicked: {
                                        console.log("Manual Refresh Triggered")
                                        updateStates()
                                    }
                                    onEntered: if (parent.enabled) parent.color = "#34495E"
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

                        // Module Tabs
                        TabBar {
                            id: moduleTabBar
                            Layout.fillWidth: true
                            visible: modules.length > 0

                            onCurrentIndexChanged: {
                                pingResult.text = ""
                                toggleLedResult.text = ""
                                echoResult.text = ""
                            }

                            Repeater {
                                model: modules.length
                                TabButton {
                                    text: "Module " + index
                                    width: implicitWidth
                                }
                            }
                        }

                        StackLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            currentIndex: moduleTabBar.currentIndex
                            visible: modules.length > 0

                            Repeater {
                                model: modules.length

                                Item {
                                    // Capture index for use inside nested elements
                                    property int moduleIndex: index

                                    ColumnLayout {
                                        anchors.fill: parent
                                        spacing: 10

                                        // Device Info
                                        RowLayout {
                                            spacing: 8
                                            Text { text: "Device ID:"; color: "#BDC3C7"; font.pixelSize: 14 }
                                            Text { text: modules[moduleIndex] ? modules[moduleIndex].deviceId : "N/A"; color: "#3498DB"; font.pixelSize: 14 }
                                        }

                                        RowLayout {
                                            spacing: 8
                                            Text { text: "Firmware Version:"; color: "#BDC3C7"; font.pixelSize: 14 }
                                            Text { text: modules[moduleIndex] ? modules[moduleIndex].firmwareVersion : "N/A"; color: "#2ECC71"; font.pixelSize: 14 }
                                        }

                                        // Temperature Widgets
                                        TemperatureWidget {
                                            temperature: modules[moduleIndex] ? modules[moduleIndex].tx_temperature : 0.0
                                            tempName: "TX Temperature"
                                            Layout.fillWidth: true
                                        }

                                        TemperatureWidget {
                                            temperature: modules[moduleIndex] ? modules[moduleIndex].amb_temperature : 0.0
                                            tempName: "Ambient Temperature"
                                            Layout.fillWidth: true
                                        }

                                        Item { Layout.fillHeight: true }

                                        // Per-module Soft Reset Button
                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 40
                                            radius: 10
                                            color: enabled ? "#E74C3C" : "#7F8C8D"
                                            enabled: LIFUConnector.txConnected

                                            Text {
                                                text: "Soft Reset Module " + moduleIndex
                                                anchors.centerIn: parent
                                                color: parent.enabled ? "white" : "#BDC3C7"
                                                font.pixelSize: 16
                                                font.weight: Font.Bold
                                            }

                                            MouseArea {
                                                anchors.fill: parent
                                                enabled: parent.enabled
                                                onClicked: {
                                                    console.log("Soft Reset Module", moduleIndex)
                                                    LIFUConnector.softResetTXModule(moduleIndex)
                                                }
                                                onEntered: if (parent.enabled) parent.color = "#C0392B"
                                                onExited: if (parent.enabled) parent.color = "#E74C3C"
                                            }

                                            Behavior on color { ColorAnimation { duration: 200 } }
                                        }
                                    }
                                }
                            }
                        }

                        // Placeholder when no modules detected
                        Text {
                            visible: modules.length === 0
                            text: "No modules detected"
                            color: "#7F8C8D"
                            font.pixelSize: 14
                            Layout.alignment: Qt.AlignHCenter
                        }
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
