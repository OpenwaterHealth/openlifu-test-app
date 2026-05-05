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
    // Each preset carries the *full* sonication parameter dictionary so
    // tweaking one preset later (e.g. raising voltage for Hip) doesn't
    // require touching the rest of the page. All times are in their UI-
    // facing units: voltage [V], frequency [kHz], pulse length [us],
    // pulse interval [ms], pulse train interval [s], depth [mm].
    readonly property var presetOptions: [
        {
            label: "Knee",
            voltage: 20,
            frequency_khz: 400,
            pulse_length_us: 20000,    // 20 ms
            pulse_interval_ms: 100,
            pulse_count: 1,
            pulse_train_interval_s: 0, // 0 -> SDK derives from pulse_count*pulse_interval
            depth_mm: 30
        },
        {
            label: "Hip",
            voltage: 20,
            frequency_khz: 400,
            pulse_length_us: 20000,
            pulse_interval_ms: 100,
            pulse_count: 1,
            pulse_train_interval_s: 0,
            depth_mm: 40
        },
        {
            label: "Spine",
            voltage: 20,
            frequency_khz: 400,
            pulse_length_us: 20000,
            pulse_interval_ms: 100,
            pulse_count: 1,
            pulse_train_interval_s: 0,
            depth_mm: 40
        }
    ]

    readonly property var durationOptions: [
        { label: "30 sec", seconds: 30 },
        { label: "1 min",  seconds: 60 },
        { label: "2 min",  seconds: 120 },
        { label: "5 min",  seconds: 300 }
    ]

    // ----- Selection-derived values -----
    function selectedPreset()          { return presetOptions[presetCombo.currentIndex] }
    function selectedPresetLabel()     { return selectedPreset().label }
    function selectedVoltage()         { return selectedPreset().voltage }
    function selectedFrequencyKHz()    { return selectedPreset().frequency_khz }
    function selectedPulseLengthUs()   { return selectedPreset().pulse_length_us }
    function selectedPulseIntervalMs() { return selectedPreset().pulse_interval_ms }
    function selectedPulseCount()      { return selectedPreset().pulse_count }
    function selectedTrainIntervalS()  { return selectedPreset().pulse_train_interval_s }
    function selectedDepthMm()         { return selectedPreset().depth_mm }
    function selectedDepthLabel()      { return selectedDepthMm() + " mm" }
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

    property string progressState: "idle"
    property int progressCurrent: 0
    property int progressTotal: 0

    // ----- Multi-block (pause/resume) tracking -----
    // The original (pre-pause) sonication parameters are snapshotted at
    // each fresh Start so that on Pause we can reconfigure the device
    // with a shorter trainCount covering only the *remaining* portion,
    // while the UI keeps reporting progress against the original total.
    // On Abort or natural completion we restore the originally-loaded
    // solution so the next run starts from the user's selections again.
    property int originalTrainTotal: 0
    property real originalDurationS: 0
    property int originalPulseCount: 0
    property int trainsDeliveredBeforeBlock: 0
    property int blockCount: 0
    property real runStartTimeMs: 0
    property real runElapsedMs: 0
    // null when no run has been started yet, otherwise the exact arg
    // tuple last passed to configure_transmitter for the user's selected
    // sequence (used to restore after pause-shortened reconfigures).
    property var originalConfigArgs: null

    // ----- Thermal management -----
    // While the TX temperature is above ``coolingThreshold`` the page
    // surfaces a "Cooling Down" status and inhibits Start. At or above
    // ``shutdownThreshold`` we abort sonication, force HV off, and pop up
    // a thermal-shutdown notice (one-shot, re-armed on cooldown).
    readonly property real coolingThreshold: 50.0
    readonly property real shutdownThreshold: 75.0
    property bool coolingDown: false
    property bool thermalShutdownTriggered: false
    // Stashed HV enable mode at the moment we forced HV off for cooldown,
    // so we can restore it once the device is back below the cool
    // threshold. -1 means "nothing to restore".
    property int preCooldownHvMode: -1

    function maxTxTemperature() {
        var maxT = NaN
        for (var i = 0; i < txTemperatures.length; i++) {
            var t = txTemperatures[i]
            if (typeof t === "number" && !isNaN(t)) {
                if (isNaN(maxT) || t > maxT) maxT = t
            }
        }
        return maxT
    }

    function evaluateThermalState() {
        var t = maxTxTemperature()
        if (isNaN(t)) {
            // No telemetry yet -- don't latch a cooling-down state on
            // boot or before the first STATUS frame arrives.
            coolingDown = false
            return
        }

        // While sonication is in progress only the >=75C hard-shutdown
        // path is allowed to act on temperature. Crossing 50C mid-run
        // must NOT flip the device into cooldown (which would try to
        // toggle HV enable mode -- something the connector rejects with
        // a "cannot change HV enable state while running" warning -- or
        // otherwise interfere with an active sonication). The cooldown
        // transition is evaluated normally as soon as the run ends.
        if (LIFUConnector.state === 3) {
            if (t >= shutdownThreshold) {
                triggerThermalShutdown(t)
            }
            return
        }

        var wasCooling = coolingDown
        var nowCooling = (t > coolingThreshold)

        // >=75C is a hard shutdown: aborts sonication, drops HV, pops
        // dialog. Force cooling true so the transition logic below also
        // fires (HV-off is already handled inside the shutdown helper,
        // but keeping the path consistent is clearer).
        if (t >= shutdownThreshold) {
            triggerThermalShutdown(t)
            nowCooling = true
        }

        if (nowCooling && !wasCooling) {
            // Just entered cooldown -- whether from a successful run
            // ending hot, or from a >75C abort. Force HV off so the rail
            // doesn't sit energized while the operator waits (or walks
            // away). Stash the previous mode so we can restore it once
            // the device cools back down.
            if (LIFUConnector.hvEnableMode !== 2) {
                preCooldownHvMode = LIFUConnector.hvEnableMode
                LIFUConnector.setHvEnableMode(2)
            }
        }

        if (!nowCooling && wasCooling) {
            // Just dropped below the cool threshold. Re-arm the
            // shutdown one-shot and restore the HV mode that was active
            // before we forced it off. We intentionally leave the
            // connector in its current (configured) state so the
            // operator returns straight to Ready with Start enabled --
            // no need to Program Device again. The progress bar's last
            // "Finished"/"Stopped" message stays visible until the next
            // run starts.
            thermalShutdownTriggered = false
            if (preCooldownHvMode >= 0 && preCooldownHvMode !== 2) {
                LIFUConnector.setHvEnableMode(preCooldownHvMode)
            }
            preCooldownHvMode = -1
        }

        coolingDown = nowCooling
    }

    function triggerThermalShutdown(observedTemp) {
        if (thermalShutdownTriggered) return
        thermalShutdownTriggered = true
        // Skip the paused intermediate state -- thermal shutdown is a
        // hard abort regardless of whether we were running or paused.
        if (progressState === "running" || progressState === "paused") {
            abortRun()
        }
        if (LIFUConnector.state === 3) {
            LIFUConnector.stop_sonication()
        }
        // Force HV off regardless of current enable mode. Stash the
        // previous mode (if we haven't already) so cooldown-exit can
        // restore it -- otherwise Start stays disabled after the device
        // cools back down.
        if (LIFUConnector.hvEnableMode !== 2 && preCooldownHvMode < 0) {
            preCooldownHvMode = LIFUConnector.hvEnableMode
        }
        LIFUConnector.setHvEnableMode(2)
        thermalShutdownDialog.observedTemp = observedTemp
        thermalShutdownDialog.open()
    }

    function resetProgressIdle() {
        progressState = "idle"
        progressCurrent = 0
        progressTotal = 0
        originalTrainTotal = 0
        originalDurationS = 0
        originalPulseCount = 0
        trainsDeliveredBeforeBlock = 0
        blockCount = 0
        runStartTimeMs = 0
        runElapsedMs = 0
    }

    function startProgressFromUi() {
        // Snapshot the user's selections so progress / final-message
        // arithmetic uses the *original* sequence even after a Pause/
        // Resume reconfigures the device with a shorter trainCount.
        originalTrainTotal = pulseTrainCount()
        originalDurationS = selectedDurationSeconds()
        originalPulseCount = selectedPulseCount()
        trainsDeliveredBeforeBlock = 0
        blockCount = 1
        runStartTimeMs = Date.now()
        runElapsedMs = 0
        progressTotal = originalTrainTotal
        progressCurrent = 1
        progressState = "running"
    }

    function pauseRun() {
        // Move whatever the current block has delivered into the
        // running-total counter so overall % keeps advancing across
        // blocks. progressCurrent gets re-seeded to 0 so the next
        // block starts cleanly.
        var deliveredThisBlock = Math.max(0, Math.min(progressCurrent, progressTotal))
        trainsDeliveredBeforeBlock += deliveredThisBlock
        progressCurrent = 0
        progressTotal = 0
        progressState = "paused"
        LIFUConnector.stop_sonication()
    }

    function resumeRun() {
        var remaining = originalTrainTotal - trainsDeliveredBeforeBlock
        if (remaining <= 0) {
            // Nothing left to do -- treat as natural completion.
            progressState = "finished"
            runElapsedMs = Date.now() - runStartTimeMs
            restoreOriginalConfig()
            return
        }
        // Reconfigure the device with a shortened trainCount covering
        // just the remaining portion. originalConfigArgs is the args
        // tuple that was loaded for the full sequence.
        if (originalConfigArgs) {
            var a = originalConfigArgs
            LIFUConnector.configure_transmitter(
                a.x, a.y, a.z, a.freq, a.voltage,
                a.pulseInterval, a.pulseCount, a.trainInterval,
                remaining.toString(), a.durationUs, a.mode
            )
        }
        progressTotal = remaining
        progressCurrent = 0
        blockCount += 1
        progressState = "running"
        LIFUConnector.start_sonication()
    }

    function abortRun() {
        // Settles the abort message based on whatever has been
        // delivered (across all blocks) and restores the original
        // sequence for the next run. Safe to call from either the
        // running or paused state.
        if (progressState === "running") {
            var deliveredThisBlock = Math.max(0, Math.min(progressCurrent, progressTotal))
            trainsDeliveredBeforeBlock += deliveredThisBlock
            if (LIFUConnector.state === 3) LIFUConnector.stop_sonication()
        }
        progressCurrent = 0
        progressTotal = 0
        if (runStartTimeMs > 0 && runElapsedMs <= 0) {
            runElapsedMs = Date.now() - runStartTimeMs
        }
        progressState = "aborted"
        clearStatusTelemetry()
        restoreOriginalConfig()
    }

    function restoreOriginalConfig() {
        if (!originalConfigArgs) return
        if (LIFUConnector.state === 3) return  // wait until READY
        var a = originalConfigArgs
        LIFUConnector.configure_transmitter(
            a.x, a.y, a.z, a.freq, a.voltage,
            a.pulseInterval, a.pulseCount, a.trainInterval,
            a.trainCount, a.durationUs, a.mode
        )
    }

    function overallDeliveredTrains() {
        if (progressState === "finished") return originalTrainTotal
        var inBlock = Math.max(0, Math.min(progressCurrent, progressTotal))
        return Math.max(0, Math.min(originalTrainTotal,
                                    trainsDeliveredBeforeBlock + inBlock))
    }

    function overallFraction() {
        if (progressState === "idle") return 0
        if (progressState === "finished") return 1
        if (originalTrainTotal <= 0) return 0
        return Math.max(0, Math.min(1, overallDeliveredTrains() / originalTrainTotal))
    }

    function getProgressFillFraction() {
        return overallFraction()
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

    function blocksClause() {
        if (blockCount <= 1) return "."
        var elapsedS = (runElapsedMs > 0 ? runElapsedMs
                                         : (runStartTimeMs > 0 ? Date.now() - runStartTimeMs : 0))
                       / 1000.0
        return ", applied in " + blockCount + " blocks over "
             + formatDurationSeconds(elapsedS) + "."
    }

    function getProgressText() {
        if (progressState === "idle") return ""
        if (progressState === "finished") {
            var totalPulses = originalTrainTotal * originalPulseCount
            return "Finished successfully after " + totalPulses + " pulses ("
                 + formatDurationSeconds(originalDurationS) + ")"
                 + blocksClause()
        }
        if (progressState === "aborted") {
            var deliveredTrains = overallDeliveredTrains()
            var deliveredPulses = deliveredTrains * originalPulseCount
            var deliveredFrac = originalTrainTotal > 0
                ? deliveredTrains / originalTrainTotal : 0
            var deliveredSec = originalDurationS * deliveredFrac
            return "Aborted early after " + deliveredPulses + " pulses ("
                 + formatDurationSeconds(deliveredSec) + ")"
                 + blocksClause()
        }
        if (progressState === "paused") {
            var pausedFrac = overallFraction()
            var pausedPercent = Math.floor(pausedFrac * 100)
            var pausedRem = originalDurationS * (1 - pausedFrac)
            return "Paused, " + pausedPercent + "% Complete. "
                 + formatDurationSeconds(pausedRem) + " remaining"
        }
        if (originalTrainTotal <= 0) return "RUNNING"
        var frac = overallFraction()
        var percent = Math.floor(frac * 100)
        var remainingSec = originalDurationS * (1 - frac)
        return "Running [" + percent + "%] ("
             + formatDurationSeconds(remainingSec) + " remaining)"
    }

    function getProgressColor() {
        if (progressState === "finished") return "#1f963d"
        if (progressState === "aborted")  return "#E67E22"
        if (progressState === "paused")   return "#F1C40F"
        if (progressState === "running")  return "#269cf6"
        return "#3A3F4B"
    }

    function getSystemStateText() {
        if (LIFUConnector.state === 3) return "Running"
        if (LIFUConnector.state === 2) return coolingDown ? "Cooling Down" : "Ready"
        if (LIFUConnector.state === 0) return "Disconnected"
        if (LIFUConnector.state === 1) return "Connected"
        if (LIFUConnector.state === 4) return "Test Script Ready"
        return "Disconnected"
    }

    function getSystemStateColor() {
        if (!LIFUConnector.txConnected) return "#C0392B"
        if (LIFUConnector.state === 3)   return "#5db9ff"  // running
        if (LIFUConnector.state === 2 && coolingDown) return "#3498DB"  // cooling
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
        LIFUConnector.generate_plot(
            fixedX, fixedY, selectedDepthMm().toString(),
            selectedFrequencyKHz().toString(), selectedVoltage().toString(),
            selectedPulseIntervalMs().toString(), selectedPulseCount().toString(),
            selectedTrainIntervalS().toString(), pulseTrainCount().toString(),
            pulseDurationUs().toString(), "buffer"
        )
    }

    function configureNow() {
        // Programming the device clears any leftover "Finished" /
        // "Aborted" status from the previous run.
        resetProgressIdle()
        // Snapshot the args so that mid-run pause/resume reconfigures
        // (which use a shorter trainCount) can later restore the full
        // sequence on Abort or natural completion.
        originalConfigArgs = {
            x: fixedX,
            y: fixedY,
            z: selectedDepthMm().toString(),
            freq: selectedFrequencyKHz().toString(),
            voltage: selectedVoltage().toString(),
            pulseInterval: selectedPulseIntervalMs().toString(),
            pulseCount: selectedPulseCount().toString(),
            trainInterval: selectedTrainIntervalS().toString(),
            trainCount: pulseTrainCount().toString(),
            durationUs: pulseDurationUs().toString(),
            mode: fixedTriggerMode
        }
        LIFUConnector.configure_transmitter(
            originalConfigArgs.x, originalConfigArgs.y, originalConfigArgs.z,
            originalConfigArgs.freq, originalConfigArgs.voltage,
            originalConfigArgs.pulseInterval, originalConfigArgs.pulseCount,
            originalConfigArgs.trainInterval, originalConfigArgs.trainCount,
            originalConfigArgs.durationUs, originalConfigArgs.mode
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
                                resetProgressIdle()
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
                                resetProgressIdle()
                                if (everConfigured) {
                                    LIFUConnector.directSetSequence(
                                        selectedPulseIntervalMs().toString(),
                                        selectedPulseCount().toString(),
                                        selectedTrainIntervalS().toString(),
                                        pulseTrainCount().toString(),
                                        fixedTriggerMode
                                    )
                                    refreshPlot()
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
                            Text { text: selectedFrequencyKHz() + " kHz"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "Focus (X, Y, Z):";  color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: "(" + fixedX + ", " + fixedY + ", " + selectedDepthMm() + ") mm"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "Pulse Repetition Interval:"; color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: selectedPulseIntervalMs() + " ms"; color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "Pulse Count / Train:"; color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: selectedPulseCount();   color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }

                            Text { text: "Pulse Trains:";        color: "#BDC3C7"; font.pixelSize: 13 }
                            Text { text: pulseTrainCount();      color: "#9FB3C8"; font.pixelSize: 13; font.family: "Consolas" }
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
                        text: progressState === "paused" ? "Resume" : "Start"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        font.pixelSize: 22
                        font.weight: Font.Bold
                        visible: LIFUConnector.state >= 2
                        enabled: LIFUConnector.state === 2
                                 && !coolingDown
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
                            if (progressState === "paused") {
                                resumeRun()
                            } else {
                                startProgressFromUi()
                                LIFUConnector.start_sonication()
                            }
                        }
                    }

                    Button {
                        id: vetStopButton
                        text: progressState === "paused" ? "Abort" : "Stop"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        font.pixelSize: 22
                        font.weight: Font.Bold
                        visible: LIFUConnector.state >= 2
                        enabled: LIFUConnector.state === 3 || progressState === "paused"
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
                            if (progressState === "paused") {
                                abortRun()
                            } else if (progressState === "running") {
                                pauseRun()
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
                      + " \u00b0C exceeded the " + shutdownThreshold.toFixed(0)
                      + " \u00b0C limit. Sonication has been stopped and HV turned off. "
                      + "Wait for the device to cool below " + coolingThreshold.toFixed(0)
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

        function onPlotGenerated(imageData) {
            if (vetPlotImage) vetPlotImage.updateImage(imageData)
        }

        function onTemperatureTxUpdated(module, tx_temp, amb_temp) {
            var arr = txTemperatures.slice()
            while (arr.length <= module) arr.push(NaN)
            arr[module] = tx_temp
            txTemperatures = arr
            evaluateThermalState()
        }

        function onSonicationProgressUpdated(pt_curr, pt_total, p_curr, p_total) {
            if (progressState !== "running") return
            if (pt_total > 0 && pt_curr >= pt_total) {
                // Current block finished. Roll its delivered count into
                // the running total. If that completes the original
                // sequence, mark finished and restore config; otherwise
                // (shouldn't normally happen unless the device finishes
                // a block we didn't ask to be a final block) stay
                // running and let the next block take over.
                trainsDeliveredBeforeBlock += (progressTotal > 0 ? progressTotal : pt_curr)
                progressCurrent = 0
                progressTotal = 0
                if (trainsDeliveredBeforeBlock >= originalTrainTotal) {
                    progressState = "finished"
                    runElapsedMs = Date.now() - runStartTimeMs
                    restoreOriginalConfig()
                }
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
                // Re-evaluate cooling on the just-cleared telemetry so
                // the status flips out of "Cooling Down" once the next
                // STATUS frame arrives (and not before).
                coolingDown = false
                // If we just naturally finished (or aborted) the run,
                // the device may still be configured with the shorter
                // resumed-block solution. Now that it has dropped out
                // of RUNNING we can safely restore the originally-
                // programmed sequence so the next Start replays the
                // user's full selection.
                if (progressState === "finished" || progressState === "aborted") {
                    restoreOriginalConfig()
                }
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
                    // by navigating to Settings). Clear the progress
                    // UI so the user must re-program before
                    // restarting.
                    resetProgressIdle()
                    vetPlotImage.source = "../assets/images/empty_graph.png"
                }
            }
            previousConnectorState = state
        }
    }
}
