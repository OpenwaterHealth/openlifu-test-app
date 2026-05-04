import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

Rectangle {
    id: vetPage
    width: parent.width
    height: parent.height
    color: "#29292B"
    radius: 20
    opacity: 0.95

    // ----- Fixed parameters (per Vet spec) -----
    readonly property string fixedX: "0"
    readonly property string fixedY: "0"
    readonly property string fixedFrequencyKHz: "400"
    readonly property string fixedPulseIntervalMs: "100"
    readonly property string fixedPulseCount: "1"
    readonly property string fixedTrainIntervalS: "0"
    readonly property string fixedTriggerMode: "Sequence"

    // ----- Dropdown choices -----
    readonly property var voltageOptions: [10, 20, 30, 40, 50]
    readonly property var dutyOptions: [5, 10, 15, 20, 25]
    readonly property var durationOptions: [
        { label: "30 sec", seconds: 30 },
        { label: "1 min",  seconds: 60 },
        { label: "2 min",  seconds: 120 },
        { label: "5 min",  seconds: 300 }
    ]
    readonly property var depthOptions: [
        { label: "3 cm", mm: 30 },
        { label: "4 cm", mm: 40 },
        { label: "5 cm", mm: 50 }
    ]

    // ----- Selection-derived values -----
    function selectedVoltage()         { return voltageOptions[voltageCombo.currentIndex] }
    function selectedDutyPercent()     { return dutyOptions[dutyCombo.currentIndex] }
    function selectedDurationSeconds() { return durationOptions[durationCombo.currentIndex].seconds }
    function selectedDurationLabel()   { return durationOptions[durationCombo.currentIndex].label }
    function selectedDepthMm()         { return depthOptions[depthCombo.currentIndex].mm }
    function selectedDepthLabel()      { return depthOptions[depthCombo.currentIndex].label }

    // pulse_interval=100 ms, pulse_count=1, pulse_train_interval=0
    //  -> SDK sets train_interval = 0.1 s, duty = pulse_duration / pulse_interval.
    function pulseDurationUs() {
        return (selectedDutyPercent() / 100.0) * 100.0 /* ms */ * 1000.0
    }
    function pulseTrainCount() {
        return Math.max(1, Math.round(selectedDurationSeconds() * 10))
    }

    // ----- State (mirrors Demo.qml's progress/status state machine) -----
    property bool everConfigured: false
    property var txTemperatures: []
    property real hvPositiveRail: NaN
    property real hvNegativeRail: NaN
    property bool hvOn: false
    property int configuredModuleCount: 0
    property int previousConnectorState: LIFUConnector.state

    property string progressState: "idle"
    property int progressCurrent: 0
    property int progressTotal: 0

    function resetProgressIdle() {
        progressState = "idle"
        progressCurrent = 0
        progressTotal = 0
    }

    function startProgressFromUi() {
        progressTotal = pulseTrainCount()
        progressCurrent = 1
        progressState = "running"
    }

    function getProgressFillFraction() {
        if (progressState === "idle") return 0
        if (progressState === "finished") return 1
        if (progressTotal <= 0) return 0
        return Math.max(0, Math.min(1, progressCurrent / progressTotal))
    }

    function formatDurationSeconds(totalSeconds) {
        var s = Math.max(0, Math.round(totalSeconds))
        if (s >= 60) {
            var m = Math.floor(s / 60)
            var rem = s % 60
            return m + "m" + (rem < 10 ? "0" : "") + rem + "s"
        }
        return s + "s"
    }

    function getProgressText() {
        if (progressState === "idle") return ""
        if (progressState === "finished") {
            var totalPulses = progressTotal * parseInt(fixedPulseCount)
            return "Finished " + totalPulses + " pulses in "
                 + formatDurationSeconds(selectedDurationSeconds())
        }
        if (progressState === "stopped") {
            var stoppedPulses = progressCurrent * parseInt(fixedPulseCount)
            var stoppedFrac = progressTotal > 0
                ? Math.max(0, Math.min(1, progressCurrent / progressTotal)) : 0
            var stoppedSec = selectedDurationSeconds() * stoppedFrac
            return "Stopped after " + stoppedPulses + " pulses in "
                 + formatDurationSeconds(stoppedSec)
        }
        if (progressTotal <= 0) return "RUNNING"
        var frac = Math.max(0, Math.min(1, progressCurrent / progressTotal))
        var percent = Math.floor(frac * 100)
        var totalSec = selectedDurationSeconds()
        var remainingSec = totalSec * (1 - frac)
        return "Running [" + percent + "%] ("
             + formatDurationSeconds(remainingSec) + " remaining)"
    }

    function getProgressColor() {
        if (progressState === "finished") return "#1f963d"
        if (progressState === "stopped")  return "#E67E22"
        if (progressState === "running")  return "#269cf6"
        return "#3A3F4B"
    }

    function getSystemStateText() {
        return LIFUConnector.state === 0 ? "Disconnected"
             : LIFUConnector.state === 1 ? "Connected"
             : LIFUConnector.state === 2 ? "Ready"
             : LIFUConnector.state === 3 ? "Running"
             : LIFUConnector.state === 4 ? "Test Script Ready"
             : "Disconnected"
    }

    function getTXIndicatorColor() {
        if (!LIFUConnector.txConnected)   return "#C0392B"  // red: disconnected
        if (LIFUConnector.state === 3)    return "#5db9ff"  // blue: sonication running
        if (LIFUConnector.state < 2)      return "#0f5d24"  // dark green: connected, not configured
        return "#269cf6"                                     // green: configured / ready
    }

    function getHVIndicatorColor() {
        if (!LIFUConnector.hvConnected)   return "#C0392B"  // red: disconnected
        if (hvOn)                          return '#5db9ff'  // blue: HV rail energized
        if (LIFUConnector.state < 2)      return "#0f5d24"  // dark green: connected, rail off
        return "#269cf6"                                     // green: connected, rail off
    }

    function getHvRailText() {
        if (!LIFUConnector.hvConnected || isNaN(hvPositiveRail) || isNaN(hvNegativeRail))
            return "Rails +--.-- / ----.-- V"
        return "Rails +" + hvPositiveRail.toFixed(2) + " / -" + Math.abs(hvNegativeRail).toFixed(2) + " V"
    }

    function getTxTemperatureText() {
        if (!LIFUConnector.txConnected) return "Temp [--.-]"
        var displayCount = Math.max(configuredModuleCount, txTemperatures.length)
        if (displayCount === 0) return "Temp [--.-]"
        var vals = []
        for (var i = 0; i < displayCount; i++) {
            var t = txTemperatures[i]
            vals.push(typeof t === "number" && !isNaN(t) ? t.toFixed(1) : "--")
        }
        return "Temp [" + vals.join(", ") + "] C"
    }

    function refreshPlot() {
        LIFUConnector.generate_plot(
            fixedX, fixedY, selectedDepthMm().toString(),
            fixedFrequencyKHz, selectedVoltage().toString(),
            fixedPulseIntervalMs, fixedPulseCount,
            fixedTrainIntervalS, pulseTrainCount().toString(),
            pulseDurationUs().toString(), "buffer"
        )
    }

    function configureNow() {
        resetProgressIdle()
        LIFUConnector.configure_transmitter(
            fixedX, fixedY, selectedDepthMm().toString(),
            fixedFrequencyKHz, selectedVoltage().toString(),
            fixedPulseIntervalMs, fixedPulseCount,
            fixedTrainIntervalS, pulseTrainCount().toString(),
            pulseDurationUs().toString(), fixedTriggerMode
        )
        if (LIFUConnector.state >= 2) {
            everConfigured = true
            configuredModuleCount = LIFUConnector.queryNumModulesConnected
        }
        refreshPlot()
    }

    function clearStatusTelemetry() {
        txTemperatures = []
        if (!hvOn) {
            hvPositiveRail = NaN
            hvNegativeRail = NaN
        }
    }

    Component.onCompleted: refreshPlot()

    // HEADER
    Text {
        id: headerText
        text: "Device Control"
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

    // Master layout: top RowLayout (3 columns) + bottom controls panel.
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        anchors.topMargin: 45
        spacing: 14

        // ===================== TOP ROW (3 columns) =====================
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 14

            // ---------- Column 1: Sonication Settings ----------
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 100   // ratio anchor
                Layout.minimumWidth: 240
                color: "#1E1E20"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 14

                    Text {
                        text: "Sonication Settings"
                        color: "white"
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        Layout.alignment: Qt.AlignHCenter
                    }

                    GridLayout {
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 12
                        Layout.fillWidth: true

                        Text { text: "Voltage:";       color: "white"; font.pixelSize: 14; Layout.preferredWidth: 110 }
                        ComboBox {
                            id: voltageCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            model: voltageOptions.map(function(v) { return v + " V" })
                            currentIndex: 0
                            enabled: LIFUConnector.state !== 3
                            background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                            onActivated: {
                                resetProgressIdle()
                                if (everConfigured) {
                                    LIFUConnector.directSetVoltage(selectedVoltage().toString())
                                    refreshPlot()
                                }
                            }
                        }

                        Text { text: "Duty Cycle:";    color: "white"; font.pixelSize: 14; Layout.preferredWidth: 110 }
                        ComboBox {
                            id: dutyCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            model: dutyOptions.map(function(v) { return v + " %" })
                            currentIndex: 0
                            enabled: LIFUConnector.state !== 3
                            background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                            onActivated: {
                                resetProgressIdle()
                                if (everConfigured) {
                                    LIFUConnector.directSetPulse(
                                        fixedX, fixedY, selectedDepthMm().toString(),
                                        fixedFrequencyKHz, selectedVoltage().toString(),
                                        fixedPulseIntervalMs, fixedPulseCount,
                                        fixedTrainIntervalS, pulseTrainCount().toString(),
                                        pulseDurationUs().toString(), fixedTriggerMode
                                    )
                                    refreshPlot()
                                }
                            }
                        }

                        Text { text: "Total Duration:"; color: "white"; font.pixelSize: 14; Layout.preferredWidth: 110 }
                        ComboBox {
                            id: durationCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            model: durationOptions.map(function(o) { return o.label })
                            currentIndex: 0
                            enabled: LIFUConnector.state !== 3
                            background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                            onActivated: {
                                resetProgressIdle()
                                if (everConfigured) {
                                    LIFUConnector.directSetSequence(
                                        fixedPulseIntervalMs, fixedPulseCount,
                                        fixedTrainIntervalS, pulseTrainCount().toString(),
                                        fixedTriggerMode
                                    )
                                    refreshPlot()
                                }
                            }
                        }

                        Text { text: "Depth:";         color: "white"; font.pixelSize: 14; Layout.preferredWidth: 110 }
                        ComboBox {
                            id: depthCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            model: depthOptions.map(function(o) { return o.label })
                            currentIndex: 0
                            enabled: LIFUConnector.state !== 3
                            background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                            onActivated: {
                                resetProgressIdle()
                                if (everConfigured) {
                                    LIFUConnector.directSetPulse(
                                        fixedX, fixedY, selectedDepthMm().toString(),
                                        fixedFrequencyKHz, selectedVoltage().toString(),
                                        fixedPulseIntervalMs, fixedPulseCount,
                                        fixedTrainIntervalS, pulseTrainCount().toString(),
                                        pulseDurationUs().toString(), fixedTriggerMode
                                    )
                                }
                                refreshPlot()
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // ---------- Column 2: Read-only solution readouts ----------
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 100   // ratio anchor
                Layout.minimumWidth: 240
                color: "#1E1E20"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    Text {
                        text: "Output Parameters"
                        color: "white"
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        Layout.alignment: Qt.AlignHCenter
                    }

                    GridLayout {
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 8
                        Layout.fillWidth: true

                        // Helper rows: label + read-only value text
                        Text { text: "Voltage:";          color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: selectedVoltage() + " V"; color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                        Text { text: "Duty Cycle:";       color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: selectedDutyPercent() + " %"; color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                        Text { text: "Total Duration:";   color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: selectedDurationLabel(); color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                        Text { text: "Depth:";            color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: selectedDepthLabel(); color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                        Rectangle {
                            Layout.columnSpan: 2
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            Layout.topMargin: 4
                            Layout.bottomMargin: 4
                            color: "#3E4E6F"
                        }

                        Text { text: "Frequency:";        color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: fixedFrequencyKHz + " kHz"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                        Text { text: "Focus (X, Y, Z):";  color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: "(" + fixedX + ", " + fixedY + ", " + selectedDepthMm() + ") mm"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                        Text { text: "Pulse Length:";   color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: (pulseDurationUs() / 1000.0).toFixed(1) + " ms"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                        Text { text: "Pulse Repetition Interval:";   color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: fixedPulseIntervalMs + " ms"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                        Text { text: "Pulse Trains:";     color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: pulseTrainCount();   color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // ---------- Column 3: Plot (2x wide) ----------
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 200   // 2x the others -> 1:1:2 ratio
                Layout.minimumWidth: 320
                color: "#1E1E20"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                Image {
                    id: vetPlotImage
                    anchors.fill: parent
                    anchors.margins: 10
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false
                    // Constrain the implicit size so a large base64 PNG
                    // doesn't propagate huge implicit dimensions back up
                    // through the Layout chain (which was driving the
                    // window into negative-coord geometry resizes).
                    sourceSize.width: width
                    sourceSize.height: height
                    source: "../assets/images/empty_graph.png"

                    function updateImage(base64data) {
                        if (!base64data || base64data === "") return
                        if (base64data.startsWith("data:image") || base64data.startsWith("file:") || base64data.startsWith("qrc:") || base64data.startsWith("/") || base64data.startsWith("../") || base64data.startsWith("./")) {
                            source = base64data
                        } else {
                            // Raw base64 PNG payload from generate_ultrasound_plot;
                            // wrap in a data URI so QML doesn't resolve it as a file path.
                            source = "data:image/png;base64," + base64data
                        }
                    }
                }

                Button {
                    id: vetRefreshButton
                    text: "\u21bb Refresh"
                    font.pixelSize: 13
                    width: 120
                    height: 44
                    anchors.bottom: parent.bottom
                    anchors.right: parent.right
                    anchors.margins: 8
                    background: Rectangle {
                        color: vetRefreshButton.down ? "#2F333D" : "#3A3F4B"
                        radius: 4
                        border.color: "#BDC3C7"
                        opacity: 0.85
                    }
                    onClicked: refreshPlot()
                }
            }
        }

        // ===================== BOTTOM CONTROLS BAR =====================
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 180
            color: "#252525"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 10

                // Status row: state text + connection indicators
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16

                    Text {
                        text: "System State: " + getSystemStateText()
                        color: getTXIndicatorColor()
                        font.pixelSize: 14
                        Layout.alignment: Qt.AlignVCenter
                    }

                    Item { Layout.fillWidth: true }

                    RowLayout {
                        spacing: 6
                        Rectangle { width: 18; height: 18; radius: 9; color: getTXIndicatorColor(); border.color: "black"; border.width: 1 }
                        Text { text: "TX"; color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: getTxTemperatureText(); color: "#9FB3C8"; font.pixelSize: 12 }
                    }

                    RowLayout {
                        spacing: 6
                        Rectangle { width: 18; height: 18; radius: 9; color: getHVIndicatorColor(); border.color: "black"; border.width: 1 }
                        Text { text: "HV"; color: "#BDC3C7"; font.pixelSize: 13 }
                        Text { text: getHvRailText(); color: "#9FB3C8"; font.pixelSize: 12 }
                    }
                }

                // Big progress bar
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 44
                    radius: 6
                    color: "#1B1D22"
                    border.color: "#3E4E6F"
                    border.width: 1

                    Rectangle {
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        anchors.margins: 3
                        width: Math.max(0, (parent.width - 6) * getProgressFillFraction())
                        radius: 4
                        color: getProgressColor()
                        Behavior on width { NumberAnimation { duration: 120 } }
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }

                    Text {
                        anchors.fill: parent
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: getProgressText()
                        font.pixelSize: 18
                        font.weight: Font.Bold
                        color: "white"
                        style: Text.Outline
                        styleColor: "#000000"
                    }
                }

                // Big control buttons row.
                // Before configuration: a single big "Program Device" button.
                // After configuration: Start + Stop become visible (Reset
                // happens automatically when the user navigates to Settings).
                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 14

                    Button {
                        id: vetProgramButton
                        text: "Program Device"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        font.pixelSize: 22
                        font.weight: Font.Bold
                        visible: LIFUConnector.state < 2
                        enabled: LIFUConnector.txConnected && LIFUConnector.state < 2
                        background: Rectangle {
                            color: vetProgramButton.down ? "#2F333D" : "#3A3F4B"
                            radius: 6
                            border.color: "#BDC3C7"
                            border.width: 2
                        }
                        contentItem: Text {
                            text: vetProgramButton.text
                            color: vetProgramButton.enabled ? "white" : "#888"
                            font: vetProgramButton.font
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        onClicked: configureNow()
                    }

                    Button {
                        id: vetStartButton
                        text: "Start"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        font.pixelSize: 22
                        font.weight: Font.Bold
                        visible: LIFUConnector.state >= 2
                        enabled: LIFUConnector.state === 2
                                 && LIFUConnector.hvConnected
                                 && LIFUConnector.hvEnableMode !== 2
                        background: Rectangle {
                            color: !vetStartButton.enabled ? "#2A4030"
                                 : vetStartButton.down    ? "#157031"
                                 : "#1f963d"
                            radius: 6
                            border.color: "#0E5A23"
                            border.width: 2
                        }
                        contentItem: Text {
                            text: vetStartButton.text
                            color: vetStartButton.enabled ? "white" : "#9CB8A4"
                            font: vetStartButton.font
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        onClicked: {
                            startProgressFromUi()
                            LIFUConnector.start_sonication()
                        }
                    }

                    Button {
                        id: vetStopButton
                        text: "Stop"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        font.pixelSize: 22
                        font.weight: Font.Bold
                        visible: LIFUConnector.state >= 2
                        enabled: LIFUConnector.state === 3
                        background: Rectangle {
                            color: !vetStopButton.enabled ? "#3A2424"
                                 : vetStopButton.down    ? "#8E1F1F"
                                 : "#C0392B"
                            radius: 6
                            border.color: "#7A1F1F"
                            border.width: 2
                        }
                        contentItem: Text {
                            text: vetStopButton.text
                            color: vetStopButton.enabled ? "white" : "#B89A9A"
                            font: vetStopButton.font
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        onClicked: {
                            if (progressState === "running") progressState = "stopped"
                            clearStatusTelemetry()
                            LIFUConnector.stop_sonication()
                        }
                    }
                }
            }
        }
    }

    Connections {
        target: LIFUConnector

        function onPlotGenerated(imageData) {
            if (vetPlotImage) vetPlotImage.updateImage(imageData)
        }

        function onTemperatureTxUpdated(module, tx_temp, amb_temp) {
            var arr = txTemperatures.slice()
            while (arr.length <= module) arr.push(NaN)
            arr[module] = tx_temp
            txTemperatures = arr
        }

        function onSonicationProgressUpdated(pt_curr, pt_total, p_curr, p_total) {
            if (progressState !== "running") return
            if (pt_total > 0 && pt_curr >= pt_total) {
                progressCurrent = progressTotal > 0 ? progressTotal : pt_curr
                progressState = "finished"
                return
            }
            var next = pt_curr + 1
            if (progressTotal > 0 && next > progressTotal) next = progressTotal
            progressCurrent = next
        }

        function onMonVoltagesReceived(voltages) {
            if (voltages.length >= 4) {
                hvPositiveRail = voltages[0].converted_voltage
                hvNegativeRail = voltages[3].converted_voltage
            }
        }

        function onPowerStatusReceived(v12_state, hv_state) {
            hvOn = hv_state
            if (!hv_state) {
                hvPositiveRail = NaN
                hvNegativeRail = NaN
            }
        }

        function onStateChanged(state) {
            if (previousConnectorState === 3 && state !== 3) {
                clearStatusTelemetry()
            }
            if (state >= 2) {
                if (configuredModuleCount <= 0) {
                    configuredModuleCount = LIFUConnector.queryNumModulesConnected
                }
                everConfigured = true
            } else {
                everConfigured = false
                if (previousConnectorState >= 2) {
                    clearStatusTelemetry()
                    // State dropped below READY (e.g. reset triggered by
                    // navigating to Settings). Clear the progress UI so the
                    // user must re-program before restarting.
                    resetProgressIdle()
                    vetPlotImage.source = "../assets/images/empty_graph.png"
                }
            }
            previousConnectorState = state
        }
    }
}
