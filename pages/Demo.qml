import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import QtQuick.Dialogs

Rectangle {
    id: demoPage
    width: parent.width
    height: parent.height
    color: "#29292B"
    radius: 20
    opacity: 0.95

    // Properties to track solution loading state
    property bool solutionLoaded: LIFUConnector.solutionLoaded
    property bool controlsReadOnly: solutionLoaded || LIFUConnector.state >= 2
    property bool uiLockedAfterSend: false
    property bool uiNeedsResend: false
    property int solutionConfigLabelWidth: 190
    property int solutionConfigInputWidth: 160
    property var txTemperatures: []
    property real hvPositiveRail: NaN
    property real hvNegativeRail: NaN
    property string statusOverrideText: ""
    property int configuredModuleCount: 0
    property int previousConnectorState: LIFUConnector.state
    
    // Properties to track field activity based on trigger mode
    property bool pulseIntervalActive: true
    property bool pulseCountActive: true
    property bool trainIntervalActive: triggerModeDropdown.currentText !== "Single"
    property bool trainCountActive: triggerModeDropdown.currentText === "Sequence"
    
    // Property to track if train interval is less than pulse interval x pulse count
    property bool trainIntervalTooShort: false
    
    // Function to update the validation
    function updateTrainIntervalValidation() {
        if (!triggerPulseInterval || !triggerPulseCount || !triggerPulseTrainInterval) {
            trainIntervalTooShort = false
            return
        }
        
        var pulseIntervalMs = parseFloat(triggerPulseInterval.text || "100")
        var pulseCount = parseFloat(triggerPulseCount.text || "1")
        var trainInterval = parseFloat(triggerPulseTrainInterval.text || "0")
        
        if (isNaN(pulseIntervalMs) || isNaN(pulseCount) || isNaN(trainInterval)) {
            trainIntervalTooShort = false
            return
        }

        var pulseIntervalSeconds = pulseIntervalMs / 1000.0
        
        trainIntervalTooShort = trainInterval < (pulseIntervalSeconds * pulseCount)
    }

    function getSystemStateText() {
        return "System State: " + (LIFUConnector.state === 0 ? "Disconnected"
                            : LIFUConnector.state === 1 ? "TX Connected, Not Configured"
                            : LIFUConnector.state === 2 ? "Configured"
                            : LIFUConnector.state === 3 ? "Ready"
                            : "Running")
    }

    function getTxTemperatureText() {
        if (!LIFUConnector.txConnected) {
            return "Temp [--.-]"
        }

        let displayCount = Math.max(configuredModuleCount, txTemperatures.length)
        if (displayCount === 0) {
            return "Temp [--.-]"
        }

        let displayValues = []
        for (let index = 0; index < displayCount; index++) {
            let temp = txTemperatures[index]
            displayValues.push(typeof temp === "number" && !isNaN(temp) ? temp.toFixed(1) : "--")
        }

        return "Temp [" + displayValues.join(", ") + "] C"
    }

    function getHvRailText() {
        if (!LIFUConnector.hvConnected || isNaN(hvPositiveRail) || isNaN(hvNegativeRail)) {
            return "Rails +--.-- / ----.-- V"
        }

        return "Rails +" + hvPositiveRail.toFixed(2) + " / -" + Math.abs(hvNegativeRail).toFixed(2) + " V"
    }

    function refreshStatusTelemetry() {
        if (LIFUConnector.txConnected) {
            if (configuredModuleCount <= 0) {
                LIFUConnector.queryNumModules()
            }
            LIFUConnector.queryTxTemperature()
        }

        if (LIFUConnector.hvConnected) {
            LIFUConnector.getMonitorVoltages()
        }
    }

    function clearStatusTelemetry() {
        txTemperatures = []
        hvPositiveRail = NaN
        hvNegativeRail = NaN
    }

    // Keep a button visually depressed while its click work executes.
    function runWithButtonFeedback(button, action) {
        if (!button || !action || button.visualPressed) {
            return
        }

        button.visualPressed = true
        Qt.callLater(function() {
            try {
                action()
            } finally {
                button.visualPressed = false
            }
        })
    }

    function getIndicatorColor(isConnected) {
        if (!isConnected) {
            return "#C0392B"
        }

        if (LIFUConnector.state === 4) {
            return "#43bb57"
        }

        if (LIFUConnector.state === 1) {
            return "#5BC0EB"
        }

        if (LIFUConnector.state === 2 || LIFUConnector.state === 3) {
            return "#31aa63"
        }

        return "#5BC0EB"
    }

    // File dialog for loading solutions
    FileDialog {
        id: solutionFileDialog
        title: "Load Solution File"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: {
            console.log("Selected file: " + selectedFile)
            var filePath = selectedFile.toString()
            
            // Convert file URL to local path
            if (filePath.startsWith("file:///")) {
                // Windows: file:///C:/path -> C:/path
                filePath = filePath.substring(8)
            } else if (filePath.startsWith("file://")) {
                // Unix: file://path -> /path
                filePath = filePath.substring(7)
            }
            
            // Convert forward slashes to backslashes on Windows
            if (Qt.platform.os === "windows") {
                filePath = filePath.replace(/\//g, "\\")
            }
            
            console.log("Converted file path: " + filePath)
            LIFUConnector.loadSolutionFromFile(filePath)
            LIFUConnector.generate_plot(
                    xInput.text, yInput.text, zInput.text,
                    frequencyInput.text, voltage.text, triggerPulseInterval.text,
                    triggerPulseCount.text, triggerPulseTrainInterval.text, triggerPulseTrainCount.text,
                    durationInput.text, "buffer"
            );
        }
    }

    // Function to apply loaded solution settings to UI controls
    function applySolutionSettings() {
        if (LIFUConnector.solutionLoaded) {
            var settings = LIFUConnector.getLoadedSolutionSettings()
            
            // Apply focus settings
            xInput.text = settings.xInput.toString()
            yInput.text = settings.yInput.toString()
            zInput.text = settings.zInput.toString()
            
            // Apply pulse settings
            frequencyInput.text = settings.frequency.toString()
            durationInput.text = settings.duration.toString()
            voltage.text = settings.voltage.toString()
            
            // Apply trigger settings
            triggerPulseInterval.text = settings.pulseInterval.toString()
            triggerPulseCount.text = settings.pulseCount.toString()
            triggerPulseTrainInterval.text = settings.trainInterval.toString()
            triggerPulseTrainCount.text = settings.trainCount.toString()
        }
    }

    // HEADER
    Text {
        text: "Focused Ultrasound Demo"
        font.pixelSize: 18
        font.weight: Font.Bold
        color: "white"
        horizontalAlignment: Text.AlignHCenter
        anchors {
            top: parent.top
            left: parent.left
            right: parent.right
            topMargin: 10
        }
    }

    // Initialize validation after all components are created
    Component.onCompleted: {
        updateTrainIntervalValidation()
    }
    
    // LAYOUT
    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 20

        // Left Column (Input Panel)
        Rectangle {
            id: inputContainer
            width: 500
            height: 630
            color: "#1E1E20"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 15

                GroupBox {
                    title: "Pulse Profile"
                    Layout.fillWidth: true

                    GridLayout {
                        columns: 2
                        width: parent.width

                        Text {
                            text: "Voltage per Rail (+/-):"
                            color: "white"
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft

                            HoverHandler {
                                id: voltageHover
                            }

                            ToolTip {
                                visible: voltageHover.hovered
                                text: "High voltage setting applied to the ultrasound transducer.\nPeak to Peak Voltage will be double this value"
                                delay: 500
                            }
                        }
                        TextField {
                            id: voltage
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "12.0"
                            color: controlsReadOnly ? "#BBB" : "white"
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                        }

                        Text { 
                            text: "Frequency (kHz):" 
                            color: "white" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: frequencyHover
                            }
                            
                            ToolTip {
                                visible: frequencyHover.hovered
                                text: "Ultrasound center frequency (kHz)"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: frequencyInput
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "400"
                            color: controlsReadOnly ? "#BBB" : "white" 
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                        }

                        Text { 
                            text: "Duration (uS):" 
                            color: "white" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: durationHover
                            }
                            
                            ToolTip {
                                visible: durationHover.hovered
                                text: "Duration of each ultrasound pulse (uS)"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: durationInput
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "200"
                            color: controlsReadOnly ? "#BBB" : "white" 
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                        }
                    }
                }

                GroupBox {
                    title: "Pulse Timing Settings"
                    Layout.fillWidth: true

                    GridLayout {
                        columns: 2
                        width: parent.width
                        Text { 
                            text: "Trigger Mode:" 
                            color: "white" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: triggerModeHover
                            }
                            
                            ToolTip {
                                visible: triggerModeHover.hovered
                                text: "Single: one pulse train\nContinuous: indefinitely repeated pulse trains\nSequence: fixed pulse train sequence"
                                delay: 500
                            }
                        }

						ComboBox {
							id: triggerModeDropdown
                            Layout.preferredWidth: solutionConfigInputWidth
							Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
							model: ["Single", "Continuous", "Sequence"]
                            currentIndex: 1
                            enabled: !controlsReadOnly
							
							background: Rectangle {
                                color: "#222"
                                border.color: "#999"
                                radius: 4
                            }
							
							onActivated: {
								var selectedIndex = triggerModeDropdown.currentText;
								console.log("Selected " + selectedIndex);
								
							}
						}

                        Text { 
                            text: "Pulse Interval (ms):" 
                            color: pulseIntervalActive ? "white" : "#888" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: pulseIntervalHover
                            }
                            
                            ToolTip {
                                visible: pulseIntervalHover.hovered
                                text: "Time interval between initiation of successive pulses (ms)"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: triggerPulseInterval
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "100"
                            color: controlsReadOnly ? (pulseIntervalActive ? "#BBB" : "#777") : (pulseIntervalActive ? "white" : "#888")
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextChanged: updateTrainIntervalValidation()
                        }

                        Text { 
                            text: "Pulses per Pulse Train:" 
                            color: pulseCountActive ? "white" : "#888" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: pulseCountHover
                            }
                            
                            ToolTip {
                                visible: pulseCountHover.hovered
                                text: "Number of pulses repeated in a Pulse Train"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: triggerPulseCount
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "1"
                            color: controlsReadOnly ? (pulseCountActive ? "#BBB" : "#777") : (pulseCountActive ? "white" : "#888")
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextChanged: updateTrainIntervalValidation()
                        }

                        Text { 
                            text: trainIntervalTooShort ? "Pulse Train Interval (S)*:" : "Pulse Train Interval (S): "
                            color: trainIntervalActive ? "white" : "#888" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: labelHover
                            }
                            
                            ToolTip {
                                visible: labelHover.hovered
                                text: trainIntervalTooShort ? "When Pulse Train Interval is less than Pulse Interval x Pulse Count,\nPulse Trains will fire back-to-back with no delay" : "Interval between the start of successive Pulse Trains (S)"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: triggerPulseTrainInterval
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "0"
                            color: controlsReadOnly ? (trainIntervalActive ? "#BBB" : "#777") : (trainIntervalActive ? "white" : "#888")
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextChanged: updateTrainIntervalValidation()
                        }

                        Text { 
                            text: "Pulse Train Count:" 
                            color: trainCountActive ? "white" : "#888" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: trainCountHover
                            }
                            
                            ToolTip {
                                visible: trainCountHover.hovered
                                text: "Total number of Pulse Trains to generate in Sequence mode"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: triggerPulseTrainCount
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "1"
                            color: controlsReadOnly ? (trainCountActive ? "#BBB" : "#777") : (trainCountActive ? "white" : "#888")
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                        }
                    }
                }

                GroupBox {
                    title: solutionLoaded ? "Beam Focus (Delays and Apodizations Loaded Directly from Solution)" : "Beam Focus"
                    Layout.fillWidth: true

                    RowLayout {
                        width: parent.width
                        spacing: 16

                        RowLayout {
                            spacing: 6

                            Text {
                                text: "Lateral (X):"
                                color: "white"

                                HoverHandler {
                                    id: xPositionHover
                                }

                                ToolTip {
                                    visible: xPositionHover.hovered
                                    text: "Lateral beam focus position (mm)"
                                    delay: 500
                                }
                            }
                            TextField {
                                id: xInput
                                Layout.preferredWidth: 56
                                Layout.minimumWidth: 56
                                Layout.maximumWidth: 56
                                Layout.preferredHeight: 32
                                font.pixelSize: 14
                                text: "0"
                                color: controlsReadOnly ? "#BBB" : "white"
                                enabled: !controlsReadOnly
                                background: Rectangle {
                                    color: controlsReadOnly ? "#333" : "#222"
                                    border.color: controlsReadOnly ? "#777" : "#999"
                                    radius: 4
                                }
                            }
                        }

                        RowLayout {
                            spacing: 6

                            Text {
                                text: "Elevation (Y):"
                                color: "white"

                                HoverHandler {
                                    id: yPositionHover
                                }

                                ToolTip {
                                    visible: yPositionHover.hovered
                                    text: "Elevational beam focus position (mm)"
                                    delay: 500
                                }
                            }
                            TextField {
                                id: yInput
                                Layout.preferredWidth: 56
                                Layout.minimumWidth: 56
                                Layout.maximumWidth: 56
                                Layout.preferredHeight: 32
                                font.pixelSize: 14
                                text: "0"
                                color: controlsReadOnly ? "#BBB" : "white"
                                enabled: !controlsReadOnly
                                background: Rectangle {
                                    color: controlsReadOnly ? "#333" : "#222"
                                    border.color: controlsReadOnly ? "#777" : "#999"
                                    radius: 4
                                }
                            }
                        }

                        RowLayout {
                            spacing: 6

                            Text {
                                text: "Axial (Z):"
                                color: "white"

                                HoverHandler {
                                    id: zPositionHover
                                }

                                ToolTip {
                                    visible: zPositionHover.hovered
                                    text: "Axial beam focus position (mm)"
                                    delay: 500
                                }
                            }
                            TextField {
                                id: zInput
                                Layout.preferredWidth: 56
                                Layout.minimumWidth: 56
                                Layout.maximumWidth: 56
                                Layout.preferredHeight: 32
                                font.pixelSize: 14
                                text: "25"
                                color: controlsReadOnly ? "#BBB" : "white"
                                enabled: !controlsReadOnly
                                background: Rectangle {
                                    color: controlsReadOnly ? "#333" : "#222"
                                    border.color: controlsReadOnly ? "#777" : "#999"
                                    radius: 4
                                }
                            }
                        }
                    }
                }

                // BUTTONS
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Button {
                        id: loadSolutionButton
                        property bool visualPressed: false
                        text: "Load Solution"
                        Layout.fillWidth: true
                        enabled: (!solutionLoaded) && (LIFUConnector.state <2) && !visualPressed
                        background: Rectangle {
                            color: (loadSolutionButton.down || loadSolutionButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            runWithButtonFeedback(loadSolutionButton, function() {
                                solutionFileDialog.open()
                            })
                        }
                    }

                    Button {
                        id: editSolutionButton
                        property bool visualPressed: false
                        text: "Edit Solution"
                        Layout.fillWidth: true
                        enabled: controlsReadOnly && (LIFUConnector.state <4) && !visualPressed
                        background: Rectangle {
                            color: (editSolutionButton.down || editSolutionButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            runWithButtonFeedback(editSolutionButton, function() {
                                LIFUConnector.reset_configuration()
                                LIFUConnector.makeLoadedSolutionEditable()
                                statusOverrideText = ""
                            })
                        }
                    }
                }
            }
        }

        // RIGHT COLUMN (Graph + Status Panel)
        ColumnLayout {
            width: 500
            height: 630
            spacing: 20

            Rectangle {
                id: graphContainer
                Layout.fillWidth: true
                Layout.fillHeight: false
                Layout.preferredHeight: 440
                Layout.maximumHeight: 460
                Layout.minimumHeight: 320
                color: "#1E1E20"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                Image {
                    id: ultrasoundGraph
                    anchors.fill: parent
                    anchors.margins: 10
                    fillMode: Image.PreserveAspectFit
                    source: "../assets/images/empty_graph.png"


                    function updateImage(base64data) {
                        if (base64data.startsWith("data:image/png;base64,")) {
                            source = base64data;
                        } else {
                            source = base64data;
                        }
                    }
                }
            }

            // Status Panel (Connection Indicators + Controls)
            Rectangle {
                id: statusPanel
                Layout.fillWidth: true
                Layout.preferredHeight: 170
                color: "#252525"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 10

                    // Connection status text
                    Text {
                        id: statusText
                        text: statusOverrideText !== "" ? statusOverrideText : getSystemStateText()
                        font.pixelSize: 16
                        color: getIndicatorColor(LIFUConnector.txConnected && LIFUConnector.hvConnected)
                        horizontalAlignment: Text.AlignHCenter
                        Layout.alignment: Qt.AlignHCenter
                        SequentialAnimation on opacity {
                            running: LIFUConnector.state === 4
                            loops: Animation.Infinite
                            NumberAnimation { from: 1.0; to: 0.35; duration: 500 }
                            NumberAnimation { from: 0.35; to: 1.0; duration: 500 }
                        }
                    }

                    // Connection Indicators (TX, HV)
                    RowLayout {
                        spacing: 20
                        Layout.alignment: Qt.AlignHCenter

                        // TX LED
                        RowLayout {
                            spacing: 5
                            Rectangle {
                                id: txIndicator
                                width: 20
                                height: 20
                                radius: 10
                                color: getIndicatorColor(LIFUConnector.txConnected)
                                border.color: "black"
                                border.width: 1
                            }
                            Text {
                                text: "TX"
                                font.pixelSize: 16
                                color: "#BDC3C7"
                                verticalAlignment: Text.AlignVCenter
                            }

                            Text {
                                text: getTxTemperatureText()
                                font.pixelSize: 12
                                color: "#9FB3C8"
                                verticalAlignment: Text.AlignVCenter
                            }
                        }

                        // HV LED
                        RowLayout {
                            spacing: 5
                            Rectangle {
                                id: hvIndicator
                                width: 20
                                height: 20
                                radius: 10
                                color: getIndicatorColor(LIFUConnector.hvConnected)
                                border.color: "black"
                                border.width: 1
                            }
                            Text {
                                text: "HV"
                                font.pixelSize: 16
                                color: "#BDC3C7"
                                verticalAlignment: Text.AlignVCenter
                            }

                            Text {
                                text: getHvRailText()
                                font.pixelSize: 12
                                color: "#9FB3C8"
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10

                        Button {
                            id: configureButton
                            property bool visualPressed: false
                            text: "Configure"
                            Layout.fillWidth: true
                            enabled: (LIFUConnector.state === 1) && !visualPressed  // TX connected and not configured
                            background: Rectangle {
                                color: (configureButton.down || configureButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            onClicked: {
                                runWithButtonFeedback(configureButton, function() {
                                    var frequency = (1.0 / parseFloat(triggerPulseInterval.text)).toString()
                                    LIFUConnector.configure_transmitter(xInput.text, yInput.text,
                                        zInput.text,  frequencyInput.text, voltage.text, triggerPulseInterval.text, triggerPulseCount.text,
                                        triggerPulseTrainInterval.text, triggerPulseTrainCount.text, durationInput.text,
                                        triggerModeDropdown.currentText);
                                    configuredModuleCount = LIFUConnector.queryNumModulesConnected
                                    LIFUConnector.generate_plot(
                                         xInput.text, yInput.text, zInput.text,
                                         frequencyInput.text, voltage.text, triggerPulseInterval.text,
                                         triggerPulseCount.text, triggerPulseTrainInterval.text, triggerPulseTrainCount.text,
                                         durationInput.text, "buffer"
                                    );
                                    statusOverrideText = ""
                                })
                            }
                        }

                        Button {
                            id: startButton
                            property bool visualPressed: false
                            text: "Start"
                            Layout.fillWidth: true
                            enabled: (LIFUConnector.state === 3) && !visualPressed  // READY
                            background: Rectangle {
                                color: (startButton.down || startButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            onClicked: {
                                runWithButtonFeedback(startButton, function() {
                                    console.log("Starting Sonication...");
                                    LIFUConnector.start_sonication();
                                })
                            }
                        }

                        Button {
                            id: stopButton
                            property bool visualPressed: false
                            text: "Stop"
                            Layout.fillWidth: true
                            enabled: (LIFUConnector.state === 4) && !visualPressed  // RUNNING
                            background: Rectangle {
                                color: (stopButton.down || stopButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            onClicked: {
                                runWithButtonFeedback(stopButton, function() {
                                    console.log("Stopping Sonication...");
                                    clearStatusTelemetry()
                                    LIFUConnector.stop_sonication();
                                })
                            }
                        }

                        Button {
                            id: resetButton
                            property bool visualPressed: false
                            text: "Reset"
                            Layout.fillWidth: true
                            enabled: (LIFUConnector.state > 1 && LIFUConnector.state != 4) && !visualPressed  // CONFIGURED
                            background: Rectangle {
                                color: (resetButton.down || resetButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            onClicked: {
                                runWithButtonFeedback(resetButton, function() {
                                    console.log("Resetting parameters...");
                                    xInput.text = "0";
                                    yInput.text = "0";
                                    zInput.text = "25";
                                    frequencyInput.text = "400";
                                    durationInput.text = "200";
                                    voltage.text = "12.0";
                                    triggerPulseInterval.text = "100";
                                    LIFUConnector.reset_configuration();
                                })
                            }
                        }
                    }
                }
            }
        }
    }

    Timer {
        id: postReadyTimer
        interval: 1000 // delay in milliseconds (e.g., 1000 = 1 second)
        repeat: false
        running: false
        onTriggered: {
            console.log("Calling follow-up connector method...");
            LIFUConnector.turnOffHV(); 
            LIFUConnector.setAsyncMode(false); 
        }
    }

    Timer {
        id: telemetryPollTimer
        interval: 1000
        repeat: true
        running: LIFUConnector.state === 4
        onTriggered: {
            refreshStatusTelemetry()
        }
    }

    // **Connections for LIFUConnector signals**
    Connections {
        target: LIFUConnector

        function onSignalConnected(descriptor, port) {
            console.log(descriptor + " connected on " + port);
            statusOverrideText = ""
        }

        function onSignalDisconnected(descriptor, port) {
            console.log(descriptor + " disconnected from " + port);
            if (descriptor === "TX") {
                txTemperatures = [];
                configuredModuleCount = 0;
            }
            if (descriptor === "HV") {
                hvPositiveRail = NaN;
                hvNegativeRail = NaN;
            }
            statusOverrideText = ""
        }

        function onSignalDataReceived(descriptor, message) {
            console.log("Data from " + descriptor + ": " + message);
        }

        function onPlotGenerated(imageData) {
            console.log("Received image data for display.");
            ultrasoundGraph.updateImage("data:image/png;base64," + imageData);
            statusOverrideText = "";
        }

        // Solution loading signal handlers
        function onSolutionFileLoaded(solutionName, message) {
            console.log("Solution loaded: " + solutionName + " - " + message);
            LIFUConnector.reset_configuration();
            applySolutionSettings();
            statusOverrideText = "";
        }

        function onSolutionLoadError(errorMessage) {
            console.error("Solution load error: " + errorMessage);
            statusOverrideText = "Error: " + errorMessage;
        }

        function onSolutionStateChanged() {
            console.log("Solution state changed - loaded:", LIFUConnector.solutionLoaded);
            if (!LIFUConnector.solutionLoaded) {
                statusOverrideText = "";
            }
        }

        function onTemperatureTxUpdated(module, tx_temp, amb_temp) {
            let updated = txTemperatures.slice()
            while (updated.length <= module) {
                updated.push(NaN)
            }
            updated[module] = tx_temp
            txTemperatures = updated
        }

        function onNumModulesUpdated() {
            configuredModuleCount = LIFUConnector.queryNumModulesConnected
        }

        function onMonVoltagesReceived(voltages) {
            if (voltages.length >= 4) {
                hvPositiveRail = voltages[0].converted_voltage
                hvNegativeRail = voltages[3].converted_voltage
            }
        }

        function onStateChanged(state) {
            statusOverrideText = "";

            if (previousConnectorState === 4 && state !== 4) {
                clearStatusTelemetry();
                postReadyTimer.stop();
            }

            if (state >= 2 && configuredModuleCount <= 0) {
                configuredModuleCount = LIFUConnector.queryNumModulesConnected
            }

            previousConnectorState = state;
        }
    }


    Component.onDestruction: {
        console.log("Closing UI, clearing LIFUConnector...");
    }
}
