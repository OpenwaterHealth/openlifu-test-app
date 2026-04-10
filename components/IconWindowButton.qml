import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

Item {
    id: iconWindowButton
    width: 40
    height: 40

    // IconWindowButton properties
    // 0 = glyph fallback, 1 = minimize, 2 = close
    property int iconType: 0
    property string buttonIcon: "\ue900"         // Icon Unicode
    property color iconColor: "#BDC3C7"         // Default icon color
    property color hoverBackground: "#3C3C3C"   // Background color on hover
    property color hoverIconColor: "white"      // Icon color on hover
    property color backgroundColor: "transparent" // Default background color
    property color activeBackground: "#374774"      // Background color when clicked
    property color activeIconColor: "white"     // Icon color when clicked

    // Signal for click handling
    signal clicked()

    // Background
    Rectangle {
        id: background
        width: parent.width
        height: parent.height
        color: mouseArea.pressed ? activeBackground : (mouseArea.containsMouse ? hoverBackground : backgroundColor)
        radius: 6
        border.color: "transparent"
    }

    // Icon
    Text {
        id: icon
        visible: iconWindowButton.iconType === 0
        text: buttonIcon
        font.pixelSize: 24 // Icon size
        color: mouseArea.pressed ? activeIconColor : (mouseArea.containsMouse ? hoverIconColor : iconColor)
        anchors.centerIn: parent
    }

    // Minimize icon rendered as a shape to avoid locale/font substitutions.
    Rectangle {
        visible: iconWindowButton.iconType === 1
        width: 14
        height: 2
        radius: 1
        anchors.centerIn: parent
        anchors.verticalCenterOffset: 5
        color: mouseArea.pressed ? activeIconColor : (mouseArea.containsMouse ? hoverIconColor : iconColor)
    }

    // Close icon rendered as a shape to avoid locale/font substitutions.
    Item {
        visible: iconWindowButton.iconType === 2
        width: 14
        height: 14
        anchors.centerIn: parent

        Rectangle {
            width: 14
            height: 2
            radius: 1
            anchors.centerIn: parent
            rotation: 45
            color: mouseArea.pressed ? activeIconColor : (mouseArea.containsMouse ? hoverIconColor : iconColor)
        }

        Rectangle {
            width: 14
            height: 2
            radius: 1
            anchors.centerIn: parent
            rotation: -45
            color: mouseArea.pressed ? activeIconColor : (mouseArea.containsMouse ? hoverIconColor : iconColor)
        }
    }

    // Mouse Area for hover and click
    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true

        onClicked: {
            iconWindowButton.clicked() // Emit the clicked signal
        }
    }
}
