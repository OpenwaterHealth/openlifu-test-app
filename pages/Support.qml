import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

Rectangle {
    id: supportPage
    width: parent.width
    height: parent.height
    color: "#29292B"
    radius: 20
    opacity: 0.95

    // ── Inline component: labelled value row ──────────────────────────
    component InfoRow: RowLayout {
        property string label:      ""
        property string value:      "—"
        property color  valueColor: "#BDC3C7"

        Layout.fillWidth: true
        spacing: 8

        Text {
            text: label + ":"
            color: "#BDC3C7"
            font.pixelSize: 12
            Layout.preferredWidth: 130
        }
        Text {
            text: parent.value
            color: parent.valueColor
            font.pixelSize: 12
            font.family: "Courier New"
            Layout.fillWidth: true
            wrapMode: Text.WrapAnywhere
        }
    }

    // ── Inline component: on/off badge ───────────────────────────────
    component PowerBadge: RowLayout {
        property string label: ""
        property bool   on:    false

        spacing: 6
        Rectangle {
            width: 12; height: 12; radius: 6
            color: parent.on ? "#2ECC71" : "#E74C3C"
            border.color: "black"; border.width: 1
        }
        Text {
            text: parent.label + ": " + (parent.on ? "ON" : "OFF")
            color: parent.on ? "#2ECC71" : "#E74C3C"
            font.pixelSize: 12
        }
    }

    // ── State ─────────────────────────────────────────────────────────
    property string hvFwVersion:   "—"
    property string hvDeviceId:    "—"
    property real   hvTemp1:       0.0
    property real   hvTemp2:       0.0
    property bool   hvTempsReceived: false
    property bool   hvPowerOn:     false
    property bool   hv12vOn:       false
    property var    monVoltages:   []

    property var    txModules:     []       // [{module, firmwareVersion, deviceId}]
    property int    txModuleCount: 0
    property var    txTemps:       ({})     // { moduleIdx: {txTemp, ambTemp} }

    property string diagnosticsJson: ""
    property bool   refreshing:    false

    // ── Diagnostics test state ────────────────────────────────────────
    property bool   testRunning:   false
    property var    testResults:   []   // [{group, name, status, detail}]
    property var    testSummary:   ({passed:0, failed:0, skipped:0})
    property string lastResultsJson: ""
    property int    activeTab:     0    // 0 = Hardware Monitor, 1 = Diagnostics

    // ── Reconnect debounce timers ─────────────────────────────────────
    // Delay queries slightly after connect so the SDK can fully initialise
    // and to collapse rapid connect/disconnect events into one query.
    Timer {
        id: hvQueryTimer
        interval: 2000
        repeat: false
        onTriggered: {
            if (LIFUConnector.hvConnected) {
                LIFUConnector.queryHvInfo()
                LIFUConnector.queryHvTemperature()
                LIFUConnector.getMonitorVoltages()
                supportPage.diagnosticsJson = LIFUSupportConnector.collectDiagnostics()
            }
        }
    }
    Timer {
        id: txQueryTimer
        interval: 3000
        repeat: false
        onTriggered: {
            if (LIFUConnector.txConnected) {
                LIFUConnector.queryTxInfo()
                LIFUConnector.queryTxTemperature()
                supportPage.diagnosticsJson = LIFUSupportConnector.collectDiagnostics()
            }
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────
    function stateLabel(s) {
        switch (s) {
            case 0:  return "DISCONNECTED"
            case 1:  return "CONNECTED"
            case 2:  return "READY"
            case 3:  return "RUNNING"
            case 4:  return "TEST_SCRIPT_READY"
            default: return "UNKNOWN (" + s + ")"
        }
    }

    function refreshAll() {
        refreshing = true
        if (LIFUConnector.hvConnected) {
            LIFUConnector.queryHvInfo()
            LIFUConnector.queryHvTemperature()
            LIFUConnector.getMonitorVoltages()
        }
        if (LIFUConnector.txConnected) {
            LIFUConnector.queryTxInfo()
            LIFUConnector.queryTxTemperature()
        }
        diagnosticsJson = LIFUSupportConnector.collectDiagnostics()
        refreshing = false
    }

    // ── Signal handlers ───────────────────────────────────────────────
    Connections {
        target: LIFUConnector

        function onHvDeviceInfoReceived(fwVersion, deviceId) {
            supportPage.hvFwVersion = fwVersion
            supportPage.hvDeviceId  = deviceId
        }
        function onTxDeviceInfoReceived(modulesList) {
            supportPage.txModules = modulesList.map(function(m) {
                return { module: m.module, firmwareVersion: m.firmwareVersion, deviceId: m.deviceId }
            })
        }
        function onTemperatureHvUpdated(temp1, temp2) {
            supportPage.hvTemp1 = temp1
            supportPage.hvTemp2 = temp2
            supportPage.hvTempsReceived = true
        }
        function onTemperatureTxUpdated(moduleIdx, txTemp, ambTemp) {
            var t = Object.assign({}, supportPage.txTemps)
            t[moduleIdx] = { txTemp: txTemp, ambTemp: ambTemp }
            supportPage.txTemps = t
        }
        function onMonVoltagesReceived(voltages) {
            supportPage.monVoltages = voltages
        }
        function onPowerStatusReceived(v12on, hvOn) {
            supportPage.hv12vOn   = v12on
            supportPage.hvPowerOn = hvOn
        }
        function onNumModulesUpdated() {
            supportPage.txModuleCount = LIFUConnector.queryNumModulesConnected
        }
        function onHvConnectedChanged() {
            if (LIFUConnector.hvConnected) {
                hvQueryTimer.restart()   // debounced — fires 500 ms after connect settles
            } else {
                hvQueryTimer.stop()
                supportPage.hvFwVersion = "—"
                supportPage.hvDeviceId  = "—"
                supportPage.hvTemp1     = 0.0
                supportPage.hvTemp2     = 0.0
                supportPage.hvTempsReceived = false
                supportPage.monVoltages = []
                supportPage.hvPowerOn   = false
                supportPage.hv12vOn     = false
            }
        }
        function onTxConnectedChanged() {
            if (LIFUConnector.txConnected) {
                txQueryTimer.restart()   // debounced — fires 500 ms after connect settles
            } else {
                txQueryTimer.stop()
                supportPage.txModules    = []
                supportPage.txModuleCount = 0
                supportPage.txTemps      = ({})
            }
        }
    }

    Connections {
        target: LIFUSupportConnector
        function onDiagnosticsReady(json) {
            supportPage.diagnosticsJson = json
        }
        function onTestResultReady(group, name, status, detail) {
            var arr = supportPage.testResults.slice()
            arr.push({ group: group, name: name, status: status, detail: detail })
            supportPage.testResults = arr
            // update live summary
            var s = { passed: 0, failed: 0, skipped: 0 }
            for (var i = 0; i < arr.length; i++) {
                if (arr[i].status === "PASS")       s.passed++
                else if (arr[i].status === "FAIL")  s.failed++
                else                                s.skipped++
            }
            supportPage.testSummary = s
        }
        function onTestRunFinished(json) {
            supportPage.testRunning    = false
            supportPage.lastResultsJson = json
        }
        function onPdfSaved(success, pathOrError) {
            pdfStatusText.text    = success ? ("Saved: " + pathOrError) : ("Error: " + pathOrError)
            pdfStatusText.color   = success ? "#2ECC71" : "#E74C3C"
            pdfStatusTimer.restart()
        }
    }

    Timer {
        id: pdfStatusTimer
        interval: 5000
        repeat: false
        onTriggered: pdfStatusText.text = ""
    }

    Component.onCompleted: {
        // Use the same debounce timers so page-enter and reconnect
        // share identical query logic and can't double-fire.
        if (LIFUConnector.hvConnected) hvQueryTimer.start()
        if (LIFUConnector.txConnected) txQueryTimer.start()
        if (!LIFUConnector.hvConnected && !LIFUConnector.txConnected)
            supportPage.diagnosticsJson = LIFUSupportConnector.collectDiagnostics()
    }

    // ── Layout ────────────────────────────────────────────────────────
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // ── Header ────────────────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Text {
                text: "Support Diagnostics"
                font.pixelSize: 22
                font.weight: Font.Bold
                color: "white"
            }

            // ── Tab buttons ───────────────────────────────────────
            RowLayout {
                spacing: 0
                Repeater {
                    model: ["Hardware Monitor", "Diagnostics"]
                    delegate: Rectangle {
                        width: 150; height: 32; radius: 6
                        color: supportPage.activeTab === index ? "#4A90E2" : "transparent"
                        border.color: supportPage.activeTab === index ? "#4A90E2" : "#3E4E6F"
                        border.width: 1
                        Text {
                            anchors.centerIn: parent
                            text: modelData
                            color: "white"; font.pixelSize: 13
                            font.weight: supportPage.activeTab === index ? Font.Medium : Font.Normal
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: supportPage.activeTab = index
                        }
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                }
            }

            Item { Layout.fillWidth: true }

            // Shown only on Hardware Monitor tab
            Rectangle {
                visible: supportPage.activeTab === 0
                width: 140; height: 36; radius: 6
                color: refreshArea.containsMouse ? "#4A90E2" : "#3A3F4B"
                border.color: refreshArea.containsMouse ? "#FFFFFF" : "#BDC3C7"
                Text {
                    anchors.centerIn: parent
                    text: supportPage.refreshing ? "Refreshing…" : "Refresh All"
                    color: "white"; font.pixelSize: 13; font.weight: Font.Medium
                }
                MouseArea {
                    id: refreshArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: supportPage.refreshAll()
                }
                Behavior on color { ColorAnimation { duration: 150 } }
            }
        }

        // ── Content ───────────────────────────────────────────────────
        // Tab 0: Hardware Monitor
        ColumnLayout {
            visible: supportPage.activeTab === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16

            // ── Row 1: System Info ────────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                // ── System Info Card ───────────────────────────────
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: sysInfoCol.implicitHeight + 32
                    color: "#1E1E20"; radius: 10
                    border.color: "#3E4E6F"; border.width: 2
                    clip: true

                    ColumnLayout {
                        id: sysInfoCol
                        anchors { top: parent.top; left: parent.left; right: parent.right; margins: 16 }
                        spacing: 10

                        Text {
                            text: "System Information"
                            font.pixelSize: 16; font.weight: Font.Bold; color: "white"
                            Layout.alignment: Qt.AlignHCenter
                            topPadding: 4
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: "#3E4E6F" }

                        // ── Software (left) | Devices (right) ──────────
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 24

                            // Left: Software
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Text { text: "Software"; font.pixelSize: 13; font.weight: Font.Bold; color: "#BDC3C7" }
                                InfoRow { label: "App Version"; value: appVersion;                      valueColor: "#4A90E2" }
                                InfoRow { label: "SDK Version"; value: LIFUConnector.sdkVersion;        valueColor: "#4A90E2" }
                                InfoRow { label: "App State";   value: supportPage.stateLabel(LIFUConnector.state); valueColor: "#BDC3C7" }
                            }

                            // Vertical divider
                            Rectangle { width: 1; Layout.fillHeight: true; color: "#3E4E6F" }

                            // Right: Devices
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Text { text: "Devices"; font.pixelSize: 13; font.weight: Font.Bold; color: "#BDC3C7" }
                                InfoRow {
                                    label: "Console"
                                    value: LIFUConnector.hvConnected ? "Connected" : "Not Connected"
                                    valueColor: LIFUConnector.hvConnected ? "#2ECC71" : "#E74C3C"
                                }
                                InfoRow {
                                    label: "Transmitter"
                                    value: LIFUConnector.txConnected
                                           ? supportPage.txModules.length + " module" + (supportPage.txModules.length !== 1 ? "s" : "")
                                           : "Not Connected"
                                    valueColor: LIFUConnector.txConnected ? "#2ECC71" : "#E74C3C"
                                }
                                InfoRow {
                                    label: "HV Enable Mode"
                                    value: ["AUTO", "ON", "OFF"][LIFUConnector.hvEnableMode] ?? "—"
                                    valueColor: "#BDC3C7"
                                }
                            }
                        }

                        Item { height: 8 }
                    }
                }
            }

            // ── Row 2: Console | TX ───────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 16

                // ── Console / HV Card ──────────────────────────────
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#1E1E20"; radius: 10
                    border.color: "#3E4E6F"; border.width: 2
                    clip: true

                    ScrollView {
                        anchors.fill: parent
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical.policy: ScrollBar.AsNeeded

                        ColumnLayout {
                            width: parent.width
                            anchors.margins: 16
                            spacing: 8

                            Item { height: 4 }

                            // Card header
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Layout.leftMargin: 16; Layout.rightMargin: 16
                                Rectangle {
                                    width: 14; height: 14; radius: 7
                                    color: LIFUConnector.hvConnected ? "#2ECC71" : "#E74C3C"
                                    border.color: "black"; border.width: 1
                                }
                                Text {
                                    text: "Console (HV Controller)"
                                    font.pixelSize: 16; font.weight: Font.Bold; color: "white"
                                    Layout.fillWidth: true
                                }
                            }

                            Text {
                                text: LIFUConnector.hvConnected ? "Connected" : "Not Connected"
                                color: LIFUConnector.hvConnected ? "#2ECC71" : "#E74C3C"
                                font.pixelSize: 12
                                Layout.leftMargin: 16
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#3E4E6F"; Layout.leftMargin: 16; Layout.rightMargin: 16 }

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: 16; Layout.rightMargin: 16
                                spacing: 6

                                InfoRow { label: "Firmware Version"; value: supportPage.hvFwVersion; valueColor: "#4A90E2" }
                                InfoRow { label: "Device ID";        value: supportPage.hvDeviceId;  valueColor: "#BDC3C7" }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#3E4E6F"; Layout.leftMargin: 16; Layout.rightMargin: 16 }

                            Text {
                                text: "Power Status"; font.pixelSize: 13; font.weight: Font.Bold; color: "#BDC3C7"
                                Layout.leftMargin: 16
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 24
                                Layout.leftMargin: 16
                                PowerBadge { label: "12V"; on: supportPage.hv12vOn }
                                PowerBadge { label: "HV";  on: supportPage.hvPowerOn }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#3E4E6F"; Layout.leftMargin: 16; Layout.rightMargin: 16 }

                            Text {
                                text: "Temperatures"; font.pixelSize: 13; font.weight: Font.Bold; color: "#BDC3C7"
                                Layout.leftMargin: 16
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: 16; Layout.rightMargin: 16
                                spacing: 6
                                InfoRow {
                                    label: "Temp 1"
                                    value: supportPage.hvTempsReceived ? Number(supportPage.hvTemp1).toFixed(1) + " °C" : "—"
                                    valueColor: "#F39C12"
                                }
                                InfoRow {
                                    label: "Temp 2"
                                    value: supportPage.hvTempsReceived ? Number(supportPage.hvTemp2).toFixed(1) + " °C" : "—"
                                    valueColor: "#F39C12"
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#3E4E6F"; Layout.leftMargin: 16; Layout.rightMargin: 16 }

                            Text {
                                text: "Monitor Voltages"; font.pixelSize: 13; font.weight: Font.Bold; color: "#BDC3C7"
                                Layout.leftMargin: 16
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.leftMargin: 16; Layout.rightMargin: 16
                                spacing: 6

                                Repeater {
                                    model: supportPage.monVoltages.length
                                    delegate: InfoRow {
                                        readonly property var _names: ["HVP1", "HVP2", "HVM2", "HVM1", "12V", "VCA1", "VCB1", "VCC1"]
                                        label: index < _names.length ? _names[index] : ("V" + (index + 1))
                                        value: (supportPage.monVoltages[index] !== undefined &&
                                                supportPage.monVoltages[index].converted_voltage !== undefined)
                                               ? Number(supportPage.monVoltages[index].converted_voltage).toFixed(2) + " V" : "—"
                                        valueColor: "#2ECC71"
                                    }
                                }
                                Text {
                                    visible: supportPage.monVoltages.length === 0
                                    text: "No readings"
                                    color: "#7F8C8D"; font.pixelSize: 12
                                }
                            }

                            Item { height: 12 }
                        }
                    }
                }

                // ── TX Device Card ─────────────────────────────────
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#1E1E20"; radius: 10
                    border.color: "#3E4E6F"; border.width: 2
                    clip: true

                    ScrollView {
                        anchors.fill: parent
                        clip: true
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical.policy: ScrollBar.AsNeeded

                        ColumnLayout {
                            width: parent.width
                            spacing: 8

                            Item { height: 4 }

                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Layout.leftMargin: 16; Layout.rightMargin: 16
                                Rectangle {
                                    width: 14; height: 14; radius: 7
                                    color: LIFUConnector.txConnected ? "#2ECC71" : "#E74C3C"
                                    border.color: "black"; border.width: 1
                                }
                                Text {
                                    text: "Transmitter (TX)"
                                    font.pixelSize: 16; font.weight: Font.Bold; color: "white"
                                    Layout.fillWidth: true
                                }
                            }

                            Text {
                                text: LIFUConnector.txConnected
                                      ? supportPage.txModules.length + " module"
                                        + (supportPage.txModules.length !== 1 ? "s" : "") + " connected"
                                      : "Not Connected"
                                color: LIFUConnector.txConnected ? "#2ECC71" : "#E74C3C"
                                font.pixelSize: 12
                                Layout.leftMargin: 16
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#3E4E6F"; Layout.leftMargin: 16; Layout.rightMargin: 16 }

                            Repeater {
                                model: supportPage.txModules
                                delegate: ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.leftMargin: 16; Layout.rightMargin: 16
                                    spacing: 6

                                    Text {
                                        text: "Module " + modelData.module
                                        font.pixelSize: 13; font.weight: Font.Bold; color: "#BDC3C7"
                                    }
                                    InfoRow { label: "Firmware Version"; value: modelData.firmwareVersion; valueColor: "#4A90E2" }
                                    InfoRow { label: "Device ID";        value: modelData.deviceId;       valueColor: "#BDC3C7" }
                                    InfoRow {
                                        label: "TX Temp"
                                        value: supportPage.txTemps[modelData.module] !== undefined
                                               ? Number(supportPage.txTemps[modelData.module].txTemp).toFixed(1) + " °C" : "—"
                                        valueColor: "#F39C12"
                                    }
                                    InfoRow {
                                        label: "Ambient Temp"
                                        value: supportPage.txTemps[modelData.module] !== undefined
                                               ? Number(supportPage.txTemps[modelData.module].ambTemp).toFixed(1) + " °C" : "—"
                                        valueColor: "#F39C12"
                                    }
                                    Rectangle {
                                        visible: index < supportPage.txModules.length - 1
                                        Layout.fillWidth: true; height: 1; color: "#3E4E6F"
                                    }
                                }
                            }

                            Text {
                                visible: supportPage.txModules.length === 0
                                text: "No modules detected"
                                color: "#7F8C8D"; font.pixelSize: 12
                                Layout.leftMargin: 16
                            }

                            Item { height: 12 }
                        }
                    }
                }
            }
        }

        // ── Tab 1: Diagnostics ────────────────────────────────────────
        ColumnLayout {
            visible: supportPage.activeTab === 1
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            // ── Run controls ──────────────────────────────────────
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                // Summary badges
                Text {
                    text: supportPage.testRunning ? "Running…" : (
                          supportPage.testResults.length > 0
                          ? supportPage.testSummary.passed + " passed  "
                            + supportPage.testSummary.failed + " failed  "
                            + supportPage.testSummary.skipped + " skipped"
                          : "No tests run yet")
                    color: supportPage.testRunning ? "#F39C12"
                           : (supportPage.testSummary.failed > 0 ? "#E74C3C" : "#BDC3C7")
                    font.pixelSize: 13
                }

                Item { Layout.fillWidth: true }

                // Individual group buttons
                Repeater {
                    model: [
                        { label: "Console",   group: "console"  },
                        { label: "TX",        group: "tx"       },
                    ]
                    delegate: Rectangle {
                        width: 100; height: 32; radius: 6
                        color: groupBtnArea.containsMouse ? "#3A5A8A" : "#2C3E50"
                        border.color: "#3E4E6F"
                        Text {
                            anchors.centerIn: parent
                            text: modelData.label
                            color: "white"; font.pixelSize: 12
                        }
                        MouseArea {
                            id: groupBtnArea
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: !supportPage.testRunning
                            onClicked: {
                                supportPage.testResults = []
                                supportPage.testSummary = {passed:0, failed:0, skipped:0}
                                supportPage.testRunning = true
                                LIFUSupportConnector.runTestGroup(modelData.group)
                            }
                        }
                        Behavior on color { ColorAnimation { duration: 120 } }
                    }
                }

                // Run All button
                Rectangle {
                    width: 110; height: 32; radius: 6
                    color: runAllArea.containsMouse ? "#4A90E2" : "#1A5276"
                    border.color: runAllArea.containsMouse ? "#FFFFFF" : "#4A90E2"
                    Text {
                        anchors.centerIn: parent
                        text: supportPage.testRunning ? "Running…" : "Run All Tests"
                        color: "white"; font.pixelSize: 12; font.weight: Font.Medium
                    }
                    MouseArea {
                        id: runAllArea
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: !supportPage.testRunning
                        onClicked: {
                            supportPage.testResults = []
                            supportPage.testSummary = {passed:0, failed:0, skipped:0}
                            supportPage.testRunning = true
                            LIFUSupportConnector.runAllTests()
                        }
                    }
                    Behavior on color { ColorAnimation { duration: 120 } }
                }

                // Save PDF button (only after results available)
                Rectangle {
                    visible: supportPage.lastResultsJson !== ""
                    width: 110; height: 32; radius: 6
                    color: pdfBtnArea.containsMouse ? "#27AE60" : "#1E8449"
                    border.color: "#2ECC71"
                    Text {
                        anchors.centerIn: parent
                        text: "Save PDF"
                        color: "white"; font.pixelSize: 12; font.weight: Font.Medium
                    }
                    MouseArea {
                        id: pdfBtnArea
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: {
                            var dest = pdfPathInput.text.trim()
                            if (dest === "") {
                                var ts = Qt.formatDateTime(new Date(), "yyyy-MM-dd_HH-mm-ss")
                                dest = LIFUSupportConnector.documentsLocation
                                       + "/openlifu-diag-" + ts + ".pdf"
                            }
                            LIFUSupportConnector.savePdfReport(supportPage.lastResultsJson, dest)
                        }
                    }
                    Behavior on color { ColorAnimation { duration: 120 } }
                }
            }

            // PDF path input + status
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text { text: "PDF Path:"; color: "#BDC3C7"; font.pixelSize: 12 }
                Rectangle {
                    Layout.fillWidth: true; height: 28; radius: 4
                    color: "#1A1A1C"; border.color: "#3E4E6F"
                    TextInput {
                        id: pdfPathInput
                        anchors { fill: parent; leftMargin: 8; rightMargin: 8; verticalCenter: parent.verticalCenter }
                        verticalAlignment: TextInput.AlignVCenter
                        color: "white"; font.pixelSize: 12; font.family: "Courier New"
                        Text {
                            visible: pdfPathInput.text === "" && !pdfPathInput.activeFocus
                            text: "Leave blank for auto-generated filename"
                            color: "#555"; font.pixelSize: 12
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }
                Text {
                    id: pdfStatusText
                    text: ""; color: "#2ECC71"; font.pixelSize: 11
                }
            }

            // ── Results list ──────────────────────────────────────
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: "#1E1E20"; radius: 10
                border.color: "#3E4E6F"; border.width: 2
                clip: true

                // Column headers
                Rectangle {
                    id: diagHeader
                    anchors { top: parent.top; left: parent.left; right: parent.right }
                    height: 32; color: "#2C3E50"; radius: 10
                    // square off bottom corners
                    Rectangle { anchors { bottom: parent.bottom; left: parent.left; right: parent.right } height: 10; color: "#2C3E50" }
                    RowLayout {
                        anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                        spacing: 0
                        Text { text: "Group";  color: "#BDC3C7"; font.pixelSize: 12; font.weight: Font.Bold; Layout.preferredWidth: 100 }
                        Text { text: "Test";   color: "#BDC3C7"; font.pixelSize: 12; font.weight: Font.Bold; Layout.fillWidth: true }
                        Text { text: "Status"; color: "#BDC3C7"; font.pixelSize: 12; font.weight: Font.Bold; Layout.preferredWidth: 70; horizontalAlignment: Text.AlignHCenter }
                        Text { text: "Detail"; color: "#BDC3C7"; font.pixelSize: 12; font.weight: Font.Bold; Layout.preferredWidth: 240 }
                    }
                }

                ListView {
                    anchors { top: diagHeader.bottom; left: parent.left; right: parent.right; bottom: parent.bottom; margins: 4 }
                    clip: true
                    model: supportPage.testResults
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    Text {
                        visible: supportPage.testResults.length === 0
                        anchors.centerIn: parent
                        text: supportPage.testRunning ? "Tests running…" : "Run a test to see results"
                        color: "#555"; font.pixelSize: 13
                    }

                    delegate: Rectangle {
                        width: ListView.view.width
                        height: 28
                        color: index % 2 === 0 ? "#232325" : "#1E1E20"
                        readonly property var _statusColor: ({
                            "PASS": "#2ECC71", "FAIL": "#E74C3C", "SKIP": "#F39C12"
                        })
                        RowLayout {
                            anchors { fill: parent; leftMargin: 12; rightMargin: 12 }
                            spacing: 0
                            Text {
                                text: modelData.group
                                color: "#7F8C8D"; font.pixelSize: 11; font.family: "Courier New"
                                Layout.preferredWidth: 100; elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.name
                                color: "#ECEFF1"; font.pixelSize: 12
                                Layout.fillWidth: true; elide: Text.ElideRight
                            }
                            Text {
                                text: modelData.status
                                color: _statusColor[modelData.status] || "#BDC3C7"
                                font.pixelSize: 12; font.weight: Font.Bold
                                Layout.preferredWidth: 70; horizontalAlignment: Text.AlignHCenter
                            }
                            Text {
                                text: modelData.detail
                                color: "#BDC3C7"; font.pixelSize: 11; font.family: "Courier New"
                                Layout.preferredWidth: 240; elide: Text.ElideRight
                                ToolTip.visible: detailMa.containsMouse && modelData.detail.length > 35
                                ToolTip.text: modelData.detail
                                ToolTip.delay: 500
                                MouseArea { id: detailMa; anchors.fill: parent; hoverEnabled: true }
                            }
                        }
                    }
                }
            }
        }
    }
}
