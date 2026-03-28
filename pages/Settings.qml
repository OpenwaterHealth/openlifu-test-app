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

    function queryTxModules() {
        txLoading = true
        txQueryTimer.start()
    }

    // Populate default firmware paths; query versions if already connected
    Component.onCompleted: {
        consoleFwPath.text = LIFUConnector.getDefaultFirmwarePath("console")
        transmitterFwPath.text = LIFUConnector.getDefaultFirmwarePath("transmitter")
        if (LIFUConnector.hvConnected) {
            consoleCurrentVersion.text = "Reading…"
            LIFUConnector.readHvFirmwareVersion()
        }
        if (LIFUConnector.txConnected) {
            queryTxModules()
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
            }
        }

        function onNumModulesUpdated() {
            settingsPage.txModuleCount = LIFUConnector.queryNumModulesConnected
            settingsPage.txLoading = false
            // Auto-read version for whichever module is currently selected
            if (settingsPage.txModuleCount > 0) {
                txCurrentVersion.text = "Reading…"
                LIFUConnector.readTxFirmwareVersion(txModuleSelector.currentIndex)
            }
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
    // Layout
    // ----------------------------------------------------------------
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 15

        // Title
        Text {
            text: "Settings"
            font.pixelSize: 24
            font.weight: Font.Bold
            color: "white"
            horizontalAlignment: Text.AlignHCenter
            Layout.alignment: Qt.AlignHCenter
        }

        // Scrollable content
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.vertical.policy: ScrollBar.AsNeeded
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: settingsPage.width - 40
                spacing: 16

                // ============================================
                // CONSOLE FIRMWARE CARD
                // ============================================
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: consoleCardColumn.implicitHeight + 30
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2

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
                                color: "#4A90E2"
                                font.pixelSize: 14
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
                                placeholderText: "Path to firmware (.bin)"
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
                                onClicked: consoleFwDialog.open()
                            }
                        }

                        // Update button
                        Rectangle {
                            id: consoleUpdateButton
                            Layout.preferredWidth: 200
                            Layout.alignment: Qt.AlignRight
                            height: 40
                            radius: 6
                            color: enabled ? (consoleUpdateArea.containsMouse ? "#C0392B" : "#E74C3C") : "#7F8C8D"
                            enabled: LIFUConnector.hvConnected && !consoleUpdating && consoleFwPath.text.length > 0

                            Text {
                                text: consoleUpdating ? "Updating…" : "Update Firmware"
                                anchors.centerIn: parent
                                color: parent.enabled ? "white" : "#BDC3C7"
                                font.pixelSize: 15
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
                                    LIFUConnector.updateConsoleFirmware(consoleFwPath.text)
                                }
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }
                    }
                }

                // ============================================
                // TRANSMITTER FIRMWARE CARD
                // ============================================
                Rectangle {
                    id: txCard
                    Layout.fillWidth: true
                    implicitHeight: txLoading ? 120 : (txCardColumn.implicitHeight + 30)
                    color: "#1E1E20"
                    radius: 10
                    border.color: "#3E4E6F"
                    border.width: 2

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

                        // TX connection status indicator
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
                        }

                        // Module selector + current version row
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Text {
                                text: "Module:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                Layout.preferredWidth: 80
                            }

                            ComboBox {
                                id: txModuleSelector
                                model: {
                                    let count = txModuleCount > 0 ? txModuleCount : 1
                                    let items = []
                                    for (var i = 0; i < count; i++) items.push(String(i))
                                    return items
                                }
                                Layout.preferredWidth: 70
                                Layout.preferredHeight: 32
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

                            Text {
                                text: "Firmware Version:"
                                color: "#BDC3C7"
                                font.pixelSize: 14
                                leftPadding: 10
                            }

                            Text {
                                id: txCurrentVersion
                                text: "—"
                                color: "#4A90E2"
                                font.pixelSize: 14
                                Layout.fillWidth: true
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
                                placeholderText: "Path to firmware (.bin)"
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
                                onClicked: txFwDialog.open()
                            }
                        }

                        // Update button
                        Rectangle {
                            id: txUpdateButton
                            Layout.preferredWidth: 200
                            Layout.alignment: Qt.AlignRight
                            height: 40
                            radius: 6
                            color: enabled ? (txUpdateArea.containsMouse ? "#C0392B" : "#E74C3C") : "#7F8C8D"
                            enabled: LIFUConnector.txConnected && !transmitterUpdating && txModuleCount > 0 && transmitterFwPath.text.length > 0

                            Text {
                                text: transmitterUpdating ? "Updating…" : "Update Firmware"
                                anchors.centerIn: parent
                                color: parent.enabled ? "white" : "#BDC3C7"
                                font.pixelSize: 15
                                font.weight: Font.Bold
                            }

                            MouseArea {
                                id: txUpdateArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: parent.enabled
                                onClicked: {
                                    fwUpdateDialog.updateTitle = "Updating Transmitter Firmware (Module " + txModuleSelector.currentIndex + ")…"
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
                                        parseInt(txModuleSelector.currentText)
                                    )
                                }
                            }

                            Behavior on color { ColorAnimation { duration: 150 } }
                        }
                    }
                }

                // Bottom spacer
                Item { implicitHeight: 10 }
            }
        }
    }
}
