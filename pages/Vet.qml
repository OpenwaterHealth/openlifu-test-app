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
    readonly property string fixedTriggerMode: "Sequence"

    // ----- Preset definitions -----
    // Loaded at component-completed time from preset_vet_settings/ via
    // LIFUConnector.getVetPresets(). Each entry carries the full
    // sonication parameter dictionary plus the per-preset analysis
    // (MI/TIS/ISPPA/ISTPA/PNP) and the file URL of the matching
    // intensity_plot PNG. UI-facing units: voltage [V], frequency [kHz],
    // pulse length [us], pulse interval [ms], pulse train interval [s],
    // depth [mm].
    property var presetOptions: []

    readonly property var durationOptions: [
        { label: "10 min", secdons: 600 },
        { label: "5 min",  seconds: 300 },
        { label: "2 min",  seconds: 120 },
        { label: "1 min",  seconds: 60 },
        { label: "30 sec", seconds: 30 }
    ]

    // ----- Selection-derived values -----
    readonly property var emptyPreset: ({
        id: "",
        label: "",
        voltage: 0,
        frequency_khz: 0,
        pulse_length_us: 0,
        pulse_interval_ms: 0,
        pulse_count: 1,
        pulse_train_interval_s: 0,
        depth_mm: 0,
        analysis: ({}),
        intensityPlotUrl: ""
    })
    function selectedPreset() {
        if (!presetOptions || presetOptions.length === 0) return emptyPreset
        var idx = presetCombo.currentIndex
        if (idx < 0 || idx >= presetOptions.length) idx = 0
        return presetOptions[idx]
    }
    function selectedPresetId()        { return selectedPreset().id }
    function selectedPresetLabel()     { return selectedPreset().label }
    function selectedVoltage()         { return selectedPreset().voltage }
    function selectedFrequencyKHz()    { return selectedPreset().frequency_khz }
    function selectedPulseLengthUs()   { return selectedPreset().pulse_length_us }
    function selectedPulseIntervalMs() { return selectedPreset().pulse_interval_ms }
    function selectedPulseCount()      { return selectedPreset().pulse_count }
    function selectedTrainIntervalS()  { return selectedPreset().pulse_train_interval_s }
    function selectedDepthMm()         { return selectedPreset().depth_mm }
    function selectedDepthLabel()      { return selectedDepthMm() + " mm" }
    function selectedAnalysis()        { return selectedPreset().analysis || ({}) }
    function selectedIntensityPlotUrl(){ return selectedPreset().intensityPlotUrl || "" }
    function formatAnalysis(key, digits) {
        var v = selectedAnalysis()[key]
        if (typeof v !== "number" || isNaN(v)) return "--"
        return v.toFixed(digits)
    }
    function selectedDurationSeconds() { return durationOptions[durationCombo.currentIndex].seconds }
    function selectedDurationLabel()   { return durationOptions[durationCombo.currentIndex].label }

    function pulseDurationUs() { return selectedPulseLengthUs() }
    function pulseTrainCount() {
        // Train period (when pulse_train_interval_s = 0 the SDK uses
        // pulse_count * pulse_interval). Number of trains is duration
        // divided by that period.
        var trainPeriodS = selectedTrainIntervalS()
        if (trainPeriodS <= 0) {
            trainPeriodS = selectedPulseCount() * selectedPulseIntervalMs() / 1000.0
        }
        if (trainPeriodS <= 0) return 1
        return Math.max(1, Math.round(selectedDurationSeconds() / trainPeriodS))
    }

    // ----- State (mirrors Demo.qml's progress/status state machine) -----
    property bool everConfigured: false
    property var txTemperatures: []
    property real hvPositiveRail: NaN
    property real hvNegativeRail: NaN
    property bool hvOn: false
    property int configuredModuleCount: 0
    property int previousConnectorState: LIFUConnector.state

    // The thermal/cooldown state machine, the run/pause/resume tracker,
    // and the per-block snapshot logic used to live here as QML
    // properties + on-temperature handlers. They have all moved into
    // ``LIFUConnector`` so the policy lives next to the device commands
    // (and so QML bindings don't re-evaluate this logic on every tick).
    // The page now reads progress via LIFUConnector.runState /
    // .runOverallFraction / etc. and reacts to coolingStateChanged /
    // thermalShutdownEvent signals below.

    function formatDurationSeconds(totalSeconds) {
        var s = Math.max(0, Math.round(totalSeconds))
        if (s >= 60) {
            var m = Math.floor(s / 60)
            var rem = s % 60
            return m + "m" + (rem < 10 ? "0" : "") + rem + "s"
        }
        return s + "s"
    }

    function getProgressFillFraction() {
        return LIFUConnector.runOverallFraction
    }

    function blocksClause() {
        var blocks = LIFUConnector.runBlockCount
        if (blocks <= 1) return "."
        return ", applied in " + blocks + " blocks over "
             + formatDurationSeconds(LIFUConnector.runElapsedSeconds) + "."
    }

    function getProgressText() {
        var rs = LIFUConnector.runState
        var origTotal = LIFUConnector.runOriginalTrainTotal
        var origPulses = LIFUConnector.runOriginalPulseCount
        var origDur = LIFUConnector.runOriginalDurationS
        var frac = LIFUConnector.runOverallFraction

        if (rs === "idle") return ""
        if (rs === "finished") {
            var totalPulses = origTotal * origPulses
            return "Finished successfully after " + totalPulses + " pulses ("
                 + formatDurationSeconds(origDur) + ")"
                 + blocksClause()
        }
        if (rs === "aborted") {
            var deliveredTrains = LIFUConnector.runOverallDeliveredTrains
            var deliveredPulses = deliveredTrains * origPulses
            var deliveredSec = origDur * frac
            return "Aborted early after " + deliveredPulses + " pulses ("
                 + formatDurationSeconds(deliveredSec) + ")"
                 + blocksClause()
        }
        if (rs === "paused") {
            var pausedPercent = Math.floor(frac * 100)
            var pausedRem = origDur * (1 - frac)
            return "Paused, " + pausedPercent + "% Complete. "
                 + formatDurationSeconds(pausedRem) + " remaining"
        }
        if (origTotal <= 0) return "RUNNING"
        var percent = Math.floor(frac * 100)
        var remainingSec = origDur * (1 - frac)
        return "Running [" + percent + "%] ("
             + formatDurationSeconds(remainingSec) + " remaining)"
    }

    function getProgressColor() {
        var rs = LIFUConnector.runState
        if (rs === "finished") return "#1f963d"
        if (rs === "aborted")  return "#E67E22"
        if (rs === "paused")   return "#F1C40F"
        if (rs === "running")  return "#269cf6"
        return "#3A3F4B"
    }

    function getSystemStateText() {
        if (LIFUConnector.state === 3) return "Running"
        if (LIFUConnector.state === 2) return LIFUConnector.coolingDown ? "Cooling Down" : "Ready"
        if (LIFUConnector.state === 0) return "Disconnected"
        if (LIFUConnector.state === 1) return "Connected"
        if (LIFUConnector.state === 4) return "Test Script Ready"
        return "Disconnected"
    }

    function getSystemStateColor() {
        if (!LIFUConnector.txConnected) return "#C0392B"
        if (LIFUConnector.state === 3)   return "#5db9ff"  // running
        if (LIFUConnector.state === 2 && LIFUConnector.coolingDown) return "#3498DB"  // cooling
        if (LIFUConnector.state < 2)     return "#0f5d24"
        return "#269cf6"
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
        // Each preset ships with a pre-rendered intensity plot PNG; we
        // simply point the Image at it instead of re-rendering on the
        // Python side every time the user changes a control.
        if (vetPlotImage) vetPlotImage.updateImage(selectedIntensityPlotUrl())
    }

    function applyActivePresetToConnector() {
        var pid = selectedPresetId()
        if (pid && pid !== "")
            LIFUConnector.setActiveVetPreset(pid)
        else
            LIFUConnector.clearActiveVetPreset()
    }

    function configureNow() {
        // configure_transmitter() snapshots its args internally and
        // resets the run-progress state machine, so we don't need to
        // mirror that here. Push the active preset's delays/apodizations
        // into the connector first so get_solution() picks them up.
        applyActivePresetToConnector()
        LIFUConnector.configure_transmitter(
            fixedX, fixedY, selectedDepthMm().toString(),
            selectedFrequencyKHz().toString(), selectedVoltage().toString(),
            selectedPulseIntervalMs().toString(), selectedPulseCount().toString(),
            selectedTrainIntervalS().toString(), pulseTrainCount().toString(),
            pulseDurationUs().toString(), fixedTriggerMode
        )
        if (LIFUConnector.state >= 2) {
            everConfigured = true
            configuredModuleCount = LIFUConnector.queryNumModulesConnected
        }
    }

    function clearStatusTelemetry() {
        txTemperatures = []
        if (!hvOn) {
            hvPositiveRail = NaN
            hvNegativeRail = NaN
        }
    }

    Component.onCompleted: {
        presetOptions = LIFUConnector.getVetPresets()
        if (presetCombo.currentIndex < 0 && presetOptions.length > 0)
            presetCombo.currentIndex = 0
        applyActivePresetToConnector()
        refreshPlot()
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

            // ---------- Column 1: Sonication Settings (presets) +
            // collapsible Output Parameters readout ----------
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 100   // ratio anchor
                Layout.minimumWidth: 260
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

                        Text { text: "Preset:";        color: "white"; font.pixelSize: 14; Layout.preferredWidth: 110 }
                        ComboBox {
                            id: presetCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            model: presetOptions.map(function(p) { return p.label })
                            currentIndex: 0
                            enabled: LIFUConnector.state !== 3
                            background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                            onActivated: {
                                LIFUConnector.clear_run_progress()
                                applyActivePresetToConnector()
                                if (everConfigured) {
                                    // Preset switch changes voltage, depth,
                                    // and pulse parameters, so push the
                                    // full pulse update + voltage to HV.
                                    LIFUConnector.directSetVoltage(selectedVoltage().toString())
                                    LIFUConnector.directSetPulse(
                                        fixedX, fixedY, selectedDepthMm().toString(),
                                        selectedFrequencyKHz().toString(), selectedVoltage().toString(),
                                        selectedPulseIntervalMs().toString(), selectedPulseCount().toString(),
                                        selectedTrainIntervalS().toString(), pulseTrainCount().toString(),
                                        pulseDurationUs().toString(), fixedTriggerMode
                                    )
                                }
                                refreshPlot()
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
                                LIFUConnector.clear_run_progress()
                                if (everConfigured) {
                                    LIFUConnector.directSetSequence(
                                        selectedPulseIntervalMs().toString(),
                                        selectedPulseCount().toString(),
                                        selectedTrainIntervalS().toString(),
                                        pulseTrainCount().toString(),
                                        fixedTriggerMode
                                    )
                                }
                            }
                        }
                    }

                    // Collapsible Output Parameters section.
                    Item {
                        id: outputParamsSection
                        property bool expanded: false
                        Layout.fillWidth: true
                        Layout.preferredHeight: outputParamsHeader.height
                                              + (expanded ? outputParamsBody.implicitHeight + 6 : 0)
                        Behavior on Layout.preferredHeight { NumberAnimation { duration: 150 } }
                        clip: true

                        Rectangle {
                            id: outputParamsHeader
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            height: 30
                            color: outputParamsMA.containsMouse ? "#2A2F3A" : "#222732"
                            radius: 4
                            border.color: "#3E4E6F"
                            border.width: 1

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 8
                                spacing: 6

                                Text {
                                    text: outputParamsSection.expanded ? "\u25BC" : "\u25B6"
                                    color: "#9FB3C8"
                                    font.pixelSize: 11
                                }
                                Text {
                                    text: "Output Parameters"
                                    color: "white"
                                    font.pixelSize: 13
                                    font.weight: Font.Bold
                                    Layout.fillWidth: true
                                }
                            }

                            MouseArea {
                                id: outputParamsMA
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: outputParamsSection.expanded = !outputParamsSection.expanded
                            }
                        }

                        GridLayout {
                            id: outputParamsBody
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: outputParamsHeader.bottom
                            anchors.topMargin: 6
                            columns: 2
                            columnSpacing: 12
                            rowSpacing: 6
                            visible: outputParamsSection.expanded

                            Text { text: "Voltage:";          color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: selectedVoltage() + " V"; color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "Pulse Length:";     color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: (selectedPulseLengthUs() / 1000.0).toFixed(1) + " ms"; color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "Total Duration:";   color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: selectedDurationLabel(); color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "Focal Depth (from Transducer Face):";            color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: selectedDepthLabel(); color: "#43BB57"; font.pixelSize: 13; font.family: "Consolas" }

                            Rectangle {
                                Layout.columnSpan: 2
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                Layout.topMargin: 4
                                Layout.bottomMargin: 4
                                color: "#3E4E6F"
                            }

                            // Per-preset acoustic analysis values, loaded from
                            // <preset_id>_settings.json's "analysis" section.
                            Text { text: "MI:";              color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: formatAnalysis("MI", 3);                       color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "TIS:";             color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: formatAnalysis("TIS", 4);                      color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "ISPPA:";           color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: formatAnalysis("ISPPA (W/cm2)", 2) + " W/cm\u00b2";   color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "ISTPA:";           color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: formatAnalysis("ISTPA (mW/cm2)", 1) + " mW/cm\u00b2"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "PNP:";             color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: formatAnalysis("PNP (kPa)", 1) + " kPa";       color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // ---------- Column 2: Plot (2x wide) ----------
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

                // Status row: state text + temperature + HV rails.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16

                    Text {
                        text: "System State: " + getSystemStateText()
                        color: getSystemStateColor()
                        font.pixelSize: 14
                        Layout.alignment: Qt.AlignVCenter
                    }

                    Item { Layout.fillWidth: true }

                    Text { text: getTxTemperatureText(); color: "#9FB3C8"; font.pixelSize: 12 }
                    Text { text: getHvRailText();       color: "#9FB3C8"; font.pixelSize: 12 }
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
                        text: LIFUConnector.runState === "paused" ? "Resume" : "Start"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        font.pixelSize: 22
                        font.weight: Font.Bold
                        visible: LIFUConnector.state >= 2
                        enabled: LIFUConnector.state === 2
                                 && !LIFUConnector.coolingDown
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
                            if (LIFUConnector.runState === "paused") {
                                LIFUConnector.resume_sonication()
                            } else {
                                LIFUConnector.begin_run_progress()
                                LIFUConnector.start_sonication()
                            }
                        }
                    }

                    Button {
                        id: vetStopButton
                        text: LIFUConnector.runState === "paused" ? "Abort" : "Stop"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        font.pixelSize: 22
                        font.weight: Font.Bold
                        visible: LIFUConnector.state >= 2
                        enabled: LIFUConnector.state === 3 || LIFUConnector.runState === "paused"
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
                            if (LIFUConnector.runState === "paused") {
                                LIFUConnector.abort_sonication()
                            } else if (LIFUConnector.runState === "running") {
                                LIFUConnector.pause_sonication()
                            }
                        }
                    }
                }
            }
        }
    }

    // Local thermal-shutdown popup. Distinct from the global device-error
    // dialog so the message is specific to the Vet page's safety logic.
    Dialog {
        id: thermalShutdownDialog
        modal: true
        focus: true
        title: "Thermal Shutdown"
        width: 480
        anchors.centerIn: parent

        property real observedTemp: 0

        background: Rectangle {
            color: "#1E1E20"
            border.color: "#7A2E2E"
            border.width: 2
            radius: 8
        }

        contentItem: ColumnLayout {
            spacing: 10
            Text {
                text: "The transducer has entered thermal shutdown."
                color: "#F5B5B5"
                font.pixelSize: 15
                font.bold: true
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            Text {
                text: "Measured TX temperature " + thermalShutdownDialog.observedTemp.toFixed(1)
                      + " \u00b0C exceeded the " + LIFUConnector.shutdownThresholdC.toFixed(0)
                      + " \u00b0C limit. Sonication has been stopped and HV turned off. "
                      + "Wait for the device to cool below " + LIFUConnector.coolingThresholdC.toFixed(0)
                      + " \u00b0C before resuming."
                color: "#FFD3D3"
                font.pixelSize: 13
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }

        footer: RowLayout {
            spacing: 10
            Item { Layout.fillWidth: true }
            Button { text: "OK"; onClicked: thermalShutdownDialog.close() }
            Item { Layout.preferredWidth: 10 }
        }
    }

    Connections {
        target: LIFUConnector

        function onTemperatureTxUpdated(module, tx_temp, amb_temp) {
            // The connector evaluates cooldown / shutdown internally;
            // here we only need to keep the displayed temperature list
            // in sync.
            var arr = txTemperatures.slice()
            while (arr.length <= module) arr.push(NaN)
            arr[module] = tx_temp
            txTemperatures = arr
        }

        function onThermalShutdownEvent(observedTemp, shutdownC, coolingC) {
            thermalShutdownDialog.observedTemp = observedTemp
            thermalShutdownDialog.open()
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
                // Connector resets its own cooling latch on cooldown
                // exit; nothing to clear here.
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
                    // State dropped below READY (e.g. reset triggered
                    // by navigating to Settings). Connector resets the
                    // run-progress state machine on its end; clear the
                    // plot here.
                    vetPlotImage.source = "../assets/images/empty_graph.png"
                }
            }
            previousConnectorState = state
        }
    }
}

