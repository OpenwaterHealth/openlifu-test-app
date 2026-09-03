import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import QtQuick.Dialogs

Rectangle {
    id: settingsPage
    width: parent.width
    height: parent.height
    color: "#29292B"
    radius: 20
    opacity: 0.95

    // ----------------------------------------------------------------
    // Internal state helpers
    // ----------------------------------------------------------------
    property bool consoleUpdating: false
    property bool transmitterUpdating: false
    property int txModuleCount: 0
    property bool txLoading: false
    property var configTargetModel: []
    property var modules: []  // Device info for all modules

    // Console firmware to install: the operator-browsed file if one is
    // selected, otherwise the signed image included with the SDK. The path
    // field starts empty (meaning "use the included firmware"); Browse is an
    // override, offered only once the console is on the secure bootloader
    // (>= 1.2.6). See the console firmware card below.
    readonly property string consoleEffectivePath:
        consoleFwPath.text.length > 0
            ? consoleFwPath.text
            : LIFUConnector.getDefaultFirmwarePath("console")

    // Transmitter firmware to install: same rule as the console — the
    // operator-browsed file if one is selected, otherwise the signed image
    // included with the SDK. The path field starts empty ("use the included
    // firmware"); this resolves it so the version display and update button
    // show/act on the bundled firmware instead of "none".
    readonly property string transmitterEffectivePath:
        transmitterFwPath.text.length > 0
            ? transmitterFwPath.text
            : LIFUConnector.getDefaultFirmwarePath("transmitter")

    // Font for the small "Check for Updates" icon buttons. Other widgets
    // that need icons (e.g. IconButton.qml) load their own copy.
    FontLoader {
        id: settingsIconFont
        source: "../assets/fonts/keenicons-outline.ttf"
    }

    // ----------------------------------------------------------------
    // Firmware-version helpers (used by the colored "current firmware"
    // labels). Compares the live device version against the operator's
    // currently selected firmware file and the minimum-version pin
    // baked into ``lifu_constants``. Result tiers:
    //   "ok"               -- device >= file (green)
    //   "update_available" -- device >= MIN but < file (yellow)
    //   "update_required"  -- device < MIN (red)
    //   "unknown"          -- can't parse the device version (gray)
    // ----------------------------------------------------------------
    function _parseFwVersion(s) {
        if (!s) return null
        var m = String(s).match(/(\d+)\.(\d+)\.(\d+)/)
        if (!m) return null
        return [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])]
    }
    function _cmpFwVersion(a, b) {
        if (a === null || b === null) return 0
        for (var i = 0; i < 3; i++) {
            if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1
        }
        return 0
    }

    // True when ``ver`` parses AND is below the [maj, min, patch] triple.
    // Unparseable versions ("—", "Error") return false — the update stays
    // enabled and the SDK's own auto-detect/guards decide at run time.
    function _txVerBelow(ver, triple) {
        var d = _parseFwVersion(ver)
        return d !== null && _cmpFwVersion(d, triple) < 0
    }

    // ----------------------------------------------------------------
    // Transmitter update gates (mirrored by guards in lifu_settings.py).
    // Apps <= 2.0.3 predate both bootloaders and slave-I2C updates:
    //   * a <= 2.0.3 SLAVE cannot be updated over I2C at all — its DFU
    //     entry jumps to the STM32 ROM loader, unreachable through the
    //     master's I2C passthrough. It must be connected as the USB
    //     master (by itself) and updated as module 0.
    //   * a <= 2.0.3 MASTER may only be updated as a SINGLE-module
    //     system (any number of attached slaves blocks it): the ROM-DFU
    //     migration reflashes the whole chip and those apps can't
    //     coordinate the slaves.
    // ----------------------------------------------------------------
    readonly property bool txSelectedIsSlave: txModuleSelector.currentIndex > 0
    readonly property bool txSlaveTooOld:
        txSelectedIsSlave && _txVerBelow(txCurrentVersion.text, [2, 0, 4])
    readonly property bool txMasterTooOldMultiModule:
        !txSelectedIsSlave && txModuleCount > 1
        && _txVerBelow(txCurrentVersion.text, [2, 0, 4])
    readonly property bool txUpdateBlocked:
        txSlaveTooOld || txMasterTooOldMultiModule
    function firmwareVersionColor(deviceVersion, minVersion, fileVersion) {
        var d = _parseFwVersion(deviceVersion)
        if (d === null) return "#BDC3C7"
        var minV = _parseFwVersion(minVersion)
        var fileV = _parseFwVersion(fileVersion)
        if (minV !== null && _cmpFwVersion(d, minV) < 0) return "#E74C3C"  // red
        if (fileV !== null && _cmpFwVersion(d, fileV) < 0) return "#F39C12"  // yellow
        return "#2ECC71"  // green
    }

    // Color the firmware-file version label by how it compares to the
    // hard MIN and to whatever the device is currently running.
    //   orange -- file is itself below MIN (flashing this won't fix
    //             the lockout; offer it but warn)
    //   yellow -- file is below the device version (downgrade)
    //   green  -- file is at/above MIN AND at/above device version
    // Operators are not blocked from downgrading; this is purely
    // informational.
    function fileVersionColor(fileVersion, minVersion, deviceVersion) {
        var f = _parseFwVersion(fileVersion)
        if (f === null) return "#BDC3C7"
        var minV = _parseFwVersion(minVersion)
        var devV = _parseFwVersion(deviceVersion)
        if (minV !== null && _cmpFwVersion(f, minV) < 0) return "#E67E22"  // orange
        if (devV !== null && _cmpFwVersion(f, devV) < 0) return "#F1C40F"  // yellow (downgrade)
        return "#2ECC71"  // green
    }

    // Canonical "M.m.p" form, stripping any "v" / "sim-" prefix the
    // hardware reports. Returns the input verbatim when unparseable
    // so the button text still says something rather than "undefined".
    function _formatFwVersion(s) {
        var p = _parseFwVersion(s)
        if (p === null) return String(s || "?")
        return p[0] + "." + p[1] + "." + p[2]
    }

    // FULL version for display: keeps any pre-release / build suffix
    // ("1.2.6-rc.5") while still stripping a leading "v" / "sim-" prefix,
    // so an rc build is shown as an rc rather than collapsed to "1.2.6".
    // Version *comparisons* still use the M.m.p triple (_parseFwVersion) —
    // that is what the SBSFU header encodes and the bootloader enforces.
    function _fullFwVersion(s) {
        if (!s) return "?"
        var m = String(s).match(/\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?/)
        return m ? m[0] : String(s)
    }

    // Color tier for the Update Firmware button:
    //   red    -- this update lifts the device from below MIN to at/above MIN
    //   yellow -- this update is a downgrade, OR the file is still below MIN
    //   blue   -- normal actionable update / reinstall (already compliant,
    //             low-urgency but still a valid, clickable action)
    // Falls back to red when versions can't be parsed (status quo).
    // NOTE: this must NOT return the disabled colour (#7F8C8D) for any
    // enabled state, or a valid reinstall looks greyed-out / disabled.
    function updateButtonColor(deviceVersion, fileVersion, minVersion) {
        var d = _parseFwVersion(deviceVersion)
        var f = _parseFwVersion(fileVersion)
        var minV = _parseFwVersion(minVersion)
        if (d === null || f === null || minV === null) return "#E74C3C"
        var dBelowMin = _cmpFwVersion(d, minV) < 0
        var fBelowMin = _cmpFwVersion(f, minV) < 0
        var diff = _cmpFwVersion(f, d)
        if (diff > 0 && dBelowMin && !fBelowMin) return "#E74C3C"  // red
        if (diff < 0) return "#F1C40F"                              // yellow (downgrade)
        if (diff > 0 && fBelowMin) return "#F1C40F"                 // yellow (still below MIN)
        return "#4A90E2"                                            // blue (reinstall / compliant)
    }

    // "Upgrade Firmware 1.2.5 -> 1.2.6-rc.5" / "Downgrade Firmware ..."
    // / "Reinstall Firmware v1.2.6-rc.5" / fallback "Update Firmware".
    // Labels show the FULL version (pre-release suffix included) so an rc
    // build reads as an rc; the upgrade/downgrade/reinstall decision still
    // comes from the M.m.p triple the bootloader actually enforces.
    function updateButtonText(deviceVersion, fileVersion) {
        var d = _parseFwVersion(deviceVersion)
        var f = _parseFwVersion(fileVersion)
        if (f === null) return "Update Firmware"
        if (d === null) return "Install Firmware v" + _fullFwVersion(fileVersion)
        var diff = _cmpFwVersion(f, d)
        if (diff > 0) return "Upgrade Firmware " + _fullFwVersion(deviceVersion)
            + " → " + _fullFwVersion(fileVersion)
        if (diff < 0) return "Downgrade Firmware " + _fullFwVersion(deviceVersion)
            + " → " + _fullFwVersion(fileVersion)
        // Same M.m.p but a different build string (e.g. rc -> release) is
        // still a meaningful change, so show both sides rather than "v X".
        var dFull = _fullFwVersion(deviceVersion)
        var fFull = _fullFwVersion(fileVersion)
        if (dFull !== fFull) return "Install Firmware " + dFull + " → " + fFull
        return "Reinstall Firmware v" + fFull
    }

    function rebuildConfigTargets() {
        var items = []
        // Console user config not yet supported in firmware – enable when ready:
        // if (LIFUConnector.hvConnected) items.push("Console")
        if (LIFUConnector.txConnected) {
            for (var i = 0; i < txModuleCount; i++) items.push("TX " + i)
        }
        configTargetModel = items
    }

    function queryTxModules() {
        txLoading = true
        txQueryTimer.start()
    }

    // Query versions if already connected. BOTH firmware path fields are
    // left EMPTY by default: an empty field means "use the firmware included
    // with the SDK". Browse only overrides it, and only once the unit is on
    // its secure bootloader (console >= 1.2.6, transmitter >= 2.0.8);
    // otherwise the operator must first update to the included firmware.
    Component.onCompleted: {
        if (LIFUConnector.hvConnected) {
            consoleCurrentVersion.text = "Reading…"
            LIFUConnector.readHvFirmwareVersion()
        }
        if (LIFUConnector.txConnected) {
            queryTxModules()
        }
        rebuildConfigTargets()
        // Pages live in a StackLayout and are all instantiated up-front, so
        // this page can be the visible one already at creation time.
        if (visible) LIFUConnector.pauseMonitoring(true)
    }

    // Stop background telemetry (module temperatures, trigger/power status)
    // while Settings is showing. Those 1 Hz reads race the firmware queries
    // and DFU traffic this page issues, producing UART timeouts. Resume on
    // leaving — unless a firmware update is still in flight, which manages
    // the pause itself and must not be interrupted.
    onVisibleChanged: {
        if (visible) {
            LIFUConnector.pauseMonitoring(true)
        } else if (!consoleUpdating && !transmitterUpdating) {
            LIFUConnector.pauseMonitoring(false)
        }
    }

    // Small delay so the busy indicator renders before the blocking query
    Timer {
        id: txQueryTimer
        interval: 50
        running: false
        onTriggered: LIFUConnector.queryNumModules()
    }

    // Delay on fresh TX connection to let the device stabilise
    Timer {
        id: txConnectTimer
        interval: 1500
        running: false
        onTriggered: queryTxModules()
    }

    // Delay on fresh HV connection before reading version
    Timer {
        id: hvConnectTimer
        interval: 500
        running: false
        onTriggered: {
            consoleCurrentVersion.text = "Reading…"
            LIFUConnector.readHvFirmwareVersion()
        }
    }

    // ----------------------------------------------------------------
    // Signal handlers – firmware update backend
    // ----------------------------------------------------------------
    Connections {
        target: LIFUConnector

        function onFwVersionRead(deviceType, version) {
            if (deviceType === "console") {
                consoleCurrentVersion.text = version
            } else if (deviceType.startsWith("transmitter")) {
                txCurrentVersion.text = version
            }
        }

        function onHvConnectedChanged() {
            if (LIFUConnector.hvConnected) {
                hvConnectTimer.start()
            } else {
                hvConnectTimer.stop()
                consoleCurrentVersion.text = "—"
            }
            rebuildConfigTargets()
        }

        function onFwUpdateProgress(label, written, total) {
            let pct = (total > 0) ? Math.round(written * 100 / total) : 0
            fwUpdateDialog.progressValue = pct / 100.0
            fwUpdateDialog.progressLabel = label + ": " + written + " / " + total + " B  (" + pct + "%)"
        }

        function onFwUpdateStatus(deviceType, success, message) {
            fwUpdateDialog.statusMessage = message
            fwUpdateDialog.statusSuccess = success
            fwUpdateDialog.statusColor = success ? "#2ECC71" : (message.startsWith("Starting") ? "#F39C12" : "#E74C3C")
            if (success) {
                fwUpdateDialog.progressValue = 1.0
                fwUpdateDialog.updateDone = true
                settingsPage.consoleUpdating = false
                settingsPage.transmitterUpdating = false
            } else if (message.toLowerCase().includes("failed") || message.toLowerCase().includes("error")) {
                fwUpdateDialog.updateDone = true
                settingsPage.consoleUpdating = false
                settingsPage.transmitterUpdating = false
            }
        }

        function onTxConnectedChanged() {
            if (LIFUConnector.txConnected) {
                txConnectTimer.start()
            } else {
                txConnectTimer.stop()
                txQueryTimer.stop()
                settingsPage.txLoading = false
                settingsPage.txModuleCount = 0
                txCurrentVersion.text = "\u2014"
                modules = []  // Clear device info when disconnected
            }
            rebuildConfigTargets()
        }

        function onNumModulesUpdated() {
            settingsPage.txModuleCount = LIFUConnector.queryNumModulesConnected
            settingsPage.txLoading = false
            // Auto-read version for whichever module is currently selected
            if (settingsPage.txModuleCount > 0) {
                txCurrentVersion.text = "Reading…"
                LIFUConnector.readTxFirmwareVersion(txModuleSelector.currentIndex)
                // Also fetch device info for all modules
                LIFUConnector.queryTxInfo()
            }
            rebuildConfigTargets()
        }

        function onUserConfigRead(target, jsonStr) {
            userConfigEditor.text = jsonStr
        }

        function onUserConfigStatus(target, success, message) {
            // Flash the status text briefly; reuse the editor placeholder area
            userConfigStatusText.text = message
            userConfigStatusText.color = success ? "#2ECC71" : "#E74C3C"
            userConfigStatusText.visible = true
            userConfigStatusHideTimer.restart()
        }

        function onTestReportLoaded(success, message) {
            // Flash the status text briefly; reuse the editor placeholder area
            userConfigStatusText.text = message
            userConfigStatusText.color = success ? "#2ECC71" : "#E74C3C"
            userConfigStatusText.visible = true
            userConfigStatusHideTimer.restart()
        }

        function onTxDeviceInfoReceived(modulesList) {
            // Store device info for all modules
            modules = modulesList.map(function(m) {
                return {
                    firmwareVersion: m.firmwareVersion,
                    deviceId: m.deviceId
                }
            })
        }
    }

    // ----------------------------------------------------------------
    // File dialogs
    // ----------------------------------------------------------------
    FileDialog {
        id: consoleFwDialog
        title: "Select Console Firmware File"
        nameFilters: ["Signed firmware (*.bin *.signed.bin)", "All files (*)"]
        onAccepted: consoleFwPath.text = selectedFile.toString().replace("file:///", "")
    }

    FileDialog {
        id: txFwDialog
        title: "Select Transmitter Firmware File"
        nameFilters: ["Signed firmware (*.bin *.signed.bin)", "All files (*)"]
        onAccepted: transmitterFwPath.text = selectedFile.toString().replace("file:///", "")
    }
    
    FileDialog {
        id: testReportDialog
        title: "Select Excel Test Report"
        nameFilters: ["Excel files (*.xlsx *.xls)", "All files (*)"]
        onAccepted: {
            var target = configTargetSelector.currentText.toLowerCase()
            LIFUConnector.loadTestReport(selectedFile, target)
        }
    }

    // ----------------------------------------------------------------
    // Shared firmware update progress dialog
    // ----------------------------------------------------------------
    Popup {
        id: fwUpdateDialog
        anchors.centerIn: Overlay.overlay
        width: 500
        padding: 20
        modal: true
        closePolicy: Popup.NoAutoClose

        property string updateTitle: ""
        property real progressValue: 0.0
        property string progressLabel: ""
        property string statusMessage: ""
        property bool statusSuccess: false
        property string statusColor: "#BDC3C7"
        property bool updateDone: false

        background: Rectangle {
            color: "#1E1E20"
            radius: 12
            border.color: "#3E4E6F"
            border.width: 2
        }

        ColumnLayout {
            width: parent.width
            spacing: 16

            Text {
                text: fwUpdateDialog.updateTitle
                font.pixelSize: 16
                font.weight: Font.Bold
                color: "white"
                Layout.alignment: Qt.AlignHCenter
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            ProgressBar {
                id: fwDialogProgressBar
                Layout.fillWidth: true
                from: 0.0
                to: 1.0
                value: fwUpdateDialog.progressValue

                background: Rectangle {
                    implicitHeight: 14
                    color: "#2A2F3B"
                    radius: 7
                    border.color: "#3E4E6F"
                }
                contentItem: Item {
                    implicitHeight: 14
                    Rectangle {
                        width: fwDialogProgressBar.visualPosition * parent.width
                        height: parent.height
                        radius: 7
                        color: (fwUpdateDialog.updateDone && fwUpdateDialog.statusSuccess) ? "#2ECC71" : "#4A90E2"
                    }
                }
            }

            Text {
                text: fwUpdateDialog.progressLabel
                color: "#BDC3C7"
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                visible: fwUpdateDialog.progressLabel.length > 0
            }

            Text {
                text: fwUpdateDialog.statusMessage
                color: fwUpdateDialog.statusColor
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                visible: fwUpdateDialog.statusMessage.length > 0
            }

            Button {
                text: "Close"
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                enabled: fwUpdateDialog.updateDone
                hoverEnabled: true

                contentItem: Text {
                    text: parent.text
                    color: parent.enabled ? "#BDC3C7" : "#7F8C8D"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.pixelSize: 14
                }
                background: Rectangle {
                    color: parent.enabled ? (parent.hovered ? "#4A90E2" : "#3A3F4B") : "#2A2F3B"
                    radius: 6
                    border.color: parent.enabled ? (parent.hovered ? "#FFFFFF" : "#BDC3C7") : "#7F8C8D"
                }
                onClicked: fwUpdateDialog.close()
            }
        }
    }

    // ----------------------------------------------------------------
    // "Must update to 1.2.6 first" notice
    //
    // A custom (browsed) firmware image can only be installed on the
    // secure bootloader, which exists on units already running >= 1.2.6.
    // If the operator tries to Browse on an older unit, tell them to first
    // update to the included 1.2.6 firmware (Update Firmware with the field
    // left empty does exactly that via migration).
    // ----------------------------------------------------------------
    Popup {
        id: mustUpdate126Dialog
        anchors.centerIn: Overlay.overlay
        width: 460
        padding: 20
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            color: "#1E1E20"
            radius: 12
            border.color: "#E67E22"
            border.width: 2
        }

        ColumnLayout {
            width: parent.width
            spacing: 16

            Text {
                text: "Update to 1.2.6 required first"
                font.pixelSize: 16
                font.weight: Font.Bold
                color: "white"
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: "This console is running " + consoleCurrentVersion.text
                    + ". A custom firmware image can only be installed on the "
                    + "secure bootloader (version 1.2.6 or newer).\n\n"
                    + "Leave the Firmware File field empty and press "
                    + "“Update Firmware” to install the included "
                    + "1.2.6 firmware first, then browse for a custom image."
                color: "#BDC3C7"
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Button {
                text: "OK"
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                hoverEnabled: true
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.pixelSize: 14
                }
                background: Rectangle {
                    color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                    radius: 6
                    border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                }
                onClicked: mustUpdate126Dialog.close()
            }
        }
    }

    // ----------------------------------------------------------------
    // "Update to 2.0.8 required first" dialog (transmitter)
    //
    // A custom (browsed) transmitter image is a signed app that only installs
    // on the secure bootloader, which exists on units already running
    // >= 2.0.8. If the operator tries to Browse on an older unit, tell them to
    // first update to the included firmware ("Update Firmware (included)" with
    // the field left empty migrates the unit to the secure bootloader).
    // ----------------------------------------------------------------
    Popup {
        id: mustUpdate208Dialog
        anchors.centerIn: Overlay.overlay
        width: 460
        padding: 20
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            color: "#1E1E20"
            radius: 12
            border.color: "#E67E22"
            border.width: 2
        }

        ColumnLayout {
            width: parent.width
            spacing: 16

            Text {
                text: "Update to 2.0.8 required first"
                font.pixelSize: 16
                font.weight: Font.Bold
                color: "white"
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: "This module is running " + txCurrentVersion.text
                    + ". A custom firmware image can only be installed on the "
                    + "secure bootloader (version 2.0.8 or newer).\n\n"
                    + "Leave the Firmware File field empty and press "
                    + "“Update Firmware (included)” to install the included "
                    + "firmware first — that migrates the unit to the secure "
                    + "bootloader — then browse for a custom image."
                color: "#BDC3C7"
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Button {
                text: "OK"
                Layout.fillWidth: true
                Layout.preferredHeight: 36
                hoverEnabled: true
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.pixelSize: 14
                }
                background: Rectangle {
                    color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                    radius: 6
                    border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                }
                onClicked: mustUpdate208Dialog.close()
            }
        }
    }

    // ----------------------------------------------------------------
    // Add Device Configuration dialog
    //
    // Prompts the operator for the array-level ``name`` and ``id`` fields,
    // shows the computed ``template`` and per-module HWID list as read-only
    // values, and on accept merges the resulting blob into the lead module's
    // (TX 0) user_config under the ``device`` key.
    //
    // ``template`` is derived from (txModuleCount, freq) where ``freq`` is
    // pulled from the user_config currently in the editor. The mapping
    // matches openlifu.xdc.transducerarray._DEFAULT_TEMPLATE_IDS:
    //   (1, 155) -> openlifu_1x155
    //   (1, 400) -> openlifu_1x400
    //   (2, 155) -> openlifu_2x155
    //   (2, 400) -> openlifu_2x400
    // anything else falls through to "(unsupported)" and disables OK.
    //
    // ``modules`` are the base58-encoded HWIDs already cached on the page
    // via ``onTxDeviceInfoReceived`` (Settings.qml populates ``modules[i].deviceId``).
    // ----------------------------------------------------------------
    Popup {
        id: addDeviceConfigDialog
        anchors.centerIn: Overlay.overlay
        width: 520
        padding: 20
        modal: true
        closePolicy: Popup.CloseOnEscape

        // Last successfully parsed editor JSON; merged on accept.
        property var parsedConfig: ({})
        // Resolved values
        property string templateId: ""
        property var moduleHwids: []
        property string errorText: ""

        function _resolveTemplateId(parsed) {
            // Pull freq from the user_config. The python pipeline keys on the
            // top-level "freq" field of the user_config (see
            // openlifu.xdc.transducerarray._DEFAULT_TEMPLATE_IDS).
            var freq = parsed && parsed.freq
            if (freq === undefined || freq === null) return ""
            var nMod = settingsPage.txModuleCount
            var key = nMod + "x" + Math.round(Number(freq))
            var supported = {
                "1x155": "openlifu_1x155",
                "1x400": "openlifu_1x400",
                "2x155": "openlifu_2x155",
                "2x400": "openlifu_2x400",
            }
            return supported[key] || ""
        }

        function _collectModuleHwids() {
            var ids = []
            for (var i = 0; i < settingsPage.modules.length; ++i) {
                var m = settingsPage.modules[i]
                if (m && m.deviceId) ids.push(m.deviceId)
            }
            return ids
        }

        function openForCurrentConfig() {
            errorText = ""
            // Parse the editor text. If it fails, surface the error inside
            // the dialog so the user can fix it.
            try {
                parsedConfig = JSON.parse(userConfigEditor.text)
            } catch (e) {
                parsedConfig = {}
                errorText = "Editor does not contain valid JSON: " + e
            }
            // Prepopulate name + id from an existing device block if present.
            var existing = (parsedConfig && parsedConfig.device) || {}
            nameField.text = existing.name || ""
            idField.text = existing.id || ""
            // Compute read-only values.
            templateId = _resolveTemplateId(parsedConfig)
            moduleHwids = _collectModuleHwids()
            open()
        }

        background: Rectangle {
            color: "#1E1E20"
            radius: 12
            border.color: "#3E4E6F"
            border.width: 2
        }

        ColumnLayout {
            width: parent.width
            spacing: 14

            Text {
                text: "Add Device Configuration"
                font.pixelSize: 16
                font.weight: Font.Bold
                color: "white"
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: addDeviceConfigDialog.errorText
                color: "#E74C3C"
                font.pixelSize: 12
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                visible: addDeviceConfigDialog.errorText.length > 0
            }

            // Name (editable)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Name"
                    color: "#BDC3C7"
                    font.pixelSize: 12
                }
                TextField {
                    id: nameField
                    Layout.fillWidth: true
                    color: "white"
                    placeholderText: "e.g. OpenLIFU 2x 400kHz"
                    placeholderTextColor: "#7F8C8D"
                    background: Rectangle {
                        color: "#2A2F3B"
                        radius: 4
                        border.color: "#3E4E6F"
                    }
                }
            }

            // ID (editable)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "ID"
                    color: "#BDC3C7"
                    font.pixelSize: 12
                }
                TextField {
                    id: idField
                    Layout.fillWidth: true
                    color: "white"
                    placeholderText: "e.g. openlifu_2x400_evt1"
                    placeholderTextColor: "#7F8C8D"
                    background: Rectangle {
                        color: "#2A2F3B"
                        radius: 4
                        border.color: "#3E4E6F"
                    }
                }
            }

            // Template (read-only)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Template (auto)"
                    color: "#BDC3C7"
                    font.pixelSize: 12
                }
                TextField {
                    id: templateField
                    Layout.fillWidth: true
                    readOnly: true
                    color: addDeviceConfigDialog.templateId.length > 0 ? "#2ECC71" : "#E74C3C"
                    text: addDeviceConfigDialog.templateId.length > 0
                        ? addDeviceConfigDialog.templateId
                        : "(unsupported: need 1 or 2 modules @ 155 or 400 kHz)"
                    background: Rectangle {
                        color: "#23272F"
                        radius: 4
                        border.color: "#3E4E6F"
                    }
                }
            }

            // Modules (read-only list)
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Modules (HWIDs)"
                    color: "#BDC3C7"
                    font.pixelSize: 12
                }
                TextArea {
                    id: modulesField
                    Layout.fillWidth: true
                    Layout.preferredHeight: 60
                    readOnly: true
                    color: "white"
                    font.family: "Courier New"
                    font.pixelSize: 12
                    wrapMode: TextArea.Wrap
                    text: addDeviceConfigDialog.moduleHwids.length > 0
                        ? addDeviceConfigDialog.moduleHwids.join("\n")
                        : "(no modules)"
                    background: Rectangle {
                        color: "#23272F"
                        radius: 4
                        border.color: "#3E4E6F"
                    }
                }
            }

            // OK / Cancel
            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Button {
                    text: "Cancel"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 36
                    hoverEnabled: true
                    contentItem: Text {
                        text: parent.text
                        color: "#BDC3C7"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: 14
                    }
                    background: Rectangle {
                        color: parent.hovered ? "#C0392B" : "#3A3F4B"
                        radius: 6
                        border.color: parent.hovered ? "#FFFFFF" : "#BDC3C7"
                    }
                    onClicked: addDeviceConfigDialog.close()
                }

                Button {
                    id: addDeviceConfigOkButton
                    text: "OK"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 36
                    hoverEnabled: true
                    // OK requires: parsed config, a resolved template,
                    // non-empty id (name can be defaulted from id).
                    enabled:
                        addDeviceConfigDialog.errorText.length === 0 &&
                        addDeviceConfigDialog.templateId.length > 0 &&
                        idField.text.trim().length > 0
                    contentItem: Text {
                        text: parent.text
                        color: parent.enabled ? "white" : "#7F8C8D"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        font.pixelSize: 14
                        font.weight: Font.Bold
                    }
                    background: Rectangle {
                        color: !parent.enabled
                            ? "#2A2F3B"
                            : (parent.hovered ? "#27AE60" : "#3A3F4B")
                        radius: 6
                        border.color: !parent.enabled
                            ? "#7F8C8D"
                            : (parent.hovered ? "#FFFFFF" : "#BDC3C7")
                    }
                    onClicked: {
                        // Build the device blob. We mirror the schema produced
                        // by openlifu.xdc.TransducerArray.to_device_config():
                        //   { id, name, modules: [{hwid: ...}, ...], attrs: {} }
                        // The "transform" per module is left to the consumer
                        // (transducerarray.from_module_user_configs falls back
                        // to the template's per-module transform when no
                        // explicit transform is given here).
                        var modulesBlob = []
                        for (var i = 0; i < addDeviceConfigDialog.moduleHwids.length; ++i) {
                            modulesBlob.push({ "hwid": addDeviceConfigDialog.moduleHwids[i] })
                        }
                        var deviceBlob = {
                            "id": idField.text.trim(),
                            "name": nameField.text.trim() || idField.text.trim(),
                            "template": addDeviceConfigDialog.templateId,
                            "modules": modulesBlob,
                            "attrs": {}
                        }
                        // Merge into the editor JSON, preserving existing keys.
                        var current = {}
                        try {
                            current = JSON.parse(userConfigEditor.text)
                        } catch (e) {
                            current = addDeviceConfigDialog.parsedConfig || {}
                        }
                        current.device = deviceBlob
                        userConfigEditor.text = JSON.stringify(current, null, 2)
                        addDeviceConfigDialog.close()
                    }
                }
            }
        }
    }

    // ----------------------------------------------------------------
    // Layout
    // ----------------------------------------------------------------
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Content grid
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16

            // ============================================
            // USER CONFIG CARD (row 1 – full width)
            // ============================================
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Give the User Config row more vertical space than the
                // firmware row, which has empty space at the bottom of
                // both its cards. ``preferredHeight`` (with both rows
                // filling height) acts as the relative weight when the
                // parent ColumnLayout distributes leftover space.
                Layout.preferredHeight: 650
                Layout.minimumHeight: 300
                color: "#1E1E20"
                radius: 10
                border.color: "#3E4E6F"
                border.width: 2
                clip: true

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 16

                    // Section header + JSON editor
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.horizontalStretchFactor: 7
                        spacing: 8

                        Text {
                            text: "User Config"
                            font.pixelSize: 18
                            font.weight: Font.Bold
                            color: "white"
                            Layout.alignment: Qt.AlignHCenter
                        }

                        // Status message (hidden until a read/write completes)
                        Timer {
                            id: userConfigStatusHideTimer
                            interval: 4000
                            onTriggered: userConfigStatusText.visible = false
                        }

                        Text {
                            id: userConfigStatusText
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            visible: false
                        }

                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            ScrollView {
                                anchors.fill: parent
                                clip: true
                                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                                TextArea {
                                    id: userConfigEditor
                                    width: parent.width
                                    height: Math.max(contentHeight, parent.height)
                                    leftPadding: 8
                                    rightPadding: 8
                                    topPadding: 8
                                    bottomPadding: 8

                                    font.family: "Courier New"
                                    font.pixelSize: 13
                                    color: "white"

                                    wrapMode: TextArea.Wrap

                                    background: Rectangle {
                                        color: "#2A2F3B"
                                        radius: 4
                                        border.color: "#3E4E6F"
                                    }
                                }
                            }

                            Text {
                                anchors.centerIn: parent
                                visible: userConfigEditor.text.length === 0
                                text: "No config loaded\nPress Read Config to load from device."
                                color: "#7F8C8D"
                                font.pixelSize: 14
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                                width: parent.width - 32
                            }
                        }
                    }

                    // Action buttons
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.horizontalStretchFactor: 3
                        spacing: 12
                        Layout.alignment: Qt.AlignTop

                        Text {
                            text: "Actions"
                            font.pixelSize: 14
                            font.weight: Font.Bold
                            color: "white"
                            Layout.alignment: Qt.AlignHCenter
                            topPadding: 4
                        }

                        // Component selector
                        Text {
                            text: "Target Component"
                            color: "#BDC3C7"
                            font.pixelSize: 12
                            Layout.alignment: Qt.AlignHCenter
                        }

                        ComboBox {
                            id: configTargetSelector
                            Layout.fillWidth: true
                            model: settingsPage.configTargetModel
                            enabled: settingsPage.configTargetModel.length > 0

                            onCurrentIndexChanged: userConfigEditor.text = ""

                            contentItem: Text {
                                leftPadding: 8
                                text: configTargetSelector.enabled ? configTargetSelector.displayText : "No devices"
                                color: configTargetSelector.enabled ? "white" : "#7F8C8D"
                                verticalAlignment: Text.AlignVCenter
                                font.pixelSize: 13
                            }
                            background: Rectangle {
                                color: "#2A2F3B"
                                radius: 4
                                border.color: configTargetSelector.enabled ? "#3E4E6F" : "#2A2F3B"
                            }
                        }

                        // Device Info for selected target
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            visible: configTargetSelector.enabled && configTargetSelector.currentText.startsWith("TX")
                            
                            Text { 
                                text: "Device ID:"
                                color: "#BDC3C7"
                                font.pixelSize: 12
                            }
                            Text { 
                                Layout.fillWidth: true
                                text: {
                                    if (!configTargetSelector.enabled || !configTargetSelector.currentText.startsWith("TX")) {
                                        return "N/A"
                                    }
                                    // Extract module index from "TX 0", "TX 1", etc.
                                    var parts = configTargetSelector.currentText.split(" ")
                                    if (parts.length >= 2) {
                                        var moduleIndex = parseInt(parts[1])
                                        return modules[moduleIndex] ? modules[moduleIndex].deviceId : "N/A"
                                    }
                                    return "N/A"
                                }
                                color: "#3498DB"
                                font.pixelSize: 12
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            visible: configTargetSelector.enabled && configTargetSelector.currentText.startsWith("TX")
                            
                            Text { 
                                text: "Firmware Version:"
                                color: "#BDC3C7"
                                font.pixelSize: 12
                            }
                            Text {
                                Layout.fillWidth: true
                                text: {
                                    if (!configTargetSelector.enabled || !configTargetSelector.currentText.startsWith("TX")) {
                                        return "N/A"
                                    }
                                    // Extract module index from "TX 0", "TX 1", etc.
                                    var parts = configTargetSelector.currentText.split(" ")
                                    if (parts.length >= 2) {
                                        var moduleIndex = parseInt(parts[1])
                                        return modules[moduleIndex] ? modules[moduleIndex].firmwareVersion : "N/A"
                                    }
                                    return "N/A"
                                }
                                color: "#2ECC71"
                                font.pixelSize: 12
                            }
                        }

                        // Read Config
                        Rectangle {
                            Layout.fillWidth: true
                            height: 40
                            radius: 6
                            color: readConfigArea.containsMouse ? "#4A90E2" : "#3A3F4B"
                            border.color: readConfigArea.containsMouse ? "#FFFFFF" : "#BDC3C7"

                            Text {
                                anchors.centerIn: parent
                                text: "Read Config"
                                color: "white"
                                font.pixelSize: 13
                                font.weight: Font.Medium
                            }

                            MouseArea {
                                id: readConfigArea
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: {
                                    var target = configTargetSelector.currentText.toLowerCase()
                                    LIFUConnector.readUserConfig(target)
                                }
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }

                        // Add Device Configuration
                        //
                        // Lifts the array-level "device" block (id/name/template/modules)
                        // into the lead module's (TX 0) user_config. Only enabled when:
                        //   * a TX device is connected,
                        //   * the target component is "TX 0" (the lead module),
                        //   * the editor holds something parseable,
                        //   * no connected device is below the app's hard
                        //     minimum firmware version.
                        // The actual prompt + merge happens in addDeviceConfigDialog.
                        Rectangle {
                            id: addDeviceConfigButton
                            Layout.fillWidth: true
                            height: 40
                            radius: 6
                            // Editor non-empty?
                            property bool editorHasContent: userConfigEditor.text.trim().length > 0
                            // Target is TX 0 specifically?
                            property bool targetIsLeadModule:
                                configTargetSelector.enabled &&
                                configTargetSelector.currentText === "TX 0"
                            property bool canUse:
                                LIFUConnector.txConnected &&
                                targetIsLeadModule &&
                                editorHasContent &&
                                !LIFUConnector.firmwareUpdateRequired
                            color: !canUse
                                ? "#2A2F3B"
                                : (addDeviceConfigArea.containsMouse ? "#8E44AD" : "#3A3F4B")
                            border.color: !canUse
                                ? "#3E4E6F"
                                : (addDeviceConfigArea.containsMouse ? "#FFFFFF" : "#BDC3C7")
                            opacity: canUse ? 1.0 : 0.55

                            Text {
                                anchors.centerIn: parent
                                text: "Add Device Configuration"
                                color: "white"
                                font.pixelSize: 13
                                font.weight: Font.Medium
                            }

                            MouseArea {
                                id: addDeviceConfigArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: addDeviceConfigButton.canUse
                                onClicked: addDeviceConfigDialog.openForCurrentConfig()
                            }

                            ToolTip.visible: addDeviceConfigHoverArea.containsMouse
                                && LIFUConnector.firmwareUpdateRequired
                            ToolTip.text: "Disabled: update transmitter firmware to "
                                + LIFUConnector.minTransmitterFirmwareVersion
                                + " or newer (Firmware Update section above)."
                            ToolTip.delay: 400
                            MouseArea {
                                id: addDeviceConfigHoverArea
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.NoButton
                                visible: !addDeviceConfigButton.canUse
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }

                        // Load Test Report
                        Rectangle {
                            Layout.fillWidth: true
                            height: 40
                            radius: 6
                            color: testReportArea.containsMouse ? "#F39C12" : "#3A3F4B"
                            border.color: testReportArea.containsMouse ? "#FFFFFF" : "#BDC3C7"

                            Text {
                                anchors.centerIn: parent
                                text: "Load Test Report"
                                color: "white"
                                font.pixelSize: 13
                                font.weight: Font.Medium
                            }

                            MouseArea {
                                id: testReportArea
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: {
                                    testReportDialog.open()
                                }
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }

                        // Write Config
                        //
                        // Disabled when any connected device is below the
                        // app's hard minimum firmware version -- the operator
                        // must use the Firmware Update section above to bring
                        // it back into compliance first.
                        Rectangle {
                            id: writeConfigButton
                            Layout.fillWidth: true
                            height: 40
                            radius: 6
                            property bool canUse: !LIFUConnector.firmwareUpdateRequired
                            color: !canUse
                                ? "#2A2F3B"
                                : (writeConfigArea.containsMouse ? "#27AE60" : "#3A3F4B")
                            border.color: !canUse
                                ? "#3E4E6F"
                                : (writeConfigArea.containsMouse ? "#FFFFFF" : "#BDC3C7")
                            opacity: canUse ? 1.0 : 0.55

                            Text {
                                anchors.centerIn: parent
                                text: "Write Config"
                                color: "white"
                                font.pixelSize: 13
                                font.weight: Font.Medium
                            }

                            MouseArea {
                                id: writeConfigArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: writeConfigButton.canUse
                                onClicked: {
                                    var target = configTargetSelector.currentText.toLowerCase()
                                    LIFUConnector.writeUserConfig(target, userConfigEditor.text)
                                }
                            }

                            ToolTip.visible: writeConfigHoverArea.containsMouse
                                && LIFUConnector.firmwareUpdateRequired
                            ToolTip.text: "Disabled: update firmware to the minimum required version (Firmware Update section above)."
                            ToolTip.delay: 400
                            MouseArea {
                                id: writeConfigHoverArea
                                anchors.fill: parent
                                hoverEnabled: true
                                acceptedButtons: Qt.NoButton
                                visible: !writeConfigButton.canUse
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }

                        // Clear Config
                        Rectangle {
                            Layout.fillWidth: true
                            height: 40
                            radius: 6
                            visible: false
                            color: clearConfigArea.containsMouse ? "#C0392B" : "#3A3F4B"
                            border.color: clearConfigArea.containsMouse ? "#FFFFFF" : "#BDC3C7"

                            Text {
                                anchors.centerIn: parent
                                text: "Clear Config"
                                color: "white"
                                font.pixelSize: 13
                                font.weight: Font.Medium
                            }

                            MouseArea {
                                id: clearConfigArea
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: userConfigEditor.text = ""
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }

                        // Spacer
                        Item { Layout.fillHeight: true }
                    }
                }
            }

            // Row 2 – Console (left 50%) + Transmitter (right 50%)
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Counterpart to the User Config row's preferred size; the
                // firmware row gets a smaller share of leftover vertical
                // space.
                Layout.preferredHeight: 300
                Layout.minimumHeight: 200
                spacing: 16

                // ============================================
                // CONSOLE FIRMWARE CARD
                // ============================================
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2
                    clip: true

                    ColumnLayout {
                        id: consoleCardColumn
                        anchors {
                            top: parent.top
                            left: parent.left
                            right: parent.right
                            margins: 16
                        }
                        spacing: 12

                        // Section header
                        Text {
                            text: "Console Firmware"
                            font.pixelSize: 18
                            font.weight: Font.Bold
                            color: "white"
                            Layout.alignment: Qt.AlignHCenter
                            topPadding: 4
                        }

                        // HV connection status indicator
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Rectangle {
                                width: 16
                                height: 16
                                radius: 8
                                color: LIFUConnector.hvConnected ? "#2ECC71" : "#E74C3C"
                                border.color: "black"
                                border.width: 1
                            }

                            Text {
                                text: LIFUConnector.hvConnected ? "Console Connected" : "Console Not Connected"
                                font.pixelSize: 14
                                color: "#BDC3C7"
                                Layout.fillWidth: true
                            }
                        }

                        // Firmware version row (auto-populated)
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                text: "Firmware Version:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                Layout.preferredWidth: 140
                            }

                            Text {
                                id: consoleCurrentVersion
                                text: "—"
                                color: settingsPage.firmwareVersionColor(
                                    consoleCurrentVersion.text,
                                    LIFUConnector.minConsoleFirmwareVersion,
                                    LIFUConnector.getFirmwareFileVersion(settingsPage.consoleEffectivePath))
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }
                        }

                        // File version row (the firmware that will be
                        // installed: the browsed file, or — when the field is
                        // empty — the image included with the SDK).
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                text: "File Version:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                Layout.preferredWidth: 140
                            }

                            Text {
                                id: consoleFileVersion
                                text: {
                                    var v = LIFUConnector.getFirmwareFileVersion(settingsPage.consoleEffectivePath)
                                    return v ? v : "—"
                                }
                                color: settingsPage.fileVersionColor(
                                    consoleFileVersion.text,
                                    LIFUConnector.minConsoleFirmwareVersion,
                                    consoleCurrentVersion.text)
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }
                        }

                        // Firmware path row
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Text {
                                text: "Firmware File:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                Layout.preferredWidth: 140
                            }

                            TextField {
                                id: consoleFwPath
                                Layout.fillWidth: true
                                placeholderText: "Using included firmware — Browse to override"
                                font.pixelSize: 13
                                color: "white"
                                background: Rectangle {
                                    color: "#2A2F3B"
                                    radius: 4
                                    border.color: "#3E4E6F"
                                }
                            }

                            Button {
                                text: "Browse…"
                                hoverEnabled: true
                                Layout.preferredHeight: 40
                                Layout.preferredWidth: 100
                                enabled: !consoleUpdating

                                contentItem: Text {
                                    text: parent.text
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
                                // A custom (browsed) image only installs on
                                // the secure bootloader. If the console is
                                // older than 1.2.6, tell the operator to
                                // update to the included 1.2.6 firmware first.
                                onClicked: {
                                    var d = settingsPage._parseFwVersion(
                                        consoleCurrentVersion.text)
                                    if (LIFUConnector.hvConnected && d !== null
                                        && settingsPage._cmpFwVersion(d, [1, 2, 6]) < 0) {
                                        mustUpdate126Dialog.open()
                                    } else {
                                        consoleFwDialog.open()
                                    }
                                }
                            }
                        }

                        // Force flag — flash even when the image version is
                        // BELOW the installed one. The SDK refuses downgrades
                        // by default; this passes force=True. The bootloader's
                        // persistent anti-rollback floor is still the final
                        // authority at boot and may reject the image, leaving
                        // the slot empty — bench/recovery use only.
                        // Kept on ONE row with the update button: the card
                        // clips to its height, so an extra row pushes the
                        // button out of view.
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            CheckBox {
                                id: consoleForceDowngrade
                                checked: false
                                enabled: !consoleUpdating
                                ToolTip.visible: hovered
                                ToolTip.text: "Flash even if the image is older "
                                    + "than what's installed. The bootloader's "
                                    + "anti-rollback floor may still reject it at boot."

                                indicator: Rectangle {
                                    implicitWidth: 18
                                    implicitHeight: 18
                                    x: consoleForceDowngrade.leftPadding
                                    y: parent.height / 2 - height / 2
                                    radius: 3
                                    color: consoleForceDowngrade.checked ? "#E67E22" : "#2A2F3B"
                                    border.color: consoleForceDowngrade.enabled
                                        ? (consoleForceDowngrade.checked ? "#E67E22" : "#3E4E6F")
                                        : "#7F8C8D"
                                    Text {
                                        anchors.centerIn: parent
                                        text: "✓"
                                        color: "white"
                                        font.pixelSize: 13
                                        font.weight: Font.Bold
                                        visible: consoleForceDowngrade.checked
                                    }
                                }
                                contentItem: Text {
                                    text: "Force"
                                    color: consoleForceDowngrade.enabled ? "#BDC3C7" : "#7F8C8D"
                                    font.pixelSize: 12
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: consoleForceDowngrade.indicator.width
                                        + consoleForceDowngrade.spacing
                                }
                            }

                        // Update button — dynamic color & text driven
                        // by the device-vs-file version comparison and
                        // the MIN pin (see updateButtonColor /
                        // updateButtonText helpers above).
                        Rectangle {
                            id: consoleUpdateButton
                            Layout.fillWidth: true
                            Layout.minimumWidth: 200
                            Layout.preferredHeight: 40
                            radius: 6
                            // The firmware to install must be a signed console
                            // image. When the field is empty this is the SDK's
                            // included (bundled) signed image; when the operator
                            // browses, it's their file. The unit's state
                            // (no-bootloader / legacy / secure) is auto-detected
                            // by the SDK updater, so older units update via
                            // migration — no minimum running-version gate here.
                            property bool consoleFileSigned:
                                LIFUConnector.isConsoleFirmwareSigned(
                                    settingsPage.consoleEffectivePath)
                            enabled: LIFUConnector.hvConnected && !consoleUpdating
                                && consoleFileSigned
property color baseColor: settingsPage.updateButtonColor(
    consoleCurrentVersion.text,
    (consoleFileVersion.text === "—" ? "" : consoleFileVersion.text),
    LIFUConnector.minConsoleFirmwareVersion)
                            color: !enabled ? "#3A3F4B"
                                : (consoleUpdateArea.containsMouse ? Qt.darker(baseColor, 1.25) : baseColor)

                            Text {
                                text: consoleUpdating
                                    ? "Updating…"
                                    : settingsPage.updateButtonText(
                                        consoleCurrentVersion.text,
                                        LIFUConnector.getFirmwareFileVersion(settingsPage.consoleEffectivePath))
                                anchors.fill: parent
                                anchors.margins: 8
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                                color: consoleUpdateButton.enabled ? "white" : "#BDC3C7"
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }

                            MouseArea {
                                id: consoleUpdateArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: parent.enabled
                                onClicked: {
                                    fwUpdateDialog.updateTitle = "Updating Console Firmware…"
                                    fwUpdateDialog.progressValue = 0.0
                                    fwUpdateDialog.progressLabel = ""
                                    fwUpdateDialog.statusMessage = ""
                                    fwUpdateDialog.statusSuccess = false
                                    fwUpdateDialog.statusColor = "#BDC3C7"
                                    fwUpdateDialog.updateDone = false
                                    fwUpdateDialog.open()
                                    settingsPage.consoleUpdating = true
                                    LIFUConnector.updateConsoleFirmware(
                                        settingsPage.consoleEffectivePath,
                                        consoleForceDowngrade.checked)
                                }
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }
                        }

                        // Why the update button is disabled (signed-image
                        // requirement).
                        Text {
                            Layout.alignment: Qt.AlignRight
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignRight
                            wrapMode: Text.WordWrap
                            color: "#E67E22"
                            font.pixelSize: 11
                            visible: text.length > 0
                            text: {
                                if (!LIFUConnector.hvConnected || consoleUpdating) return ""
                                // Empty field uses the included (signed) image;
                                // only a browsed, unsigned file trips this.
                                if (consoleFwPath.text.length > 0
                                    && !consoleUpdateButton.consoleFileSigned)
                                    return "This file is not a signed console image."
                                return ""
                            }
                        }
                    }
                }

                // ============================================
                // TRANSMITTER FIRMWARE CARD
                // ============================================
                Rectangle {
                    id: txCard
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2
                    clip: true

                    // Busy overlay — shown while querying module count
                    BusyIndicator {
                        id: txBusyIndicator
                        anchors.centerIn: parent
                        running: txLoading
                        visible: txLoading
                        width: 60
                        height: 60
                        z: 10
                    }

                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: txBusyIndicator.bottom
                        anchors.topMargin: 6
                        visible: txLoading
                        text: "Querying transmitter modules…"
                        color: "#BDC3C7"
                        font.pixelSize: 13
                        z: 10
                    }

                    ColumnLayout {
                        id: txCardColumn
                        visible: !txLoading
                        anchors {
                            top: parent.top
                            left: parent.left
                            right: parent.right
                            margins: 16
                        }
                        spacing: 12

                        // Section header
                        Text {
                            text: "Transmitter Firmware"
                            font.pixelSize: 18
                            font.weight: Font.Bold
                            color: "white"
                            Layout.alignment: Qt.AlignHCenter
                            topPadding: 4
                        }

                        // TX connection status indicator + module selector.
                        // The dropdown rides along on the same row as
                        // the "X Modules Connected" text so the
                        // transmitter card's row layout matches the
                        // console card's (header → connection → fw
                        // version → file version → file path → update),
                        // keeping the two side-by-side cards aligned.
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Rectangle {
                                width: 16
                                height: 16
                                radius: 8
                                color: LIFUConnector.txConnected ? "#2ECC71" : "#E74C3C"
                                border.color: "black"
                                border.width: 1
                            }

                            Text {
                                text: LIFUConnector.txConnected
                                      ? txModuleCount + " Module" + (txModuleCount !== 1 ? "s" : "") + " Connected"
                                      : "Transmitter Not Connected"
                                font.pixelSize: 14
                                color: "#BDC3C7"
                                Layout.fillWidth: true
                            }

                            Text {
                                text: "Module:"
                                color: "#BDC3C7"
                                font.pixelSize: 12
                                visible: LIFUConnector.txConnected && txModuleCount > 0
                            }

                            ComboBox {
                                id: txModuleSelector
                                visible: LIFUConnector.txConnected && txModuleCount > 0
                                model: {
                                    let count = txModuleCount > 0 ? txModuleCount : 1
                                    let items = []
                                    for (var i = 0; i < count; i++) items.push(String(i))
                                    return items
                                }
                                Layout.preferredWidth: 70
                                Layout.preferredHeight: 28
                                font.pixelSize: 12
                                enabled: LIFUConnector.txConnected && !transmitterUpdating && txModuleCount > 0

                                onCurrentIndexChanged: {
                                    if (LIFUConnector.txConnected && txModuleCount > 0) {
                                        txCurrentVersion.text = "Reading…"
                                        LIFUConnector.readTxFirmwareVersion(currentIndex)
                                    } else {
                                        txCurrentVersion.text = "—"
                                    }
                                }
                            }
                        }

                        // Firmware version row (selected module)
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                text: "Firmware Version:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                Layout.preferredWidth: 140
                            }

                            Text {
                                id: txCurrentVersion
                                text: "—"
                                color: settingsPage.firmwareVersionColor(
                                    txCurrentVersion.text,
                                    LIFUConnector.minTransmitterFirmwareVersion,
                                    LIFUConnector.getFirmwareFileVersion(settingsPage.transmitterEffectivePath))
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }
                        }

                        // File version row (extracted from the chosen .bin)
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                text: "File Version:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                Layout.preferredWidth: 140
                            }

                            Text {
                                id: txFileVersion
                                text: {
                                    var v = LIFUConnector.getFirmwareFileVersion(settingsPage.transmitterEffectivePath)
                                    return v ? v : "—"
                                }
                                color: settingsPage.fileVersionColor(
                                    txFileVersion.text,
                                    LIFUConnector.minTransmitterFirmwareVersion,
                                    txCurrentVersion.text)
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }
                        }

                        // Firmware path row
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Text {
                                text: "Firmware File:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                Layout.preferredWidth: 140
                            }

                            TextField {
                                id: transmitterFwPath
                                Layout.fillWidth: true
                                placeholderText: "Using included firmware — Browse to override (requires v2.0.8+)"
                                font.pixelSize: 13
                                color: "white"
                                background: Rectangle {
                                    color: "#2A2F3B"
                                    radius: 4
                                    border.color: "#3E4E6F"
                                }
                            }

                            Button {
                                text: "Browse…"
                                hoverEnabled: true
                                Layout.preferredHeight: 40
                                Layout.preferredWidth: 100
                                enabled: !transmitterUpdating

                                contentItem: Text {
                                    text: parent.text
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
                                // A custom (browsed) image is a signed app that
                                // only installs on the secure bootloader. If the
                                // selected module is older than 2.0.8, tell the
                                // operator to update to the included firmware
                                // first (which migrates it) rather than browse.
                                onClicked: {
                                    var d = settingsPage._parseFwVersion(
                                        txCurrentVersion.text)
                                    if (LIFUConnector.txConnected && d !== null
                                        && settingsPage._cmpFwVersion(d, [2, 0, 8]) < 0) {
                                        mustUpdate208Dialog.open()
                                    } else {
                                        txFwDialog.open()
                                    }
                                }
                            }
                        }

                        // Force-production flag + update button on one row.
                        // When checked, the update reflashes the FULL
                        // production image (bootloader + app): the USB master
                        // (module 0) via STM32 ROM DFU (OW_CMD_DFU
                        // reserved=0x77); a SECURE slave (>= 2.0.8) via the
                        // signed DFU stub over I2C. A legacy slave doesn't
                        // need it (its normal path already reflashes the
                        // bootloader). Beta/unlocked units only — on
                        // RDP/FDA-locked units the bootloader flash is
                        // protected. (Kept on one row: the card clips to
                        // height.)
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            CheckBox {
                                id: txForceProduction
                                checked: false
                                // Master (any version), or a slave already on
                                // the secure bootloader (>= 2.0.8, signed-stub
                                // path). Legacy slaves reflash the BL on their
                                // normal path; <= 2.0.3 slaves are blocked.
                                enabled: !transmitterUpdating
                                    && (!settingsPage.txSelectedIsSlave
                                        || !settingsPage._txVerBelow(
                                            txCurrentVersion.text, [2, 0, 8]))
                                onEnabledChanged: if (!enabled) checked = false
                                ToolTip.visible: hovered
                                ToolTip.text: "Reflash the full production image "
                                    + "(bootloader + app). Master: via STM32 ROM "
                                    + "DFU. Secure slave (≥ 2.0.8): via the signed "
                                    + "DFU stub over I2C. Beta/unlocked units only."

                                indicator: Rectangle {
                                    implicitWidth: 18
                                    implicitHeight: 18
                                    x: txForceProduction.leftPadding
                                    y: parent.height / 2 - height / 2
                                    radius: 3
                                    color: txForceProduction.checked ? "#E67E22" : "#2A2F3B"
                                    border.color: txForceProduction.enabled
                                        ? (txForceProduction.checked ? "#E67E22" : "#3E4E6F")
                                        : "#7F8C8D"
                                    Text {
                                        anchors.centerIn: parent
                                        text: "✓"
                                        color: "white"
                                        font.pixelSize: 13
                                        font.weight: Font.Bold
                                        visible: txForceProduction.checked
                                    }
                                }
                                contentItem: Text {
                                    text: "Force (production)"
                                    color: txForceProduction.enabled ? "#BDC3C7" : "#7F8C8D"
                                    font.pixelSize: 12
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: txForceProduction.indicator.width
                                        + txForceProduction.spacing
                                }
                            }

                        // Update button — dynamic color & text driven
                        // by the device-vs-file version comparison and
                        // the MIN pin (see updateButtonColor /
                        // updateButtonText helpers above).
                        Rectangle {
                            id: txUpdateButton
                            Layout.fillWidth: true
                            Layout.minimumWidth: 200
                            height: 40
                            radius: 6
                            // Any module can update with an empty file field
                            // (uses the included SDK firmware): the master
                            // migrates pre-2.0.8 units, a legacy slave takes
                            // the one-shot stub migration (BL + app), a secure
                            // slave gets the bundled signed app. Blocked for
                            // the <= 2.0.3 cases (txUpdateBlocked) — the
                            // button label shows why.
                            enabled: LIFUConnector.txConnected && !transmitterUpdating && txModuleCount > 0
                                && !settingsPage.txUpdateBlocked
                            property color baseColor: txForceProduction.checked
                                ? "#E67E22"
property color baseColor: txForceProduction.checked
    ? "#E67E22"
    : settingsPage.updateButtonColor(
        txCurrentVersion.text,
        (txFileVersion.text === "—" ? "" : txFileVersion.text),
        LIFUConnector.minTransmitterFirmwareVersion)
                            color: !enabled ? "#3A3F4B"
                                : (txUpdateArea.containsMouse ? Qt.darker(baseColor, 1.25) : baseColor)

                            Text {
                                text: {
                                    if (settingsPage.txSlaveTooOld)
                                        return "Slave ≤ 2.0.3 — connect it as the USB master to update"
                                    if (settingsPage.txMasterTooOldMultiModule)
                                        return "Master ≤ 2.0.3 — disconnect slaves (single module only)"
                                    if (transmitterUpdating)
                                        return "Updating…"
                                    if (txForceProduction.checked)
                                        return "Reflash Production (BL + App)"
                                    return settingsPage.updateButtonText(
                                        txCurrentVersion.text,
                                        LIFUConnector.getFirmwareFileVersion(settingsPage.transmitterEffectivePath))
                                }
                                anchors.fill: parent
                                anchors.margins: 8
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                                color: parent.enabled ? "white" : "#BDC3C7"
                                font.pixelSize: 14
                                font.weight: Font.Bold
                            }

                            MouseArea {
                                id: txUpdateArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: parent.enabled
                                onClicked: {
                                    fwUpdateDialog.updateTitle = txForceProduction.checked
                                        ? "Reflashing Transmitter Production (Bootloader + App)…"
                                        : "Updating Transmitter Firmware (Module " + txModuleSelector.currentIndex + ")…"
                                    fwUpdateDialog.progressValue = 0.0
                                    fwUpdateDialog.progressLabel = ""
                                    fwUpdateDialog.statusMessage = ""
                                    fwUpdateDialog.statusSuccess = false
                                    fwUpdateDialog.statusColor = "#BDC3C7"
                                    fwUpdateDialog.updateDone = false
                                    fwUpdateDialog.open()
                                    settingsPage.transmitterUpdating = true
                                    LIFUConnector.updateTransmitterFirmware(
                                        transmitterFwPath.text,
                                        parseInt(txModuleSelector.currentText),
                                        txForceProduction.checked
                                    )
                                }
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }
                        }
                    }
                }
            }
        }
    }
}
