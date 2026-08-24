import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import QtQuick.Dialogs
import "../components"

Rectangle {
    id: controllerPage
    width: parent.width
    height: parent.height
    color: "#29292B"
    radius: 20
    opacity: 0.95

    // Properties to track solution loading state
    property bool solutionLoaded: LIFUConnector.solutionLoaded
    // Controls are only locked when a solution was loaded from disk or while
    // the device is actively running. Manually-entered solutions can be
    // re-configured at any other time.
    property bool controlsReadOnly: solutionLoaded || LIFUConnector.state === 3
    // True once Configure has succeeded at least once on the current
    // (manually-entered) solution. Drives the green/orange field coloring
    // and the on-commit directSet behavior.
    property bool everConfigured: false
    property bool uiLockedAfterSend: false
    property bool uiNeedsResend: false
    property int solutionConfigLabelWidth: 190
    property int solutionConfigInputWidth: 160
    property var txTemperatures: []
    property real hvPositiveRail: NaN
    property real hvNegativeRail: NaN
    // Tracks the actual HV-rail-on state reported by the device. Drives
    // the HV indicator color and the rail-monitor polling cadence so that
    // "ON" mode lights up the LED even when the system isn't transmitting.
    property bool hvOn: false
    property string statusOverrideText: ""
    // True while a runWithButtonFeedback action is executing. Drives the
    // BusyOverlay so users get a visible "working" cue when the GUI
    // thread is blocked by a synchronous device-comms slot.
    property bool busy: false
    property int configuredModuleCount: 0
    property int previousConnectorState: LIFUConnector.state
    
    // Properties to track field activity based on trigger mode
    property bool pulseIntervalActive: true
    property bool pulseCountActive: true
    property bool trainIntervalActive: triggerModeDropdown.currentText !== "Single"
    property bool trainCountActive: triggerModeDropdown.currentText === "Sequence"
    
    // Property to track if train interval is less than pulse interval x pulse count
    property bool trainIntervalTooShort: false
    property var presetSolutions: []
    property string saveSolutionPath: ""
    property bool savePathAuto: true
    property string controllerTelemetryLogPath: ""

    // Sonication progress UI state. Driven by Start/Stop button clicks
    // plus unsolicited STATUS frames from the firmware. State machine:
    //   "idle"     -> bar is empty, no text (or "READY")
    //   "running"  -> bar fills, text "RUNNING i/N" (or "RUNNING i" continuous)
    //   "finished" -> bar full, green, text "FINISHED N/N"
    //   "stopped"  -> bar at last value, orange, text "STOPPED"
    property string progressState: "idle"
    property int progressCurrent: 0
    property int progressTotal: 0
    // Cached at Start time so a mid-run mode change doesn't break the
    // denominator display.
    property string progressMode: "Sequence"

    // ----- Multi-focus (rastered) sonication -----
    //
    // fociModel is the source of truth for the focus list. Row 0 mirrors
    // the inline Lateral/Elevation/Axial fields (kept for the common
    // single-focus case); rows beyond it are only editable in the Foci
    // dialog. Each focus becomes one delay profile on the transmitter and
    // the firmware cycles through the execution order at pulse
    // boundaries, so a multi-focus run auto-rasters with no host
    // involvement once Start is pressed.
    //
    // Hardware limits come from the SDK via the connector -- never restate
    // them here, or the UI silently diverges from what the device enforces.
    readonly property int maxFocusPoints: LIFUConnector.maxFocusPoints

    // Roles are fx/fy/fz rather than x/y/z: a Repeater delegate is an
    // Item, and required properties named x/y would shadow its geometry.
    ListModel {
        id: fociModel
        ListElement { fx: "0"; fy: "0"; fz: "50" }
    }

    // Empty means "use the default order" (1,2,...,N). A user-supplied
    // order may repeat and reorder entries freely, e.g. "1,2,1,3".
    property string executionOrderText: ""
    property string fociError: ""
    property string fociSummary: "single focus"

    // ----- Execution-order parsing -----
    //
    // Parameterized by focus count so the same code validates the live
    // configuration and the Foci dialog's working copy.

    function defaultOrderTextFor(count) {
        var parts = []
        for (var i = 1; i <= count; i++) {
            parts.push(i)
        }
        return parts.join(",")
    }

    function effectiveOrderTextFor(orderText, count) {
        return orderText.trim() === "" ? defaultOrderTextFor(count) : orderText
    }

    // Returns {order: [int], error: string}. A non-empty error means the
    // text could not be parsed against a focus list of `count` entries.
    function parseExecutionOrder(text, count) {
        var tokens = text.split(/[,\s]+/).filter(function(token) { return token.length > 0 })
        if (tokens.length === 0) {
            return { order: [], error: "Execution order is empty." }
        }
        var order = []
        for (var i = 0; i < tokens.length; i++) {
            if (!/^\d+$/.test(tokens[i])) {
                return { order: [], error: "\"" + tokens[i] + "\" is not a focus number." }
            }
            var value = parseInt(tokens[i])
            if (value < 1 || value > count) {
                return { order: [], error: "Focus " + value + " does not exist (valid: 1-" + count + ")." }
            }
            order.push(value)
        }
        return { order: order, error: "" }
    }

    function defaultExecutionOrderText() {
        return defaultOrderTextFor(fociModel.count)
    }

    function effectiveExecutionOrderText() {
        return effectiveOrderTextFor(executionOrderText, fociModel.count)
    }

    // Mirror the inline X/Y/Z fields into focus 1. Called before anything
    // reads the focus list so inline edits are never lost.
    function syncFocusOneFromInputs() {
        if (fociModel.count === 0) {
            fociModel.append({ fx: xInput.text, fy: yInput.text, fz: zInput.text })
        } else {
            fociModel.set(0, { fx: xInput.text, fy: yInput.text, fz: zInput.text })
        }
    }

    function syncInputsFromFocusOne() {
        if (fociModel.count === 0) {
            return
        }
        var first = fociModel.get(0)
        xInput.text = first.fx
        yInput.text = first.fy
        zInput.text = first.fz
    }

    function fociArray() {
        syncFocusOneFromInputs()
        var points = []
        for (var i = 0; i < fociModel.count; i++) {
            var row = fociModel.get(i)
            points.push({ x: parseFloat(row.fx), y: parseFloat(row.fy), z: parseFloat(row.fz) })
        }
        return points
    }

    function executionOrderArray() {
        var parsed = parseExecutionOrder(effectiveExecutionOrderText(), fociModel.count)
        // An empty list tells Python to fall back to the default order.
        // fociError blocks Configure before it can get here.
        return parsed.error === "" ? parsed.order : []
    }

    // Full validation of a focus list against the current sequence
    // parameters. Mirrors the checks in lifu_controller.get_solution so
    // the operator sees the problem before a device write is attempted.
    // Takes the model explicitly so the Foci dialog can validate its
    // working copy before committing it.
    function computeFociErrorFor(model, orderText) {
        if (model.count < 1) {
            return "At least one focus is required."
        }
        if (model.count > maxFocusPoints) {
            return "At most " + maxFocusPoints + " foci are supported (have " + model.count + ")."
        }
        for (var i = 0; i < model.count; i++) {
            var row = model.get(i)
            if (isNaN(parseFloat(row.fx)) || isNaN(parseFloat(row.fy)) || isNaN(parseFloat(row.fz))) {
                return "Focus " + (i + 1) + " has a non-numeric coordinate."
            }
        }

        var parsed = parseExecutionOrder(effectiveOrderTextFor(orderText, model.count), model.count)
        if (parsed.error !== "") {
            return parsed.error
        }

        // The interlocks below only apply when the firmware actually has
        // to switch profiles between pulses.
        if (model.count > 1) {
            var cycleLength = parsed.order.length
            var pulseCount = parseInt(triggerPulseCount.text)
            if (!isNaN(pulseCount) && pulseCount % cycleLength !== 0) {
                return "Pulse count (" + pulseCount + ") must be a multiple of the execution order length ("
                       + cycleLength + ")."
            }
            // Duration is entered in microseconds, pulse interval in
            // milliseconds -- convert so the dead-time comparison below is
            // in one unit.
            var pulseIntervalMs = parseFloat(triggerPulseInterval.text)
            var durationMs = parseFloat(durationInput.text) / 1000.0
            var minSwitchMs = LIFUConnector.minProfileSwitchIntervalMs
            if (!isNaN(pulseIntervalMs) && !isNaN(durationMs)
                    && (pulseIntervalMs - durationMs) < minSwitchMs) {
                return "Pulse interval must exceed pulse duration by at least "
                       + minSwitchMs + " ms so the firmware can switch focus."
            }
        }
        return ""
    }

    function updateFociValidation() {
        if (!triggerPulseCount || !triggerPulseInterval || !durationInput) {
            return
        }
        syncFocusOneFromInputs()
        fociError = computeFociErrorFor(fociModel, executionOrderText)
        fociSummary = fociSummaryTextFor(fociModel, executionOrderText)
    }

    // Seed the dialog before opening rather than relying solely on
    // onOpened, which only fires once the enter transition finishes.
    function openFociDialog() {
        fociDialog.loadFromPage()
        fociDialog.open()
    }

    // Compact one-line description of a focus configuration, shown next to
    // the Foci button and inside the dialog.
    function fociSummaryTextFor(model, orderText) {
        if (model.count <= 1) {
            return "single focus"
        }
        var parsed = parseExecutionOrder(effectiveOrderTextFor(orderText, model.count), model.count)
        if (parsed.error !== "") {
            return model.count + " foci"
        }
        var pulseCount = parseInt(triggerPulseCount.text)
        var perEntry = (!isNaN(pulseCount) && pulseCount % parsed.order.length === 0)
                       ? (pulseCount / parsed.order.length) : NaN
        return model.count + " foci · order " + parsed.order.join("-")
               + (isNaN(perEntry) ? "" : " · " + perEntry + " pulse" + (perEntry === 1 ? "" : "s") + " each")
    }

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

    // Single entry point for plot refreshes so every caller sends the
    // same focus list and execution order.
    function refreshPlot() {
        LIFUConnector.generate_plot(
            xInput.text, yInput.text, zInput.text,
            frequencyInput.text, voltage.text, triggerPulseInterval.text,
            triggerPulseCount.text, triggerPulseTrainInterval.text, triggerPulseTrainCount.text,
            durationInput.text, "buffer", fociArray(), executionOrderArray()
        )
    }

    function loadSolutionAndRefreshPlot(filePath) {
        if (!filePath || filePath === "") {
            return
        }

        LIFUConnector.loadSolutionFromFile(filePath)
        refreshPlot()
    }

    function refreshPresetSolutions() {
        var presets = LIFUConnector.getPresetSolutions()
        presetSolutions = presets ? presets : []
    }

    function sanitizeSolutionId(rawId) {
        var cleaned = (rawId || "").trim()
        if (cleaned === "") {
            return "solution"
        }
        // Keep file names safe across common platforms.
        return cleaned.replace(/[^a-zA-Z0-9._-]/g, "_")
    }

    function getDefaultSavePath(solutionId) {
        var safeId = sanitizeSolutionId(solutionId)
        var presetsDir = LIFUConnector.getPresetSolutionsPath()
        if (!presetsDir || presetsDir === "") {
            presetsDir = "."
        }
        var sep = Qt.platform.os === "windows" ? "\\" : "/"
        if (presetsDir.endsWith("/") || presetsDir.endsWith("\\")) {
            return presetsDir + safeId + ".json"
        }
        return presetsDir + sep + safeId + ".json"
    }

    function localPathToFileUrl(path) {
        if (!path || path === "") {
            return ""
        }

        var normalized = path.replace(/\\/g, "/")
        if (/^[A-Za-z]:\//.test(normalized)) {
            return "file:///" + normalized
        }
        if (normalized.startsWith("/")) {
            return "file://" + normalized
        }
        return "file:///" + normalized
    }

    function applySettingsToUi(settings) {
        if (!settings || settings.xInput === undefined) {
            return
        }

        // Rebuild the focus list from the solution. Older files without a
        // "foci" array come back as a single focus, which keeps row 0 (and
        // therefore the inline X/Y/Z fields) behaving exactly as before.
        fociModel.clear()
        var loadedFoci = settings.foci
        if (loadedFoci && loadedFoci.length > 0) {
            for (var i = 0; i < loadedFoci.length; i++) {
                fociModel.append({
                    fx: loadedFoci[i].x.toString(),
                    fy: loadedFoci[i].y.toString(),
                    fz: loadedFoci[i].z.toString()
                })
            }
        } else {
            fociModel.append({
                fx: settings.xInput.toString(),
                fy: settings.yInput.toString(),
                fz: settings.zInput.toString()
            })
        }
        // Only keep an explicit order when it differs from the default,
        // so adding a focus later extends the order automatically.
        var loadedOrder = settings.executionOrder
        executionOrderText = (loadedOrder && loadedOrder.length > 0)
                             ? loadedOrder.join(",") : ""
        if (executionOrderText === defaultExecutionOrderText()) {
            executionOrderText = ""
        }
        syncInputsFromFocusOne()

        frequencyInput.text = settings.frequency.toString()
        durationInput.text = settings.duration.toString()
        voltage.text = settings.voltage.toString()

        triggerPulseInterval.text = settings.pulseInterval.toString()
        triggerPulseCount.text = settings.pulseCount.toString()
        triggerPulseTrainInterval.text = settings.trainInterval.toString()
        triggerPulseTrainCount.text = settings.trainCount.toString()

        updateTrainIntervalValidation()
        updateFociValidation()
    }

    // ----- Direct-edit-on-commit helpers -----
    //
    // Once `everConfigured` is true, edits to any of the parameter fields
    // commit straight to the device when the user finishes editing
    // (Enter / focus loss / ComboBox activation). Fields turn orange while
    // dirty and green once the device has accepted the value.
    function getFieldColor(dirty, readOnly, active) {
        if (active === false) {
            return readOnly ? "#777" : "#888"
        }
        if (readOnly) {
            return "#BBB"
        }
        if (!everConfigured) {
            return "white"
        }
        return dirty ? "#E67E22" : "#43BB57"
    }

    function clearAllDirty() {
        voltage.dirty = false
        triggerPulseInterval.dirty = false
        triggerPulseCount.dirty = false
        triggerPulseTrainInterval.dirty = false
        triggerPulseTrainCount.dirty = false
        frequencyInput.dirty = false
        durationInput.dirty = false
        xInput.dirty = false
        yInput.dirty = false
        zInput.dirty = false
    }

    function commitVoltage() {
        if (!everConfigured) {
            return false
        }
        resetProgressIdle()
        var ok = LIFUConnector.directSetVoltage(voltage.text)
        if (ok) {
            refreshPlot()
        }
        return ok
    }

    function commitSequence() {
        if (!everConfigured) {
            return false
        }
        resetProgressIdle()
        // Duration and voltage are passed so the connector can re-check the
        // duty-cycle envelope; this path writes straight to the TX device.
        var ok = LIFUConnector.directSetSequence(
            triggerPulseInterval.text,
            triggerPulseCount.text,
            triggerPulseTrainInterval.text,
            triggerPulseTrainCount.text,
            triggerModeDropdown.currentText,
            durationInput.text,
            voltage.text
        )
        if (ok) {
            refreshPlot()
        }
        return ok
    }

    function commitPulse() {
        if (!everConfigured) {
            return false
        }
        resetProgressIdle()
        var ok = LIFUConnector.directSetPulse(
            xInput.text, yInput.text, zInput.text,
            frequencyInput.text, voltage.text,
            triggerPulseInterval.text, triggerPulseCount.text,
            triggerPulseTrainInterval.text, triggerPulseTrainCount.text,
            durationInput.text, triggerModeDropdown.currentText,
            fociArray(), executionOrderArray()
        )
        if (ok) {
            refreshPlot()
        }
        return ok
    }

    function getSystemStateText() {
        return "Status: " + (LIFUConnector.state === 0 ? "Disconnected"
                            : LIFUConnector.state === 1 ? "Connected"
                            : LIFUConnector.state === 2 ? "Ready"
                            : LIFUConnector.state === 3 ? "Running"
                            : LIFUConnector.state === 4 ? "Test Script Ready"
                            : "Disconnected")
    }

    // Firmware-compliance overrides for the System State row. When any
    // connected device is below the app's hard minimum we surface
    // "Firmware Update Required" in orange and lock the Configure
    // button; below the SDK-packaged version (but >= minimum) we show
    // "Firmware Update Available" in yellow as advisory only.
    function getStatusText() {
        // The bypass outranks everything else here: the left panel is
        // read-only while running, so this line is the only place the
        // operator can see the override is still armed mid-sonication.
        var bypass = LIFUConnector.safetyBypassEnabled ? "  ⚠ LIMITS BYPASSED" : ""
        if (statusOverrideText !== "") {
            return statusOverrideText + bypass
        }
        if (LIFUConnector.firmwareUpdateRequired) {
            return "Status: Firmware Update Required" + bypass
        }
        if (LIFUConnector.firmwareUpdateAvailable) {
            return "Status: Firmware Update Available" + bypass
        }
        return getSystemStateText() + bypass
    }

    function getStatusColor() {
        if (LIFUConnector.safetyBypassEnabled) {
            return "#E67E22"  // orange: safety limits are not being enforced
        }
        if (statusOverrideText !== "") {
            return getTXIndicatorColor()
        }
        if (LIFUConnector.firmwareUpdateRequired) {
            return "#E67E22"  // orange: hard minimum violated; lockout active
        }
        if (LIFUConnector.firmwareUpdateAvailable) {
            return "#F1C40F"  // yellow: advisory; SDK ships a newer build
        }
        return getTXIndicatorColor()
    }

    function getStatusTooltip() {
        // Always show the per-device firmware report when something is
        // connected; layer the Required/Available headline on top so
        // operators see live versions next to the min/packaged values.
        var report = LIFUConnector.firmwareStatusReport
        if (LIFUConnector.firmwareUpdateRequired) {
            var headline = "Configuration actions are disabled until the "
                + "firmware is updated from the Settings tab."
            return report.length > 0 ? headline + "\n\n" + report : headline
        }
        if (LIFUConnector.firmwareUpdateAvailable) {
            var headline2 = "A newer firmware is available. Update from "
                + "the Settings tab when convenient."
            return report.length > 0 ? headline2 + "\n\n" + report : headline2
        }
        return report
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

    function clearStatusTelemetry() {
        txTemperatures = []
        if (!hvOn) {
            hvPositiveRail = NaN
            hvNegativeRail = NaN
        }
    }

    // Keep a button visually depressed while its click work executes,
    // and raise a page-wide BusyOverlay so the rest of the UI shows a
    // visible "working" cue (and is non-interactive) while the
    // synchronous device-comms action runs.
    //
    // We deliberately use a Timer rather than Qt.callLater here:
    // Qt.callLater fires inside the same event-loop tick BEFORE the
    // scene graph paints, so the overlay would never actually appear
    // before the synchronous action blocks the GUI thread. A short
    // Timer interval (~50 ms) gives Qt at least one render frame to
    // paint the overlay first.
    function runWithButtonFeedback(button, action) {
        if (!button || !action || button.visualPressed || busyActionTimer.running) {
            return
        }

        button.visualPressed = true
        controllerPage.busy = true
        busyActionTimer.pendingButton = button
        busyActionTimer.pendingAction = action
        busyActionTimer.start()
    }

    // Lighter-weight variant for direct-edit commits (no button to
    // depress, just the page-wide BusyOverlay). Used by the
    // onEditingFinished handlers so the user gets a brief spinner
    // while directSet calls block the GUI thread.
    function runBusy(action) {
        if (!action || busyActionTimer.running) {
            return
        }
        controllerPage.busy = true
        busyActionTimer.pendingButton = null
        busyActionTimer.pendingAction = action
        busyActionTimer.start()
    }

    // Standard onEditingFinished handler for the parameter TextFields:
    // if the field is dirty, run the matching commit through the busy
    // timer and clear dirty on success.
    function commitDirtyField(field, commitFn) {
        if (!field || !field.dirty) {
            return
        }
        runBusy(function() {
            if (commitFn()) {
                field.dirty = false
            }
        })
    }

    Timer {
        id: busyActionTimer
        interval: 50
        repeat: false
        property var pendingAction: null
        property var pendingButton: null
        onTriggered: {
            var act = pendingAction
            var btn = pendingButton
            pendingAction = null
            pendingButton = null
            try {
                if (act) act()
            } finally {
                if (btn) btn.visualPressed = false
                controllerPage.busy = false
            }
        }
    }

    function getTXIndicatorColor() {
        if (!LIFUConnector.txConnected) {
            return "#C0392B"  // red: disconnected
        }
        if (LIFUConnector.state === 3) {
            return "#269cf6"  // blue: sonication running
        }
        if (LIFUConnector.state < 2) {
            return "#0f5d24"  // dark green: connected, not yet configured
        }
        return "#1f963d"  // green: configured / ready
    }

    function getHVIndicatorColor() {
        if (!LIFUConnector.hvConnected) {
            return "#C0392B"  // red: disconnected
        }
        if (hvOn) {
            return "#269cf6"  // blue: HV rail energized
        }
        if (LIFUConnector.state < 2) {
            return "#0f5d24"  // dark green: connected, not yet configured
        }
        return "#1f963d"  // green: connected, rail off
    }

    // ----- Sonication progress helpers -----
    function resetProgressIdle() {
        progressState = "idle"
        progressCurrent = 0
        progressTotal = 0
    }

    function startProgressFromUi() {
        progressMode = triggerModeDropdown.currentText
        if (progressMode === "Single") {
            progressTotal = 1
            progressCurrent = 1
        } else if (progressMode === "Continuous") {
            // No meaningful denominator; treat total as a sentinel that
            // disables percent-based fill in getProgressFillFraction().
            progressTotal = 0
            progressCurrent = 1
        } else { // Sequence
            var n = parseInt(triggerPulseTrainCount.text)
            if (isNaN(n) || n < 1) { n = 1 }
            progressTotal = n
            progressCurrent = 1
        }
        progressState = "running"
    }

    function getProgressFillFraction() {
        if (progressState === "idle") {
            return 0
        }
        if (progressState === "finished") {
            return 1
        }
        if (progressMode === "Continuous") {
            // No determinate end; show the bar fully filled while
            // running and on terminal states.
            return 1
        }
        if (progressTotal <= 0) {
            return 0
        }
        return Math.max(0, Math.min(1, progressCurrent / progressTotal))
    }

    function getProgressText() {
        if (progressState === "idle") {
            return ""
        }
        if (progressState === "stopped") {
            return "STOPPED"
        }
        var prefix = progressState === "finished" ? "FINISHED" : "RUNNING"
        if (progressMode === "Continuous") {
            return prefix + " " + progressCurrent
        }
        return prefix + " " + progressCurrent + "/" + progressTotal
    }

    function getProgressColor() {
        if (progressState === "finished") {
            return "#1f963d"  // green
        }
        if (progressState === "stopped") {
            return "#E67E22"  // orange
        }
        if (progressState === "running") {
            return "#269cf6"  // blue
        }
        return "#3A3F4B"      // idle / dim
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
            loadSolutionAndRefreshPlot(filePath)
            loadPresetDialog.close()
        }
    }

    Dialog {
        id: solutionLoadErrorDialog
        title: "Solution Load Error"
        modal: true
        focus: true
        width: 480
        height: 200
        property string errorMessage: ""
        x: (controllerPage.width - width) / 2
        y: (controllerPage.height - height) / 2

        background: Rectangle {
            color: "#1E1E20"
            border.color: "#7A2E2E"
            border.width: 2
            radius: 8
        }

        contentItem: ColumnLayout {
            spacing: 10

            Text {
                text: solutionLoadErrorDialog.errorMessage
                color: "#FFD3D3"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
        }

        footer: RowLayout {
            spacing: 10

            Item { Layout.fillWidth: true }

            Button {
                text: "OK"
                onClicked: solutionLoadErrorDialog.close()
            }
        }
    }

    Dialog {
        id: loadPresetDialog
        title: "Load Solution"
        modal: true
        focus: true
        width: 420
        height: 230
        x: (controllerPage.width - width) / 2
        y: (controllerPage.height - height) / 2

        background: Rectangle {
            color: "#1E1E20"
            border.color: "#3E4E6F"
            border.width: 2
            radius: 8
        }

        onOpened: {
            refreshPresetSolutions()
            presetDropdown.currentIndex = presetSolutions.length > 0 ? 0 : -1
        }

        contentItem: ColumnLayout {
            spacing: 12

            Text {
                text: "Select preset solution:"
                color: "white"
                font.pixelSize: 14
            }

            ComboBox {
                id: presetDropdown
                Layout.fillWidth: true
                Layout.preferredHeight: 38
                model: presetSolutions
                textRole: "name"
                enabled: presetSolutions.length > 0
                font.pixelSize: 14

                background: Rectangle {
                    color: "#222"
                    border.color: "#999"
                    radius: 4
                }
            }

            Text {
                text: presetSolutions.length > 0 ? "" : "No preset solutions found in preset_solutions/*.json"
                color: "#D58A8A"
                font.pixelSize: 12
                visible: presetSolutions.length === 0
            }

            Text {
                text: (presetDropdown.currentIndex >= 0 && presetSolutions.length > 0)
                      ? ("File: " + presetSolutions[presetDropdown.currentIndex].path)
                      : ""
                color: "#9FB3C8"
                font.pixelSize: 11
                visible: presetSolutions.length > 0
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
            }
        }

        footer: RowLayout {
            spacing: 8

            Item { Layout.preferredWidth: 6 }

            Button {
                text: "Load from File..."
                onClicked: solutionFileDialog.open()
            }

            Item { Layout.fillWidth: true }

            Button {
                text: "Cancel"
                onClicked: loadPresetDialog.close()
            }

            Button {
                text: "Load"
                enabled: presetDropdown.currentIndex >= 0 && presetSolutions.length > 0
                onClicked: {
                    var selectedPreset = presetSolutions[presetDropdown.currentIndex]
                    if (selectedPreset && selectedPreset.path) {
                        loadSolutionAndRefreshPlot(selectedPreset.path)
                        loadPresetDialog.close()
                    }
                }
            }

            Item { Layout.preferredWidth: 6 }
        }
    }

    FileDialog {
        id: saveSolutionFileDialog
        title: "Save Solution As"
        fileMode: FileDialog.SaveFile
        currentFolder: localPathToFileUrl(LIFUConnector.getPresetSolutionsPath())
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: {
            var filePath = selectedFile.toString()
            if (filePath.startsWith("file:///")) {
                filePath = filePath.substring(8)
            } else if (filePath.startsWith("file://")) {
                filePath = filePath.substring(7)
            }
            if (Qt.platform.os === "windows") {
                filePath = filePath.replace(/\//g, "\\")
            }
            saveSolutionPath = filePath
            savePathAuto = false
        }
    }

    Dialog {
        id: saveSolutionDialog
        title: "Save Solution"
        modal: true
        focus: true
        width: 520
        height: 380
        x: (controllerPage.width - width) / 2
        y: (controllerPage.height - height) / 2

        background: Rectangle {
            color: "#1E1E20"
            border.color: "#3E4E6F"
            border.width: 2
            radius: 8
        }

        onOpened: {
            savePathAuto = true
            if (saveSolutionIdField.text === "") {
                saveSolutionIdField.text = "solution"
            }
            if (saveSolutionNameField.text === "") {
                saveSolutionNameField.text = "Solution"
            }
            saveSolutionPath = getDefaultSavePath(saveSolutionIdField.text)
        }

        contentItem: ColumnLayout {
            spacing: 10

            TextField {
                id: saveSolutionIdField
                Layout.fillWidth: true
                placeholderText: "solution_id"
                topPadding: 0
                bottomPadding: 0
                onTextChanged: {
                    if (savePathAuto) {
                        saveSolutionPath = getDefaultSavePath(saveSolutionIdField.text)
                    }
                }
            }

            TextField {
                id: saveSolutionNameField
                Layout.fillWidth: true
                placeholderText: "Solution Name"
                topPadding: 0
                bottomPadding: 0
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                TextField {
                    id: saveSolutionPathField
                    placeholderText: "File Location"
                    Layout.fillWidth: true
                    text: saveSolutionPath
                    onTextEdited: {
                        savePathAuto = false
                        saveSolutionPath = text
                    }
                }

                Button {
                    text: "Browse"
                    onClicked: {
                        saveSolutionFileDialog.currentFolder = localPathToFileUrl(LIFUConnector.getPresetSolutionsPath())
                        saveSolutionFileDialog.selectedFile = localPathToFileUrl(getDefaultSavePath(saveSolutionIdField.text))
                        saveSolutionFileDialog.open()
                    }
                }
            }


        }

        footer: Item {
            implicitHeight: footerLayout.implicitHeight + 14

            RowLayout {
                id: footerLayout
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                anchors.topMargin: 6
                spacing: 10

                Item { Layout.fillWidth: true }

                Button {
                    text: "Save as Default"
                    enabled: LIFUConnector.queryNumModulesConnected > 0
                    onClicked: {
                        var saveDefaultOk = LIFUConnector.saveSolutionToFile(
                            "default_solution",
                            saveSolutionNameField.text.trim().length > 0 ? saveSolutionNameField.text : "Default Solution",
                            LIFUConnector.getDefaultSolutionFilePath(),
                            LIFUConnector.queryNumModulesConnected.toString(),
                            xInput.text, yInput.text, zInput.text,
                            frequencyInput.text, voltage.text,
                            triggerPulseInterval.text, triggerPulseCount.text,
                            triggerPulseTrainInterval.text, triggerPulseTrainCount.text,
                            durationInput.text,
                            fociArray(), executionOrderArray()
                        )
                        if (saveDefaultOk) {
                            saveSolutionDialog.close()
                        }
                    }
                }

                Button {
                    text: "Cancel"
                    onClicked: saveSolutionDialog.close()
                }

                Button {
                    text: "Save"
                    enabled: saveSolutionIdField.text.trim().length > 0 && saveSolutionPath.trim().length > 0 && LIFUConnector.queryNumModulesConnected > 0
                    onClicked: {
                        var saveOk = LIFUConnector.saveSolutionToFile(
                            saveSolutionIdField.text,
                            saveSolutionNameField.text,
                            saveSolutionPath,
                            LIFUConnector.queryNumModulesConnected.toString(),
                            xInput.text, yInput.text, zInput.text,
                            frequencyInput.text, voltage.text,
                            triggerPulseInterval.text, triggerPulseCount.text,
                            triggerPulseTrainInterval.text, triggerPulseTrainCount.text,
                            durationInput.text,
                            fociArray(), executionOrderArray()
                        )
                        if (saveOk) {
                            saveSolutionDialog.close()
                        }
                    }
                }
            }
        }
    }

    // Confirmation gate for the safety-limit bypass. The checkbox never
    // arms the override itself -- only this dialog calls setSafetyBypass(true).
    Dialog {
        id: safetyBypassDialog
        title: "Bypass safety limits?"
        modal: true
        focus: true
        width: 560
        height: 340
        x: (controllerPage.width - width) / 2
        y: (controllerPage.height - height) / 2
        closePolicy: Popup.NoAutoClose   // require an explicit choice

        background: Rectangle {
            color: "#1E1E20"
            border.color: "#E67E22"
            border.width: 2
            radius: 8
        }

        contentItem: ColumnLayout {
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Text {
                    text: "⚠"
                    color: "#E67E22"
                    font.pixelSize: 30
                }
                Text {
                    Layout.fillWidth: true
                    text: "Configure will skip the SDK's check_solution() safety pass."
                    color: "#E67E22"
                    font.pixelSize: 14
                    font.bold: true
                    wrapMode: Text.WordWrap
                }
            }

            Text {
                Layout.fillWidth: true
                text: "The duty-cycle, voltage and sequence-duration limits will not be enforced, "
                      + "so the array can be driven at up to 100% duty cycle."
                color: "#DDD"
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                text: "Sustained operation outside the rated envelope can permanently damage the "
                      + "transducer and the transmit electronics, and surfaces may become hot enough "
                      + "to burn. Only continue for instrumented bench testing where you are "
                      + "monitoring module temperature and drive level yourself."
                color: "#DDD"
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                text: "The bypass clears automatically when the transmitter disconnects or the "
                      + "application restarts. The HV controller's 5–100 V rail limit still applies."
                color: "#9FB3C8"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }

            Item { Layout.fillHeight: true }
        }

        footer: RowLayout {
            spacing: 8
            Item { Layout.fillWidth: true }

            Button {
                text: "Cancel"
                onClicked: safetyBypassDialog.close()
            }

            Button {
                id: confirmBypassButton
                text: "Bypass safety limits"
                background: Rectangle {
                    color: confirmBypassButton.down ? "#A85B18" : "#E67E22"
                    radius: 4
                    border.color: "#F0A050"
                }
                contentItem: Text {
                    text: confirmBypassButton.text
                    color: "white"
                    font: confirmBypassButton.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: {
                    LIFUConnector.setSafetyBypass(true)
                    // setSafetyBypass drops the configured flag, so the
                    // operator must re-Configure for this to take effect.
                    everConfigured = false
                    resetProgressIdle()
                    safetyBypassDialog.close()
                }
            }

            Item { Layout.preferredWidth: 6 }
        }
    }

    // Focus-list editor. Works on a copy of fociModel so Cancel is a true
    // no-op; OK is disabled until the configuration would actually program.
    Dialog {
        id: fociDialog
        title: "Focus Points"
        modal: true
        focus: true
        width: 700
        height: 520
        x: (controllerPage.width - width) / 2
        y: (controllerPage.height - height) / 2

        // Working copy; committed to fociModel only on OK.
        property string workingOrderText: ""
        property int workingRevision: 0
        readonly property string workingError:
            workingRevision >= 0 ? computeFociErrorFor(fociEditModel, workingOrderText) : ""
        readonly property string workingSummary:
            workingRevision >= 0 ? fociSummaryTextFor(fociEditModel, workingOrderText) : ""

        background: Rectangle {
            color: "#1E1E20"
            border.color: "#3E4E6F"
            border.width: 2
            radius: 8
        }

        ListModel { id: fociEditModel }

        function touch() {
            // ListModel row edits are not observable; bump a counter that
            // the workingError/workingSummary bindings depend on.
            workingRevision++
        }

        // Seed the working copy from the live configuration.
        function loadFromPage() {
            syncFocusOneFromInputs()
            fociEditModel.clear()
            for (var i = 0; i < fociModel.count; i++) {
                var row = fociModel.get(i)
                fociEditModel.append({ fx: row.fx, fy: row.fy, fz: row.fz })
            }
            workingOrderText = executionOrderText
            fociOrderField.text = executionOrderText
            touch()
        }

        onOpened: loadFromPage()

        contentItem: ColumnLayout {
            spacing: 8

            Text {
                Layout.fillWidth: true
                text: "Each focus is programmed as its own delay profile. During sonication the "
                      + "transmitter cycles through the execution order at pulse boundaries, "
                      + "restarting at the top of every pulse train."
                color: "#9FB3C8"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }

            // Column headers
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Text { text: "#"; color: "#9FB3C8"; font.pixelSize: 11; Layout.preferredWidth: 22 }
                Text { text: "X (mm)"; color: "#9FB3C8"; font.pixelSize: 11; Layout.preferredWidth: 90 }
                Text { text: "Y (mm)"; color: "#9FB3C8"; font.pixelSize: 11; Layout.preferredWidth: 90 }
                Text { text: "Z (mm)"; color: "#9FB3C8"; font.pixelSize: 11; Layout.preferredWidth: 90 }
                Item { Layout.fillWidth: true }
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: parent.width
                    spacing: 4

                    Repeater {
                        model: fociEditModel

                        RowLayout {
                            id: focusRow
                            required property int index
                            required property string fx
                            required property string fy
                            required property string fz

                            Layout.fillWidth: true
                            spacing: 6

                            Text {
                                text: (focusRow.index + 1).toString()
                                color: "white"
                                font.pixelSize: 12
                                Layout.preferredWidth: 22
                            }

                            TextField {
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                font.pixelSize: 13
                                text: focusRow.fx
                                color: isNaN(parseFloat(text)) ? "#E67E22" : "white"
                                background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                                onTextEdited: {
                                    fociEditModel.setProperty(focusRow.index, "fx", text)
                                    fociDialog.touch()
                                }
                            }

                            TextField {
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                font.pixelSize: 13
                                text: focusRow.fy
                                color: isNaN(parseFloat(text)) ? "#E67E22" : "white"
                                background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                                onTextEdited: {
                                    fociEditModel.setProperty(focusRow.index, "fy", text)
                                    fociDialog.touch()
                                }
                            }

                            TextField {
                                Layout.preferredWidth: 90
                                Layout.preferredHeight: 30
                                font.pixelSize: 13
                                text: focusRow.fz
                                color: isNaN(parseFloat(text)) ? "#E67E22" : "white"
                                background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                                onTextEdited: {
                                    fociEditModel.setProperty(focusRow.index, "fz", text)
                                    fociDialog.touch()
                                }
                            }

                            Button {
                                text: "Duplicate"
                                font.pixelSize: 11
                                implicitHeight: 30
                                implicitWidth: 84
                                leftPadding: 8
                                rightPadding: 8
                                enabled: fociEditModel.count < maxFocusPoints
                                onClicked: {
                                    var row = fociEditModel.get(focusRow.index)
                                    fociEditModel.insert(focusRow.index + 1,
                                                         { fx: row.fx, fy: row.fy, fz: row.fz })
                                    fociDialog.touch()
                                }
                            }

                            Button {
                                text: "Remove"
                                font.pixelSize: 11
                                implicitHeight: 30
                                implicitWidth: 76
                                leftPadding: 8
                                rightPadding: 8
                                // Focus 1 backs the inline X/Y/Z fields, so
                                // the list can never be emptied.
                                enabled: fociEditModel.count > 1
                                onClicked: {
                                    fociEditModel.remove(focusRow.index)
                                    fociDialog.touch()
                                }
                            }

                            Item { Layout.fillWidth: true }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Button {
                    text: "+ Add Focus"
                    font.pixelSize: 12
                    implicitHeight: 30
                    enabled: fociEditModel.count < maxFocusPoints
                    onClicked: {
                        // Seed from the last focus so incremental rasters
                        // are a small edit rather than a full retype.
                        var last = fociEditModel.count > 0
                                   ? fociEditModel.get(fociEditModel.count - 1)
                                   : { fx: "0", fy: "0", fz: "50" }
                        fociEditModel.append({ fx: last.fx, fy: last.fy, fz: last.fz })
                        fociDialog.touch()
                    }
                }

                Text {
                    text: fociEditModel.count + " / " + maxFocusPoints + " profiles"
                    color: "#9FB3C8"
                    font.pixelSize: 11
                }

                Item { Layout.fillWidth: true }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    text: "Execution order:"
                    color: "white"
                    font.pixelSize: 12
                }

                TextField {
                    id: fociOrderField
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    font.pixelSize: 13
                    placeholderText: defaultOrderTextFor(fociEditModel.count) + "  (default)"
                    background: Rectangle { color: "#222"; border.color: "#999"; radius: 4 }
                    onTextEdited: {
                        fociDialog.workingOrderText = text
                        fociDialog.touch()
                    }

                    ToolTip.visible: orderHoverArea.containsMouse
                    ToolTip.delay: 400
                    ToolTip.text: "Comma-separated focus numbers. Entries may repeat and appear in any "
                                  + "order, e.g. \"1,2,1,3\" dwells twice on focus 1 per cycle. "
                                  + "Leave blank for one pass over every focus."
                    MouseArea {
                        id: orderHoverArea
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.NoButton
                        propagateComposedEvents: true
                    }
                }

                Button {
                    text: "Reset"
                    font.pixelSize: 11
                    implicitHeight: 30
                    implicitWidth: 64
                    leftPadding: 8
                    rightPadding: 8
                    enabled: fociDialog.workingOrderText !== ""
                    onClicked: {
                        fociDialog.workingOrderText = ""
                        fociOrderField.text = ""
                        fociDialog.touch()
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: fociDialog.workingError !== "" ? fociDialog.workingError : fociDialog.workingSummary
                color: fociDialog.workingError !== "" ? "#E67E22" : "#43BB57"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
        }

        footer: RowLayout {
            spacing: 8

            Item { Layout.fillWidth: true }

            Button {
                text: "Cancel"
                onClicked: fociDialog.close()
            }

            Button {
                text: "OK"
                enabled: fociDialog.workingError === ""
                onClicked: {
                    fociModel.clear()
                    for (var i = 0; i < fociEditModel.count; i++) {
                        var row = fociEditModel.get(i)
                        fociModel.append({ fx: row.fx, fy: row.fy, fz: row.fz })
                    }
                    executionOrderText = fociDialog.workingOrderText
                    // A hand-typed order identical to the default is stored
                    // as blank so later focus edits keep extending it.
                    if (executionOrderText.trim() === defaultExecutionOrderText()) {
                        executionOrderText = ""
                    }
                    syncInputsFromFocusOne()
                    updateFociValidation()
                    // Focus geometry changed: the device needs a re-Configure
                    // (or a direct pulse push if it is already configured).
                    fociDialog.close()
                    if (everConfigured && !controlsReadOnly) {
                        runBusy(function() { commitPulse() })
                    } else {
                        refreshPlot()
                    }
                }
            }

            Item { Layout.preferredWidth: 6 }
        }
    }

    // Function to apply loaded solution settings to UI controls
    function applySolutionSettings() {
        if (LIFUConnector.solutionLoaded) {
            applySettingsToUi(LIFUConnector.getLoadedSolutionSettings())
        }
    }

    // HEADER
    Text {
        text: "Device Controller"
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
        applySettingsToUi(LIFUConnector.getDefaultSolutionSettings())
        updateTrainIntervalValidation()
        controllerLogCheckbox.checked = LIFUConnector.isControllerTelemetryLoggingEnabled()
        controllerTelemetryLogPath = LIFUConnector.getControllerTelemetryLogPath()
    }
    
    // LAYOUT
    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        // Clear the "Device Controller" header: the columns are now tall
        // enough that centring no longer leaves a gap at the top.
        anchors.topMargin: 38
        anchors.bottomMargin: 14
        spacing: 20

        // Left Column (Input Panel)
        Rectangle {
            id: inputContainer
            width: 500
            // The old 630 left the solution controls over budget once the
            // focus row was added, pushing Load/Save outside the panel.
            height: 648
            color: "#1E1E20"
            radius: 10
            border.color: "#3E4E6F"
            border.width: 2

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12

                GroupBox {
                    id: voltageGroup
                    title: "Voltage"
                    Layout.fillWidth: true

                    readonly property bool sectionReadOnly: controlsReadOnly

                    label: Text {
                        text: voltageGroup.title
                        color: "white"
                        font: voltageGroup.font
                    }

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
                            property bool dirty: false
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "12.0"
                            color: getFieldColor(dirty, voltageGroup.sectionReadOnly)
                            enabled: !voltageGroup.sectionReadOnly
                            background: Rectangle {
                                color: voltageGroup.sectionReadOnly ? "#333" : "#222"
                                border.color: voltageGroup.sectionReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextEdited: dirty = true
                            onEditingFinished: commitDirtyField(voltage, commitVoltage)
                        }
                    }
                }

                GroupBox {
                    id: sequenceGroup
                    title: "Sequence Settings"
                    Layout.fillWidth: true

                    readonly property bool sectionReadOnly: controlsReadOnly

                    label: Text {
                        text: sequenceGroup.title
                        color: "white"
                        font: sequenceGroup.font
                    }

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
                            // Trigger Mode stays editable any time we're
                            // not actively sonicating. Even when a solution
                            // is loaded from file (which locks the other
                            // parameter fields) the user can still flip
                            // Single/Continuous/Sequence; once Configured
                            // the change commits via directSetSequence().
                            enabled: LIFUConnector.state !== 3
							
							background: Rectangle {
                                color: "#222"
                                border.color: "#999"
                                radius: 4
                            }
							
							onActivated: {
								var selectedIndex = triggerModeDropdown.currentText;
								console.log("Selected " + selectedIndex);
								if (everConfigured) {
									commitSequence()
								}
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
                            property bool dirty: false
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "100"
                            color: getFieldColor(dirty, sequenceGroup.sectionReadOnly, pulseIntervalActive)
                            enabled: !sequenceGroup.sectionReadOnly
                            background: Rectangle {
                                color: sequenceGroup.sectionReadOnly ? "#333" : "#222"
                                border.color: sequenceGroup.sectionReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextChanged: { updateTrainIntervalValidation(); updateFociValidation() }
                            onTextEdited: dirty = true
                            onEditingFinished: commitDirtyField(triggerPulseInterval, commitSequence)
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
                            property bool dirty: false
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "1"
                            color: getFieldColor(dirty, sequenceGroup.sectionReadOnly, pulseCountActive)
                            enabled: !sequenceGroup.sectionReadOnly
                            background: Rectangle {
                                color: sequenceGroup.sectionReadOnly ? "#333" : "#222"
                                border.color: sequenceGroup.sectionReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextChanged: { updateTrainIntervalValidation(); updateFociValidation() }
                            onTextEdited: dirty = true
                            onEditingFinished: commitDirtyField(triggerPulseCount, commitSequence)
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
                            property bool dirty: false
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "0"
                            color: getFieldColor(dirty, sequenceGroup.sectionReadOnly, trainIntervalActive)
                            enabled: !sequenceGroup.sectionReadOnly
                            background: Rectangle {
                                color: sequenceGroup.sectionReadOnly ? "#333" : "#222"
                                border.color: sequenceGroup.sectionReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextChanged: { updateTrainIntervalValidation(); updateFociValidation() }
                            onTextEdited: dirty = true
                            onEditingFinished: commitDirtyField(triggerPulseTrainInterval, commitSequence)
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
                            property bool dirty: false
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "1"
                            color: getFieldColor(dirty, sequenceGroup.sectionReadOnly, trainCountActive)
                            enabled: !sequenceGroup.sectionReadOnly
                            background: Rectangle {
                                color: sequenceGroup.sectionReadOnly ? "#333" : "#222"
                                border.color: sequenceGroup.sectionReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextEdited: dirty = true
                            onEditingFinished: commitDirtyField(triggerPulseTrainCount, commitSequence)
                        }
                    }
                }

                GroupBox {
                    id: pulseGroup
                    title: solutionLoaded ? "Pulse Settings (Delays and Apodizations Loaded Directly from Solution)" : "Pulse Settings"
                    Layout.fillWidth: true

                    readonly property bool sectionReadOnly: controlsReadOnly

                    label: Text {
                        text: pulseGroup.title
                        color: "white"
                        font: pulseGroup.font
                        elide: Text.ElideRight
                        width: pulseGroup.availableWidth
                    }

                    GridLayout {
                        columns: 2
                        width: parent.width
                        rowSpacing: 12

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
                            property bool dirty: false
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "400"
                            color: getFieldColor(dirty, pulseGroup.sectionReadOnly)
                            enabled: !pulseGroup.sectionReadOnly
                            background: Rectangle {
                                color: pulseGroup.sectionReadOnly ? "#333" : "#222"
                                border.color: pulseGroup.sectionReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextEdited: dirty = true
                            onEditingFinished: commitDirtyField(frequencyInput, commitPulse)
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
                            property bool dirty: false
                            Layout.preferredWidth: solutionConfigInputWidth
                            Layout.preferredHeight: 32
                            Layout.alignment: Qt.AlignLeft
                            font.pixelSize: 14
                            text: "200"
                            color: getFieldColor(dirty, pulseGroup.sectionReadOnly)
                            enabled: !pulseGroup.sectionReadOnly
                            background: Rectangle {
                                color: pulseGroup.sectionReadOnly ? "#333" : "#222"
                                border.color: pulseGroup.sectionReadOnly ? "#777" : "#999"
                                radius: 4
                            }
                            onTextChanged: updateFociValidation()
                            onTextEdited: dirty = true
                            onEditingFinished: commitDirtyField(durationInput, commitPulse)
                        }

                        // Beam Focus spans the full grid width below the Frequency/Duration rows.
                        // The X/Y/Z fields edit focus 1; additional foci (and
                        // the execution order the firmware cycles through) live
                        // in the Foci dialog on the row below.
                        ColumnLayout {
                            Layout.columnSpan: 2
                            Layout.fillWidth: true
                            Layout.topMargin: 6
                            spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
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
                                    property bool dirty: false
                                    Layout.preferredWidth: 56
                                    Layout.minimumWidth: 56
                                    Layout.maximumWidth: 56
                                    Layout.preferredHeight: 32
                                    font.pixelSize: 14
                                    text: "0"
                                    color: getFieldColor(dirty, pulseGroup.sectionReadOnly)
                                    enabled: !pulseGroup.sectionReadOnly
                                    background: Rectangle {
                                        color: pulseGroup.sectionReadOnly ? "#333" : "#222"
                                        border.color: pulseGroup.sectionReadOnly ? "#777" : "#999"
                                        radius: 4
                                    }
                                    onTextEdited: dirty = true
                                    onEditingFinished: commitDirtyField(xInput, commitPulse)
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
                                    property bool dirty: false
                                    Layout.preferredWidth: 56
                                    Layout.minimumWidth: 56
                                    Layout.maximumWidth: 56
                                    Layout.preferredHeight: 32
                                    font.pixelSize: 14
                                    text: "0"
                                    color: getFieldColor(dirty, pulseGroup.sectionReadOnly)
                                    enabled: !pulseGroup.sectionReadOnly
                                    background: Rectangle {
                                        color: pulseGroup.sectionReadOnly ? "#333" : "#222"
                                        border.color: pulseGroup.sectionReadOnly ? "#777" : "#999"
                                        radius: 4
                                    }
                                    onTextEdited: dirty = true
                                    onEditingFinished: commitDirtyField(yInput, commitPulse)
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
                                    property bool dirty: false
                                    Layout.preferredWidth: 56
                                    Layout.minimumWidth: 56
                                    Layout.maximumWidth: 56
                                    Layout.preferredHeight: 32
                                    font.pixelSize: 14
                                    text: "50"
                                    color: getFieldColor(dirty, pulseGroup.sectionReadOnly)
                                    enabled: !pulseGroup.sectionReadOnly
                                    background: Rectangle {
                                        color: pulseGroup.sectionReadOnly ? "#333" : "#222"
                                        border.color: pulseGroup.sectionReadOnly ? "#777" : "#999"
                                        radius: 4
                                    }
                                    onTextEdited: dirty = true
                                    onEditingFinished: commitDirtyField(zInput, commitPulse)
                                }
                            }
                        }

                        // Multi-focus row: opens the focus list editor and
                        // summarizes the resulting firing pattern.
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Button {
                                id: fociButton
                                // Name the action rather than the state: at one
                                // focus the label invites adding more, which is
                                // how the feature gets discovered at all.
                                text: (fociModel.count > 1
                                       ? "Focus Points (" + fociModel.count + ")"
                                       : "Add Focus Points…")
                                implicitHeight: 30
                                implicitWidth: 158
                                font.pixelSize: 12
                                // Material's default button padding elides
                                // the label at this size.
                                leftPadding: 10
                                rightPadding: 10
                                enabled: !pulseGroup.sectionReadOnly
                                background: Rectangle {
                                    color: !fociButton.enabled ? "#2A2D34"
                                           : fociButton.down ? "#2F333D"
                                           : fociHoverArea.containsMouse ? "#4A5162"
                                           : "#3A3F4B"
                                    radius: 4
                                    border.width: 1
                                    border.color: !fociButton.enabled ? "#777"
                                                  : fociHoverArea.containsMouse ? "#7FA6D9"
                                                  : "#BDC3C7"
                                    Behavior on color { ColorAnimation { duration: 90 } }
                                }
                                onClicked: openFociDialog()

                                ToolTip.visible: fociHoverArea.containsMouse
                                ToolTip.delay: 400
                                ToolTip.text: "Add up to " + maxFocusPoints + " focus points. The transmitter "
                                              + "cycles through them automatically during sonication."
                                MouseArea {
                                    id: fociHoverArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    acceptedButtons: Qt.NoButton
                                    cursorShape: Qt.PointingHandCursor
                                    propagateComposedEvents: true
                                }
                            }

                            // The summary/error text opens the same dialog, so
                            // the whole row is one target instead of a 158 px
                            // button next to dead text.
                            Text {
                                id: fociSummaryText
                                text: fociError !== "" ? fociError : fociSummary
                                color: fociError !== "" ? "#E67E22"
                                       : fociSummaryHover.containsMouse ? "#CFE0F0" : "#9FB3C8"
                                font.pixelSize: 11
                                font.underline: fociSummaryHover.containsMouse
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                                Layout.preferredHeight: 30
                                verticalAlignment: Text.AlignVCenter

                                MouseArea {
                                    id: fociSummaryHover
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    enabled: fociButton.enabled
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: openFociDialog()
                                }
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
                        id: loadPresetButton
                        property bool visualPressed: false
                        text: "Load"
                        Layout.fillWidth: true
                        enabled: (!solutionLoaded) && (LIFUConnector.state <2) && !visualPressed
                        background: Rectangle {
                            color: (loadPresetButton.down || loadPresetButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            runWithButtonFeedback(loadPresetButton, function() {
                                loadPresetDialog.open()
                            })
                        }
                    }

                    Button {
                        id: saveSolutionButton
                        property bool visualPressed: false
                        text: "Save"
                        Layout.fillWidth: true
                        enabled: !visualPressed
                        background: Rectangle {
                            color: (saveSolutionButton.down || saveSolutionButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            runWithButtonFeedback(saveSolutionButton, function() {
                                saveSolutionDialog.open()
                            })
                        }
                    }

                    Button {
                        id: editSolutionButton
                        property bool visualPressed: false
                        text: "Edit Solution"
                        Layout.fillWidth: true
                        enabled: solutionLoaded && (LIFUConnector.state < 3) && !visualPressed
                        background: Rectangle {
                            color: (editSolutionButton.down || editSolutionButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                            radius: 4
                            border.color: "#BDC3C7"
                        }
                        onClicked: {
                            runWithButtonFeedback(editSolutionButton, function() {
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
            height: 648
            spacing: 20

            Rectangle {
                id: graphContainer
                Layout.fillWidth: true
                Layout.fillHeight: false
                Layout.preferredHeight: 398
                Layout.maximumHeight: 406
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

                Button {
                    id: refreshPlotButton
                    text: "\u21bb Refresh"
                    font.pixelSize: 13
                    width: 110
                    height: 44
                    anchors.bottom: parent.bottom
                    anchors.right: parent.right
                    anchors.margins: 6
                    background: Rectangle {
                        color: refreshPlotButton.down ? "#2F333D" : "#3A3F4B"
                        radius: 4
                        border.color: "#BDC3C7"
                        opacity: 0.85
                    }
                    onClicked: {
                        refreshPlot()
                    }
                }
            }

            // Status Panel (Connection Indicators + Controls)
            Rectangle {
                id: statusPanel
                Layout.fillWidth: true
                Layout.preferredHeight: 230
                Layout.minimumHeight: 230
                color: "#252525"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 10

                    // Status text, module count, and HV enable mode row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Text {
                            id: statusText
                            text: getStatusText()
                            font.pixelSize: 14
                            color: getStatusColor()
                            horizontalAlignment: Text.AlignHCenter
                            Layout.fillWidth: true
                            SequentialAnimation on opacity {
                                running: LIFUConnector.state === 3
                                loops: Animation.Infinite
                                NumberAnimation { from: 1.0; to: 0.35; duration: 500 }
                                NumberAnimation { from: 0.35; to: 1.0; duration: 500 }
                            }

                            MouseArea {
                                id: statusTooltipArea
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.NoButton
                                ToolTip.visible: containsMouse && getStatusTooltip().length > 0
                                ToolTip.text: getStatusTooltip()
                                ToolTip.delay: 400
                            }
                        }

                        Text {
                            text: "#TX: " + LIFUConnector.queryNumModulesConnected
                            font.pixelSize: 12
                            color: "#BDC3C7"
                            verticalAlignment: Text.AlignVCenter
                        }

                        
                        Text {
                            text: "HV:"
                            font.pixelSize: 12
                            color: "#BDC3C7"
                            verticalAlignment: Text.AlignVCenter
                        }
                        
                        ComboBox {
                            id: hvEnableModeComboBox
                            model: LIFUConnector.getHvEnableModes()
                            implicitWidth: 140
                            implicitHeight: 26
                            font.pixelSize: 12
                            enabled: LIFUConnector.state !== 3  // Disable when running
                            Component.onCompleted: currentIndex = LIFUConnector.hvEnableMode
                            background: Rectangle {
                                color: "#222"
                                border.color: hvEnableModeComboBox.enabled ? "#999" : "#555"
                                radius: 4
                            }
                            
                            // Custom delegate to handle disabled "ON" option when HV not connected
                            delegate: ItemDelegate {
                                width: hvEnableModeComboBox.width
                                height: 26
                                enabled: !(index === 1 && !LIFUConnector.hvConnected)  // Disable "ON" when HV not connected
                                
                                Rectangle {
                                    anchors.fill: parent
                                    color: parent.enabled ? (parent.hovered ? "#333" : "#222") : "#1A1A1A"
                                    
                                    Text {
                                        anchors.centerIn: parent
                                        text: model[modelData] || modelData
                                        color: parent.parent.enabled ? "white" : "#666"
                                        font.pixelSize: 12
                                    }
                                }
                            }
                            
                            onActivated: (index) => {
                                if (index < 0) {
                                    return
                                }
                                // Block selecting "ON" while HV is disconnected
                                if (index === 1 && !LIFUConnector.hvConnected) {
                                    currentIndex = LIFUConnector.hvEnableMode
                                    return
                                }
                                if (index !== LIFUConnector.hvEnableMode) {
                                    LIFUConnector.setHvEnableMode(index)
                                }
                            }
                        }
                    }

                    // Connection Indicators (TX, HV) and progress bar.
                    // The outer row spans the full width of the status
                    // panel; the progress bar absorbs whatever space
                    // the LEDs and their text labels do not consume.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 20

                        // TX LED
                        RowLayout {
                            spacing: 5
                            Rectangle {
                                id: txIndicator
                                width: 20
                                height: 20
                                radius: 10
                                color: getTXIndicatorColor()
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
                                // Red when HV is not connected, green while the
                                // rail is energized, dim cyan when connected
                                // but the rail is off.
                                color: getHVIndicatorColor()
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

                        // Sonication progress bar. Lives to the right of
                        // the HV LED. State and fill are driven by the
                        // Start/Stop buttons and the firmware's
                        // unsolicited STATUS frames.
                        Rectangle {
                            id: progressBar
                            Layout.fillWidth: true
                            Layout.minimumWidth: 120
                            Layout.preferredHeight: 22
                            Layout.maximumHeight: 22
                            radius: 4
                            color: "#1B1D22"
                            border.color: "#3E4E6F"
                            border.width: 1

                            Rectangle {
                                id: progressFill
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                anchors.margins: 2
                                width: Math.max(0, (parent.width - 4) * getProgressFillFraction())
                                radius: 3
                                color: getProgressColor()
                                Behavior on width { NumberAnimation { duration: 120 } }
                                Behavior on color { ColorAnimation { duration: 120 } }
                            }

                            Text {
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                text: getProgressText()
                                font.pixelSize: 12
                                font.weight: Font.Bold
                                color: "white"
                                style: Text.Outline
                                styleColor: "#000000"
                            }
                        }
                    }

                    // Engineering override, deliberately next to Configure --
                    // the action it changes -- and on the right panel so it
                    // stays visible (and legible) while a sonication runs and
                    // the solution controls are read-only.
                    CheckBox {
                        id: safetyBypassCheck
                        Layout.fillWidth: true
                        implicitHeight: 24
                        padding: 0
                        checked: LIFUConnector.safetyBypassEnabled
                        enabled: LIFUConnector.state !== 3
                        text: LIFUConnector.safetyBypassEnabled
                              ? "⚠  Safety limits BYPASSED — up to 100% duty cycle"
                              : "Bypass duty-cycle / voltage safety limits"
                        font.pixelSize: 12
                        contentItem: Text {
                            text: safetyBypassCheck.text
                            font.pixelSize: safetyBypassCheck.font.pixelSize
                            font.bold: LIFUConnector.safetyBypassEnabled
                            color: !safetyBypassCheck.enabled ? "#777"
                                   : LIFUConnector.safetyBypassEnabled ? "#E67E22" : "#BBB"
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            leftPadding: safetyBypassCheck.indicator.width + 6
                        }

                        // Driven purely from the connector: the box only moves
                        // once the bypass is actually armed, so a cancelled
                        // dialog cannot leave it showing a state we are not in.
                        onToggled: {
                            if (checked && !LIFUConnector.safetyBypassEnabled) {
                                checked = Qt.binding(function() { return LIFUConnector.safetyBypassEnabled })
                                safetyBypassDialog.open()
                            } else if (!checked && LIFUConnector.safetyBypassEnabled) {
                                LIFUConnector.setSafetyBypass(false)
                            }
                        }

                        ToolTip.visible: safetyBypassHover.hovered
                        ToolTip.delay: 400
                        ToolTip.text: "Skips the SDK's check_solution() duty-cycle, voltage and "
                                      + "sequence-duration limits at Configure. Bench testing only."
                        HoverHandler { id: safetyBypassHover }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.minimumHeight: 24
                        spacing: 10

                        CheckBox {
                            id: controllerLogCheckbox
                            implicitHeight: 24
                            padding: 0
                            Layout.preferredHeight: 24
                            Layout.alignment: Qt.AlignVCenter
                            text: "Save temp/voltage log"
                            checked: false
                            enabled: true
                            contentItem: Text {
                                text: controllerLogCheckbox.text
                                color: !controllerLogCheckbox.enabled ? "#777" : "#BBB"
                                font.pixelSize: 12
                                verticalAlignment: Text.AlignVCenter
                                leftPadding: controllerLogCheckbox.indicator.width + 6
                            }
                            onToggled: {
                                LIFUConnector.setControllerTelemetryLoggingEnabled(checked)
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            Layout.alignment: Qt.AlignVCenter
                            text: controllerTelemetryLogPath !== ""
                                  ? ("Log: " + controllerTelemetryLogPath)
                                  : ""
                            color: "#7FA2C7"
                            font.pixelSize: 11
                            wrapMode: Text.NoWrap
                            elide: Text.ElideMiddle
                            maximumLineCount: 1
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: Text.AlignRight
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
                            // Configure can run any time TX is connected and the
                            // device is not actively transmitting. Re-clicking
                            // re-pushes the current field values as the active
                            // solution. Disabled while any connected device is
                            // running firmware below the app's hard minimum --
                            // the operator must update from the Settings tab.
                            // Also blocked while the focus list / execution
                            // order cannot produce a programmable solution;
                            // fociError explains why next to the Foci button.
                            enabled: LIFUConnector.txConnected && LIFUConnector.state <2 && !visualPressed
                                     && !LIFUConnector.firmwareUpdateRequired
                                     && fociError === ""
                            background: Rectangle {
                                color: (configureButton.down || configureButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            ToolTip.visible: configureHoverArea.containsMouse
                                             && (LIFUConnector.firmwareUpdateRequired || fociError !== "")
                            ToolTip.text: LIFUConnector.firmwareUpdateRequired
                                          ? "Configure is disabled until firmware is updated to the minimum required version (Settings tab)."
                                          : fociError
                            ToolTip.delay: 400
                            MouseArea {
                                id: configureHoverArea
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.NoButton
                                propagateComposedEvents: true
                            }
                            onClicked: {
                                runWithButtonFeedback(configureButton, function() {
                                    resetProgressIdle()
                                    LIFUConnector.configure_transmitter(xInput.text, yInput.text,
                                        zInput.text,  frequencyInput.text, voltage.text, triggerPulseInterval.text, triggerPulseCount.text,
                                        triggerPulseTrainInterval.text, triggerPulseTrainCount.text, durationInput.text,
                                        triggerModeDropdown.currentText, fociArray(), executionOrderArray());
                                    // If configure_transmitter succeeded synchronously the
                                    // state is now >= CONFIGURED. Use that as the success cue
                                    // to mark every field as in-sync (green).
                                    if (LIFUConnector.state >= 2) {
                                        everConfigured = true
                                        clearAllDirty()
                                    }
                                    configuredModuleCount = LIFUConnector.queryNumModulesConnected
                                    refreshPlot();
                                    statusOverrideText = ""
                                })
                            }
                        }

                        Button {
                            id: startButton
                            property bool visualPressed: false
                            text: "Start"
                            Layout.fillWidth: true
                            // Start requires a configured TX (READY) and an
                            // HV rail that is allowed to energize.
                            enabled: (LIFUConnector.state === 2)
                                     && LIFUConnector.hvConnected
                                     && LIFUConnector.hvEnableMode !== 2
                                     && !visualPressed
                            background: Rectangle {
                                color: (startButton.down || startButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            onClicked: {
                                runWithButtonFeedback(startButton, function() {
                                    console.log("Starting Sonication...");
                                    startProgressFromUi()
                                    LIFUConnector.start_sonication();
                                })
                            }
                        }

                        Button {
                            id: stopButton
                            property bool visualPressed: false
                            text: "Stop"
                            Layout.fillWidth: true
                            enabled: (LIFUConnector.state === 3) && !visualPressed  // RUNNING
                            background: Rectangle {
                                color: (stopButton.down || stopButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            onClicked: {
                                runWithButtonFeedback(stopButton, function() {
                                    console.log("Stopping Sonication...");
                                    if (progressState === "running") {
                                        progressState = "stopped"
                                    }
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
                            enabled: (LIFUConnector.state >= 2 && LIFUConnector.state !== 3) && !visualPressed  // READY (configured), not RUNNING
                            background: Rectangle {
                                color: (resetButton.down || resetButton.visualPressed) ? "#2F333D" : "#3A3F4B"
                                radius: 4
                                border.color: "#BDC3C7"
                            }
                            onClicked: {
                                runWithButtonFeedback(resetButton, function() {
                                    console.log("Resetting parameters...");
                                    resetProgressIdle()
                                    // If HV is pinned ON, drop it to OFF so
                                    // the rail de-energizes as part of reset.
                                    if (LIFUConnector.hvEnableMode === 1) {
                                        LIFUConnector.setHvEnableMode(2)
                                    }
                                    applySettingsToUi(LIFUConnector.getDefaultSolutionSettings())
                                    LIFUConnector.reset_configuration();
                                    // Push the (now-default) voltage down to
                                    // the HV controller so the hardware
                                    // setpoint matches what the UI shows,
                                    // independent of whether a solution had
                                    // been loaded from file.
                                    if (LIFUConnector.hvConnected) {
                                        LIFUConnector.directSetVoltage(voltage.text)
                                    }
                                    // Clear the plot back to the placeholder.
                                    ultrasoundGraph.source = "../assets/images/empty_graph.png"
                                })
                            }
                        }
                    }
                }
            }
        }
    }

    // **Connections for LIFUConnector signals**
    Connections {
        target: LIFUConnector

        function onSignalConnected(descriptor, port) {
            console.log(descriptor + " connected on " + port);
            if (descriptor === "HV") {
                // Force ComboBox to refresh its delegate states when HV connects
                // This ensures the "ON" option gets enabled properly
                hvEnableModeComboBox.model = []
                hvEnableModeComboBox.model = LIFUConnector.getHvEnableModes()
                // Seed the indicator with the current rail state.
                LIFUConnector.queryPowerStatus()
            }
            statusOverrideText = ""
        }

        function onSignalDisconnected(descriptor, port) {
            console.log(descriptor + " disconnected from " + port);
            if (descriptor === "TX") {
                txTemperatures = [];
                configuredModuleCount = 0;
                resetProgressIdle();
            }
            if (descriptor === "HV") {
                hvPositiveRail = NaN;
                hvNegativeRail = NaN;
                hvOn = false;
                // Force ComboBox to refresh its delegate states when HV disconnects
                // This ensures the "ON" option gets disabled properly
                hvEnableModeComboBox.model = []
                hvEnableModeComboBox.model = LIFUConnector.getHvEnableModes()
            }
            statusOverrideText = ""
        }

        function onSignalDataReceived(descriptor, message) {
            // console.log("Data from " + descriptor + ": " + message);
        }

        function onPlotGenerated(imageData) {
            ultrasoundGraph.updateImage("data:image/png;base64," + imageData);
            statusOverrideText = "";
        }

        // Solution loading signal handlers
        function onSolutionFileLoaded(solutionName, message) {
            console.log("Solution loaded: " + solutionName + " - " + message);
            applySolutionSettings();
            // A loaded solution always describes a fixed pulse-train
            // sequence, so default the Trigger Mode to "Sequence".
            triggerModeDropdown.currentIndex = 2;
            resetProgressIdle();
            statusOverrideText = "";
        }

        function onSolutionLoadError(errorMessage) {
            console.error("Solution load error: " + errorMessage);
            statusOverrideText = "Error: " + errorMessage;
            solutionLoadErrorDialog.errorMessage = errorMessage
            solutionLoadErrorDialog.open()
        }

        function onSolutionStateChanged() {
            console.log("Solution state changed - loaded:", LIFUConnector.solutionLoaded);
            if (!LIFUConnector.solutionLoaded) {
                statusOverrideText = "";
            }
        }

        function onSolutionSaveStatus(success, message) {
            if (success) {
                statusOverrideText = message;
            } else {
                statusOverrideText = "Error: " + message;
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

        // Unsolicited TX STATUS frames carry pulse-train counts. The
        // firmware emits one frame at the end of each pulse train, so
        // PULSE_TRAIN:[k/N] means train k just finished and (for non-
        // final trains in Sequence/Continuous mode) train k+1 is
        // starting -- show that as the currently-running index.
        // The final frame for Single/Sequence runs is STATUS:STOPPED
        // with k==N; we use that to flip to FINISHED. Single mode
        // never emits an intermediate RUNNING frame so this handler
        // is the only place finishing is detected for that mode
        // (triggerStateChanged is not emitted because the parsed
        // trigger state never crossed False -> True).
        function onSonicationProgressUpdated(pt_curr, pt_total, p_curr, p_total) {
            if (progressState !== "running") {
                return
            }
            if (progressMode === "Continuous") {
                // Show the next train index that the device just
                // started. Continuous never "finishes" on its own;
                // user-initiated stop is handled by the Stop button.
                progressCurrent = pt_curr + 1
                return
            }
            // Single / Sequence: if this frame's index matches the
            // total, the run is complete. Otherwise advance to the
            // next train (k+1).
            if (pt_total > 0 && pt_curr >= pt_total) {
                progressCurrent = progressTotal > 0 ? progressTotal : pt_curr
                progressState = "finished"
                return
            }
            var next = pt_curr + 1
            if (progressTotal > 0 && next > progressTotal) {
                next = progressTotal
            }
            progressCurrent = next
        }

        function onTriggerStateChanged(running) {
            // Defensive fallback: if STATUS:STOPPED arrives but the
            // counts didn't match total (e.g. firmware abort mid-run),
            // still leave RUNNING state. The Stop button already
            // flips progressState to "stopped" before invoking
            // stop_sonication() so we don't override that here.
            if (!running && progressState === "running" && progressMode === "Continuous") {
                progressState = "stopped"
            }
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

        function onControllerTelemetryLoggingChanged(enabled, logPath) {
            controllerTelemetryLogPath = logPath
            if (controllerLogCheckbox.checked !== enabled) {
                controllerLogCheckbox.checked = enabled
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
            statusOverrideText = "";

            if (previousConnectorState === 3 && state !== 3) {
                clearStatusTelemetry();
            }

            // Track whether the device has been configured at least once on
            // the current solution. Crossing the CONFIGURED boundary in either
            // direction also resets per-field dirty markers.
            var crossedConfiguredBoundary = (previousConnectorState < 2) !== (state < 2)
            if (state >= 2) {
                if (configuredModuleCount <= 0) {
                    configuredModuleCount = LIFUConnector.queryNumModulesConnected
                }
                everConfigured = true
            } else {
                everConfigured = false
                // Dropping below CONFIGURED (e.g. after Reset) stops the
                // backend telemetry polling, so blank the cached readings
                // rather than leaving stale values on screen.
                if (previousConnectorState >= 2) {
                    clearStatusTelemetry();
                    // Also clear the progress UI so a navigation-triggered
                    // reset (e.g. switching to Settings) doesn't leave a
                    // stale "stopped" / "finished" banner behind.
                    resetProgressIdle()
                }
            }
            if (crossedConfiguredBoundary) {
                clearAllDirty()
            }

            previousConnectorState = state;
        }
        
        function onHvEnableModeChanged(mode) {
            if (hvEnableModeComboBox.currentIndex !== mode) {
                hvEnableModeComboBox.currentIndex = mode
            }
        }
    }


    Component.onDestruction: {
        console.log("Closing UI, clearing LIFUConnector...");
    }

    BusyOverlay {
        id: busyOverlay
        visible: controllerPage.busy
    }
}
