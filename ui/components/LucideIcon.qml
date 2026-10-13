import QtQuick

Item {
    id: root

    property string name: "house"
    property color iconColor: "#60A5FA"
    property int iconSize: 22

    width: iconSize
    height: iconSize

    Image {
        id: iconImage

        anchors.fill: parent

        source: "file:///C:/Users/lenovo/Downloads/PlanRenewalPro/node_modules/lucide-static/icons/"
                + root.name + ".svg"

        sourceSize.width: root.iconSize
        sourceSize.height: root.iconSize

        smooth: true
        mipmap: true

        layer.enabled: true
        layer.smooth: true
    }
}