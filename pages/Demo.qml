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
    property bool uiLockedAfterSend: false
    property bool uiNeedsResend: false
    property bool controlsReadOnly: solutionLoaded || uiLockedAfterSend
    property int solutionConfigLabelWidth: 190
    property int solutionConfigInputWidth: 160
    property string statusOverrideText: ""
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
        
        var pulseInterval = parseFloat(triggerPulseInterval.text || "0.1")
        var pulseCount = parseFloat(triggerPulseCount.text || "1")
        var trainInterval = parseFloat(triggerPulseTrainInterval.text || "0")
        
        if (isNaN(pulseInterval) || isNaN(pulseCount) || isNaN(trainInterval)) {
            trainIntervalTooShort = false
            return
        }
        
        trainIntervalTooShort = trainInterval < (pulseInterval * pulseCount)
    }

    function getSystemStateText() {
        return "System State: " + (LIFUConnector.state === 0 ? "Disconnected"
                            : LIFUConnector.state === 1 ? "TX Connected, Not Configured"
                            : LIFUConnector.state === 2 ? "Configured"
                            : LIFUConnector.state === 3 ? "Ready"
                            : "Running")
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
            height: 620
            color: "#1E1E20"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 15

                GroupBox {
                    title: "High Voltage"
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
                    }
                }

                GroupBox {
                    title: "Pulse Profile"
                    Layout.fillWidth: true

                    GridLayout {
                        columns: 2
                        width: parent.width

                        Text { 
                            text: "Frequency (Hz):" 
                            color: "white" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: frequencyHover
                            }
                            
                            ToolTip {
                                visible: frequencyHover.hovered
                                text: "Ultrasound center frequency (Hz)"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: frequencyInput
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "400e3"
                            color: controlsReadOnly ? "#BBB" : "white" 
                            enabled: !controlsReadOnly
                            background: Rectangle {
                                color: controlsReadOnly ? "#333" : "#222"
                                border.color: controlsReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                        }

                        Text { 
                            text: "Duration (S):" 
                            color: "white" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: durationHover
                            }
                            
                            ToolTip {
                                visible: durationHover.hovered
                                text: "Duration of each ultrasound pulse (S)"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: durationInput
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "2e-4"
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
                            text: "Pulse Interval (S):" 
                            color: pulseIntervalActive ? "white" : "#888" 
                            Layout.preferredWidth: solutionConfigLabelWidth
                            Layout.alignment: Qt.AlignLeft
                            
                            HoverHandler {
                                id: pulseIntervalHover
                            }
                            
                            ToolTip {
                                visible: pulseIntervalHover.hovered
                                text: "Time interval between initiation of successive pulses (S)"
                                delay: 500
                            }
                        }
                        TextField { 
                            id: triggerPulseInterval
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "0.1"
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

                // BUTTONS
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Button {
                        text: "Load Solution"
                        Layout.fillWidth: true
                        enabled: !controlsReadOnly
                        background: Rectangle {
                            color: "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            solutionFileDialog.open()
                        }
                    }

                    Button {
                        text: "Edit Solution"
                        Layout.fillWidth: true
                        enabled: controlsReadOnly && (LIFUConnector.state <4)
                        background: Rectangle {
                            color: "#2E86AB"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            uiLockedAfterSend = false
                            uiNeedsResend = true
                            LIFUConnector.makeLoadedSolutionEditable()
                            statusOverrideText = ""
                        }
                    }

                    Button {
                        text: "Send to Device"
                        Layout.fillWidth: true
                        enabled: LIFUConnector.txConnected && LIFUConnector.state !== 4 && (!controlsReadOnly || uiNeedsResend)
                        background: Rectangle {
                            color: "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            postReadyTimer.stop()
                            
                            var frequency = (1.0 / parseFloat(triggerPulseInterval.text)).toString()
                            LIFUConnector.configure_transmitter(xInput.text, yInput.text, 
                                zInput.text,  frequencyInput.text, voltage.text, triggerPulseInterval.text, triggerPulseCount.text, 
                                triggerPulseTrainInterval.text, triggerPulseTrainCount.text, durationInput.text, 
                                triggerModeDropdown.currentText);
                            LIFUConnector.generate_plot(
                                 xInput.text, yInput.text, zInput.text,
                                 frequencyInput.text, "100", frequency,
                                 "buffer"
                            );
                            uiLockedAfterSend = true
                            uiNeedsResend = false
                            statusOverrideText = ""
                        }
                    }
                }

                // Second row of buttons
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Button {
                        text: "Start"
                        Layout.fillWidth: true
                        enabled: LIFUConnector.state === 3 && !uiNeedsResend  // READY and synced
                        background: Rectangle {
                            color: "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            console.log("Starting Sonication...");
                            
                            // LIFUConnector.setAsyncMode(true)
                            LIFUConnector.start_sonication();
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
                            console.log("Stopping Sonication...");
                            LIFUConnector.stop_sonication();
                            // LIFUConnector.setAsyncMode(false)
                        }
                    }

                    Button {
                        text: "Reset"
                        Layout.fillWidth: true
                        enabled: (LIFUConnector.state > 1 && LIFUConnector.state != 4)  // CONFIGURED
                        background: Rectangle {
                            color: "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            console.log("Resetting parameters...");
                            xInput.text = "0";
                            yInput.text = "0";
                            zInput.text = "25";
                            frequencyInput.text = "400e3";
                            voltage.text = "12.0";
                            triggerPulseInterval.text = "0.1";
                            uiLockedAfterSend = false;
                            uiNeedsResend = false;
                            LIFUConnector.reset_configuration();
                        }
                    }
                }
            }
        }

        // RIGHT COLUMN (Status Panel + Graph)
        ColumnLayout {
            spacing: 20
			
            Rectangle {
                id: graphContainer
                width: 500
                height: 300
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
            
            Rectangle {
                id: messagePanel
                width: 500
                height: 130
                color: "#1E1E20"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 15

                    GroupBox {
                        title: solutionLoaded ? "Beam Focus (Delays and Apodizations Loaded Directly from Solution)" : "Beam Focus"
                        Layout.fillWidth: true

                        GridLayout {
                            columns: 6
                            width: parent.width

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
            }
			// Status Panel (Connection Indicators)
            Rectangle {
                id: statusPanel
                width: 500
                height: 150
                color: "#252525"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                Column {
                    anchors.centerIn: parent
                    spacing: 10

                    // Connection status text
                    Text {
                        id: statusText
                        text: statusOverrideText !== "" ? statusOverrideText : getSystemStateText()
                        font.pixelSize: 16
                        color: getIndicatorColor(LIFUConnector.txConnected && LIFUConnector.hvConnected)
                        horizontalAlignment: Text.AlignHCenter
                        anchors.horizontalCenter: parent.horizontalCenter
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
                        anchors.horizontalCenter: parent.horizontalCenter

                        // TX LED
                        RowLayout {
                            spacing: 5
                            // LED circle
                            Rectangle {
                                id: txIndicator
                                width: 20
                                height: 20
                                radius: 10
                                color: getIndicatorColor(LIFUConnector.txConnected)
                                border.color: "black"
                                border.width: 1
                            }
                            // Label for TX
                            Text {
                                text: "TX"
                                font.pixelSize: 16
                                color: "#BDC3C7"
                                verticalAlignment: Text.AlignVCenter
                            }
                        }

                        // HV LED
                        RowLayout {
                            spacing: 5
                            // LED circle
                            Rectangle {
                                id: hvIndicator
                                width: 20
                                height: 20
                                radius: 10
                                color: getIndicatorColor(LIFUConnector.hvConnected)
                                border.color: "black"
                                border.width: 1
                            }
                            // Label for HV
                            Text {
                                text: "HV"
                                font.pixelSize: 16
                                color: "#BDC3C7"
                                verticalAlignment: Text.AlignVCenter
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
                uiLockedAfterSend = false;
                uiNeedsResend = false;
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
            uiNeedsResend = true;
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

        function onStateChanged(state) {
            statusOverrideText = "";

            if (previousConnectorState === 4 && state !== 4) {
                if (state === 3) {
                    postReadyTimer.start();
                }
            }

            previousConnectorState = state;
        }
    }


    Component.onDestruction: {
        console.log("Closing UI, clearing LIFUConnector...");
    }
}
