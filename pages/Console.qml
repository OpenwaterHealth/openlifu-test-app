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
                voltageState = "Off" // Reset voltage state
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

        // Handle voltage readings updates
        function onMonVoltagesReceived(voltages) {
            // voltages is an array of 8 dictionaries with voltage, converted_voltage, etc.
            if (voltages.length >= 8) {
                voltage_HVP1.text = voltages[0].converted_voltage.toFixed(2) + " V"
                voltage_HVP2.text = voltages[1].converted_voltage.toFixed(2) + " V"
                voltage_HVM2.text = voltages[2].converted_voltage.toFixed(2) + " V"
                voltage_HVM1.text = voltages[3].converted_voltage.toFixed(2) + " V"
                voltage_12V.text = voltages[4].converted_voltage.toFixed(2) + " V"
                voltage_VCA1.text = voltages[5].converted_voltage.toFixed(2) + " V"
                voltage_VCB1.text = voltages[6].converted_voltage.toFixed(3) + " V"
                voltage_VCC1.text = voltages[7].converted_voltage.toFixed(1) + " V"
            }
        }

        function onRgbStateReceived(stateValue, stateText) {
            rgbState = stateText
            rgbLedResult.text = stateText  // Display the state as text
            rgbLedDropdown.currentIndex = stateValue  // Sync ComboBox to received state
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Title
        Text {
            text: "LIFU Console Unit Tests"
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
                        width: 650
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
                                enabled: LIFUConnector.hvConnected 

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
                                    pingResult.text = ""
                                    if(LIFUConnector.sendPingCommand("HV")){                                        
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
                                Layout.preferredWidth: 200 
                            }

                            Button {
                                id: ledButton
                                text: "Toggle LED"
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 50
                                hoverEnabled: true  // Enable hover detection
                                enabled: LIFUConnector.hvConnected 

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
                                    toggleLedResult.text = ""
                                    if(LIFUConnector.sendLedToggleCommand("HV"))
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
                                enabled: LIFUConnector.hvConnected 

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
                                    echoResult.text = ""
                                    if(LIFUConnector.sendEchoCommand("HV"))
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
                                Layout.preferredWidth: 200 
                            }

                            ComboBox {
                                id: rgbLedDropdown
                                Layout.preferredWidth: 120
                                Layout.preferredHeight: 40
                                model: ["Off", "Red", "Green", "Blue"]
                                enabled: LIFUConnector.hvConnected 

                                onActivated: {
                                    let rgbValue = rgbLedDropdown.currentIndex  // Directly map ComboBox index to integer value
                                    LIFUConnector.setRGBState(rgbValue)         // Assuming you implement this new method
                                    rgbLedResult.text = rgbLedDropdown.currentText
                                }
                            }
                            Text {
                                id: rgbLedResult
                                Layout.preferredWidth: 80
                                color: "#BDC3C7"
                                text: "Off"
                            }
                        }
                    }

                    // Power Tests Box
                    Rectangle {
                        width: 650
                        height: 195
                        radius: 8
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        // Title at Top-Center with 5px Spacing
                        Text {
                            text: "Power Tests"
                            color: "#BDC3C7"
                            font.pixelSize: 18
                            anchors.top: parent.top
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.topMargin: 5  // 5px spacing from the top
                        }

                        // Refresh Voltages Button
                        Rectangle {
                            width: 30
                            height: 30
                            radius: 15
                            color: enabled ? "#2C3E50" : "#7F8C8D"
                            anchors.top: parent.top
                            anchors.right: parent.right
                            anchors.topMargin: 5
                            anchors.rightMargin: 10
                            enabled: LIFUConnector.hvConnected

                            Text {
                                text: "\u21BB"  // Unicode refresh icon
                                anchors.centerIn: parent
                                font.pixelSize: 18
                                font.family: iconFont.name
                                color: parent.enabled ? "white" : "#BDC3C7"
                            }

                            MouseArea {
                                anchors.fill: parent
                                enabled: parent.enabled
                                onClicked: {
                                    console.log("Refreshing voltage readings...")
                                    LIFUConnector.getMonitorVoltages()
                                }

                                onEntered: if (parent.enabled) parent.color = "#34495E"
                                onExited: parent.color = parent.enabled ? "#2C3E50" : "#7F8C8D"
                            }
                        }
                        

                        // Content for power tests - 3 columns layout
                        GridLayout {
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.leftMargin: 20
                            anchors.topMargin: 40
                            columns: 3
                            rowSpacing: 10
                            columnSpacing: 20

                            // Row 1: Set HV
                            Text {
                                Layout.preferredWidth: 100
                                font.pixelSize: 16
                                text: "Set HV (+/-)"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            ComboBox {
                                id: hvDropdown
                                Layout.preferredWidth: 120
                                Layout.preferredHeight: 40
                                model: ["0", "5", "10", "15", "20", "25", "30", "35", "40", "45", "50", "55", "60", "65", "70"]
                                enabled: LIFUConnector.hvConnected 

                                onActivated: {
                                    var selectedValue = hvDropdown.currentText;
                                    if (selectedValue !== "0") {
                                        var success = LIFUConnector.setHVCommand(selectedValue);
                                        if (success) {
                                            console.log("Voltage set successfully");
                                        }  else {
                                            console.log("Failed to set voltage. Resetting ComboBox to '0'");
                                            hvDropdown.currentIndex = 0; // Index 0 corresponds
                                        }
                                    }
                                    else {
                                        if(LIFUConnector.hvState == "On")
                                        {
                                            LIFUConnector.toggleHV()
                                        }
                                    }
                                }
                            }

                            Button {
                                id: hvEnable
                                text: "HV Enable"
                                Layout.preferredWidth: 100
                                Layout.preferredHeight: 40
                                hoverEnabled: true
                                enabled: LIFUConnector.hvConnected && hvDropdown.currentText !== "-"

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";
                                    }
                                }

                                onClicked: {
                                    LIFUConnector.toggleHV()
                                }
                            }

                            // Row 2: HV Status
                            Text {
                                Layout.preferredWidth: 100
                                font.pixelSize: 16
                                text: "HV Status"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Text {
                                id: hvStatus
                                Layout.preferredWidth: 120
                                color: "#4A90E2"
                                font.pixelSize: 16
                                text: LIFUConnector.hvState ? "On" : "Off"
                            }

                            Item {
                                Layout.preferredWidth: 100
                            }

                            // Row 3: 12V Enable
                            Text {
                                Layout.preferredWidth: 100
                                font.pixelSize: 16
                                text: "12V Control"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Item {
                                Layout.preferredWidth: 120
                            }

                            Button {
                                id: v12Enable
                                text: "12V Enable"
                                Layout.preferredWidth: 100
                                Layout.preferredHeight: 40
                                hoverEnabled: true
                                enabled: LIFUConnector.hvConnected 

                                contentItem: Text {
                                    text: parent.text
                                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    color: {
                                        if (!parent.enabled) {
                                            return "#3A3F4B";
                                        }
                                        return parent.hovered ? "#4A90E2" : "#3A3F4B";
                                    }
                                    radius: 4
                                    border.color: {
                                        if (!parent.enabled) {
                                            return "#7F8C8D";
                                        }
                                        return parent.hovered ? "#FFFFFF" : "#BDC3C7";
                                    }
                                }

                                onClicked: {
                                    LIFUConnector.toggleV12()
                                }
                            }

                            // Row 4: 12V Status
                            Text {
                                Layout.preferredWidth: 100
                                font.pixelSize: 16
                                text: "12V Status"
                                color: "#BDC3C7"
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Text {
                                id: v12Status
                                Layout.preferredWidth: 120
                                color: "#4A90E2"
                                font.pixelSize: 16
                                text: "Off"
                            }

                            Item {
                                Layout.preferredWidth: 100
                            }
                        }

                        // Voltage Readings Grid - positioned on the right side
                        GridLayout {
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.rightMargin: 20
                            anchors.topMargin: 40
                            columns: 2
                            rowSpacing: 3
                            columnSpacing: 10

                            // HV+_1
                            Text {
                                text: "HV+_1:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_HVP1
                                text: "0.00 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }

                            // HV+_2
                            Text {
                                text: "HV+_2:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_HVP2
                                text: "0.00 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }

                            // HV-_2
                            Text {
                                text: "HV-_2:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_HVM2
                                text: "0.00 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }

                            // HV-_1
                            Text {
                                text: "HV-_1:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_HVM1
                                text: "0.00 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }

                            // V_HV_NEG
                            Text {
                                text: "12V:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_12V
                                text: "0.00 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }

                            // V_VBUS
                            Text {
                                text: "VC-A1:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_VCA1
                                text: "0.00 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }

                            // V_CURR
                            Text {
                                text: "VC-B1:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_VCB1
                                text: "0.00 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }

                            // V_TEMP
                            Text {
                                text: "VC-C1:"
                                color: "#BDC3C7"
                                font.pixelSize: 11
                            }
                            Text {
                                id: voltage_VCC1
                                text: "0.0 V"
                                color: "#4A90E2"
                                font.pixelSize: 11
                            }
                        }
                    }

                    // Fan Tests Box
                    Rectangle {
                        width: 650
                        height: 190
                        radius: 8
                        color: "#1E1E20"
                        border.color: "#3E4E6F"
                        border.width: 2

                        // Title at Top-Center with 5px Spacing
                        Text {
                            text: "Fan Tests"
                            color: "#BDC3C7"
                            font.pixelSize: 18
                            anchors.top: parent.top
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.topMargin: 5  // 5px spacing from the top
                        }

                        // Slider for Top Fan
                        Column {
                            anchors.top: parent.top
                            anchors.topMargin: 40  // Adjust spacing as needed
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 5

                            Text {
                                text: "Top Fan: " + (topFanSlider.value === 0 ? "OFF" : topFanSlider.value.toFixed(0) + "%")
                                color: "#BDC3C7"
                                font.pixelSize: 14
                            }

                            Slider {
                                id: topFanSlider
                                width: 600  // Adjust width as needed
                                from: 0
                                to: 100
                                stepSize: 10   // Snap to increments of 10
                                value: 0  // Default value is 0 (OFF)
                                enabled: LIFUConnector.hvConnected

                                property bool userIsSliding: false

                                onPressedChanged: {
                                    if (pressed) {
                                        userIsSliding = true
                                    } else if (!pressed && userIsSliding) {
                                        // User has finished sliding
                                        let snappedValue = Math.round(value / 10) * 10
                                        value = snappedValue
                                        console.log("Slider released at:", snappedValue)
                                        userIsSliding = false
                                        // Call the backend method with fan_id and speed
                                        let fanId = 1; // Example fan ID (adjust as needed) TOP
                                        let success = LIFUConnector.setFanLevel(fanId, snappedValue);
                                        if (success) {
                                            console.log("Fan speed set successfully");
                                        } else {
                                            console.log("Failed to set fan speed");
                                        }
                                    }
                                }
                            }
                        }

                        // Slider for Bottom Fan
                        Column {
                            anchors.top: parent.top
                            anchors.topMargin: 110  // Adjust spacing as needed
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: 5
                            enabled: LIFUConnector.hvConnected

                            Text {
                                text: "Bottom Fan: " + (bottomFanSlider.value === 0 ? "OFF" : bottomFanSlider.value.toFixed(0) + "%")
                                color: "#BDC3C7"
                                font.pixelSize: 14
                            }

                            Slider {
                                id: bottomFanSlider
                                width: 600  // Adjust width as needed
                                from: 0
                                to: 100
                                stepSize: 10   // Snap to increments of 10
                                value: 0  // Default value is 0 (OFF)


                                property bool userIsSliding: false

                                onPressedChanged: {
                                    if (pressed) {
                                        userIsSliding = true
                                    } else if (!pressed && userIsSliding) {
                                        // User has finished sliding
                                        let snappedValue = Math.round(value / 10) * 10
                                        value = snappedValue
                                        console.log("Slider released at:", snappedValue)
                                        userIsSliding = false
                                        // Call the backend method with fan_id and speed
                                        let fanId = 0; // Example fan ID (adjust as needed) Bottom
                                        let success = LIFUConnector.setFanLevel(fanId, snappedValue);
                                        if (success) {
                                            console.log("Fan speed set successfully");
                                        } else {
                                            console.log("Failed to set fan speed");
                                        }
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
                            TextField { 
                                text: deviceId
                                color: "#3498DB"
                                font.pixelSize: 14 
                                readOnly: true
                                background: Rectangle {
                                    color: "transparent"
                                    border.color: "transparent"
                                    radius: 0
                                }
                            }
                        }

                        // Display Firmware Version (Smaller Text)
                        RowLayout {
                            spacing: 8
                            Text { text: "Firmware Version:"; color: "#BDC3C7"; font.pixelSize: 14 }
                            TextField {
                                text: firmwareVersion
                                color: "#2ECC71"
                                font.pixelSize: 14
                                readOnly: true
                                background: Rectangle {
                                    color: "transparent"
                                    border.color: "transparent"
                                    radius: 0
                                }
                            }
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
                        Rectangle {
                            Layout.fillWidth: true
                            height: 40
                            radius: 10
                            color: enabled ? "#E74C3C" : "#7F8C8D"  // Red when enabled, gray when disabled
                            enabled: LIFUConnector.hvConnected  // Enable/disable based on HV connection

                            Text {
                                text: "Soft Reset"
                                anchors.centerIn: parent
                                color: parent.enabled ? "white" : "#BDC3C7"  // White when enabled, light gray when disabled
                                font.pixelSize: 18
                                font.weight: Font.Bold
                            }

                            MouseArea {
                                anchors.fill: parent
                                enabled: parent.enabled  // Disable MouseArea when the button is disabled
                                onClicked: {
                                    console.log("Soft Reset Triggered")
                                    LIFUConnector.softResetHV()
                                }

                                onEntered: {
                                    if (parent.enabled) {
                                        parent.color = "#C0392B"  // Darker red on hover (only when enabled)
                                    }
                                }
                                onExited: {
                                    if (parent.enabled) {
                                        parent.color = "#E74C3C"  // Restore original color (only when enabled)
                                    }
                                }
                            }

                            Behavior on color {
                                ColorAnimation { duration: 200 }
                            }
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
