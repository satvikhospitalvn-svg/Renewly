import QtQuick
import QtQuick.Window
import QtQuick.Effects
import "components"

Window {
    id: root

    width: 1440
    height: 900

    minimumWidth: 1100
    minimumHeight: 700

    visible: true
    title: "Renewly"

    // Frameless glass window. The custom controls below replace the
    // standard Windows title bar so the glass surface reads as one piece.
    flags: Qt.Window | Qt.FramelessWindowHint

    // Keep the native window opaque. A fully transparent native window lets
    // other desktop applications show through the entire Renewly window.
    color: "#FF071018"


    // ============================================================
    // FULL BACKGROUND IMAGE
    // ============================================================

    Image {
        id: backgroundImage

        anchors.fill: parent

        source: "assets/background.jpg"

        fillMode: Image.PreserveAspectCrop

        asynchronous: true
        cache: true

        smooth: true
        mipmap: true

        opacity: 0.78

        z: -10
    }


    // ============================================================
    // LIQUID-GLASS BACKDROP DIFFUSION
    // ============================================================

    Item {
        id: blurredBackdrop

        anchors.fill: parent

        opacity: 0.36

        z: -9.5

        clip: true

        Image {
            id: blurredBackdropSource

            anchors.fill: parent

            source: "assets/background.jpg"

            fillMode: Image.PreserveAspectCrop

            asynchronous: true
            cache: true

            smooth: true
            mipmap: true

            visible: false
        }

        MultiEffect {
            anchors.fill: parent

            source: blurredBackdropSource

            blurEnabled: true
            blur: 0.48
            blurMax: 52

            saturation: 0.10
            brightness: 0.03

            visible: blurredBackdropSource.status === Image.Ready
        }
    }


    // ============================================================
    // VERY SUBTLE BACKGROUND OVERLAY
    // ============================================================

    Rectangle {
        anchors.fill: parent

        color: "#071018"
        opacity: 0.055

        z: -9
    }


    // ============================================================
    // AMBIENT GLASS GLOWS
    // ============================================================

    Rectangle {
        width: 620
        height: 620

        x: -180
        y: -190

        radius: width / 2

        color: "#17608A"
        opacity: 0.10

        z: -8
    }

    Rectangle {
        width: 520
        height: 520

        x: root.width - 250
        y: root.height - 260

        radius: width / 2

        color: "#522D75"
        opacity: 0.09

        z: -8
    }

    Rectangle {
        width: 300
        height: 300

        x: root.width * 0.45
        y: -150

        radius: width / 2

        color: "#167A83"
        opacity: 0.065

        z: -8
    }


    // ============================================================
    // MAIN GLASS WINDOW
    // ============================================================

    Rectangle {
        id: glassWindow

        anchors.fill: parent
        anchors.margins: 18

        radius: 32

        color: "#1A15232F"

        border.width: 1
        border.color: "#42FFFFFF"


        Rectangle {
            anchors.fill: parent
            anchors.margins: 2
            radius: 30
            z: 0

            gradient: Gradient {
                GradientStop { position: 0.00; color: "#0AFFFFFF" }
                GradientStop { position: 0.36; color: "#0218D9E8" }
                GradientStop { position: 0.72; color: "#020BFFFF" }
                GradientStop { position: 1.00; color: "#08FFFFFF" }
            }
        }


        // ========================================================
        // FINE INNER OPTICAL EDGE
        // ========================================================

        Rectangle {
            anchors.fill: parent
            anchors.margins: 3
            radius: 29

            color: "#00FFFFFF"

            border.width: 1
            border.color: "#18DDF6FF"
        }


        // ========================================================
        // SOFT INNER GLASS LAYER
        // ========================================================

        Rectangle {
            anchors.fill: parent
            anchors.margins: 1

            radius: 31

            color: "#08FFFFFF"

            border.width: 1
            border.color: "#2FFFFFFF"
        }


        Rectangle {
            anchors.fill: parent
            anchors.margins: 2

            radius: 30

            color: "#03DDF6FF"

            opacity: 0.35

            border.width: 1
            border.color: "#08FFFFFF"
        }


        // ========================================================
        // SUBTLE COLORED LIQUID REFLECTION
        // ========================================================

        Rectangle {
            anchors.fill: parent
            anchors.margins: 2
            radius: 30
            z: 0

            gradient: Gradient {
                GradientStop { position: 0.00; color: "#0617D9E8" }
                GradientStop { position: 0.30; color: "#02000000" }
                GradientStop { position: 0.68; color: "#0311268F" }
                GradientStop { position: 1.00; color: "#0518D9E8" }
            }
        }


        // ========================================================
        // TOP GLASS REFLECTION
        // ========================================================

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top

            anchors.margins: 1

            height: 115

            radius: 30

            gradient: Gradient {
                GradientStop { position: 0.00; color: "#18FFFFFF" }
                GradientStop { position: 0.42; color: "#0BFFFFFF" }
                GradientStop { position: 1.00; color: "#00FFFFFF" }
            }
        }


        // Broad liquid-glass sheen
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top

            anchors.margins: 2

            height: 2

            radius: 1

            color: "#7AFFFFFF"

            opacity: 0.55
        }


        // Soft lower glass reflection
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom

            anchors.margins: 1

            height: 70

            radius: 30

            color: "#05FFFFFF"
        }


        // ========================================================
        // SIDEBAR
        // ========================================================

        Rectangle {
            id: sidebar

            x: 18
            y: 18

            width: 238
            height: parent.height - 36

            radius: 26

            color: "#1A15222E"

            border.width: 1
            border.color: "#42FFFFFF"


            Rectangle {
                anchors.fill: parent
                anchors.margins: 1

                radius: 25

                z: 0

                gradient: Gradient {
                    GradientStop { position: 0.00; color: "#20FFFFFF" }
                    GradientStop { position: 0.28; color: "#0EFFFFFF" }
                    GradientStop { position: 0.68; color: "#0616D9E8" }
                    GradientStop { position: 1.00; color: "#08FFFFFF" }
                }
            }


            // Sidebar top reflection
            Rectangle {
                x: 1
                y: 1

                width: parent.width - 2
                height: 105

                radius: 25

                color: "#08FFFFFF"
            }


            // ====================================================
            // LOGO
            // ====================================================

            Row {
                x: 22
                y: 25

                spacing: 12

                Rectangle {
                    width: 42
                    height: 42

                    radius: 13

                    color: "#321C5575"

                    border.width: 1
                    border.color: "#60FFFFFF"

                    Text {
                        anchors.centerIn: parent

                        text: "R"

                        color: "#BCEBFF"

                        font.pixelSize: 23
                        font.bold: true
                    }
                }


                Column {
                    anchors.verticalCenter: parent.verticalCenter

                    spacing: 1

                    Text {
                        text: "RENEWLY"

                        color: "#FFFFFF"

                        font.pixelSize: 17
                        font.bold: true
                        font.letterSpacing: 1.2
                    }

                    Text {
                        text: "PLAN MANAGER"

                        color: "#B5CAD8"

                        font.pixelSize: 8
                        font.bold: true
                        font.letterSpacing: 1.5
                    }
                }
            }


            // ====================================================
            // NAVIGATION
            // ====================================================

            Column {
                id: navigation

                x: 12
                y: 112

                width: parent.width - 24

                spacing: 5


                // =================================================
                // HOME
                // =================================================

                Rectangle {
                    id: homeButton

                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#24366F8A"

                    border.width: 1
                    border.color: "#42FFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: homeButton.hovered = true
                        onExited: homeButton.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: homeButton.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "house"

                        iconSize: 21
                        iconColor: "#7DD3FC"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Home"

                        color: "#FFFFFF"

                        font.pixelSize: 13
                        font.bold: true
                    }
                }


                // =================================================
                // ADD PLAN
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "circle-plus"

                        iconSize: 21
                        iconColor: "#5EEAD4"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Add Plan"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }


                // =================================================
                // ANALYTICS
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "chart-no-axes-combined"

                        iconSize: 21
                        iconColor: "#D8B4FE"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Analytics"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }


                // =================================================
                // SMART ASSISTANT
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "bot"

                        iconSize: 21
                        iconColor: "#BEF264"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Smart Assistant"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }


                // =================================================
                // PURCHASES
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "shopping-cart"

                        iconSize: 21
                        iconColor: "#FCD34D"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Purchases"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }


                // =================================================
                // CALENDAR
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "calendar-days"

                        iconSize: 21
                        iconColor: "#67E8F9"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Calendar"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }


                // =================================================
                // RENEWAL TIMELINE
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "clock-3"

                        iconSize: 21
                        iconColor: "#FDA4AF"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Renewal Timeline"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }


                // =================================================
                // RECYCLE BIN
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "trash-2"

                        iconSize: 21
                        iconColor: "#FF8A8A"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Recycle Bin"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }


                // =================================================
                // SETTINGS
                // =================================================

                Rectangle {
                    width: parent.width
                    height: 45

                    radius: 13

                    color: "#0315222E"

                    border.width: 1
                    border.color: "#2FFFFFFF"

                    property bool hovered: false

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        onEntered: parent.hovered = true
                        onExited: parent.hovered = false

                        cursorShape: Qt.PointingHandCursor
                    }

                    Rectangle {
                        visible: parent.hovered

                        anchors.fill: parent

                        radius: parent.radius

                        color: "#08FFFFFF"
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "settings"

                        iconSize: 21
                        iconColor: "#E2E8F0"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 49

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Settings"

                        color: "#D5E2EA"

                        font.pixelSize: 13
                    }
                }
            }


            // ====================================================
            // ACCOUNT SECTION
            // ====================================================

            Rectangle {
                x: 14
                y: parent.height - 145

                width: parent.width - 28
                height: 1

                color: "#22FFFFFF"
            }


            // Account
            Rectangle {
                x: 14
                y: parent.height - 125

                width: parent.width - 28
                height: 43

                radius: 12

                color: "#0315222E"

                property bool hovered: false

                MouseArea {
                    anchors.fill: parent

                    hoverEnabled: true

                    onEntered: parent.hovered = true
                    onExited: parent.hovered = false

                    cursorShape: Qt.PointingHandCursor
                }

                Rectangle {
                    visible: parent.hovered

                    anchors.fill: parent

                    radius: parent.radius

                    color: "#08FFFFFF"
                }

                LucideIcon {
                    x: 14

                    anchors.verticalCenter: parent.verticalCenter

                    name: "user-round"

                    iconSize: 20
                    iconColor: "#93C5FD"
                }

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 48

                    anchors.verticalCenter: parent.verticalCenter

                    text: "Account"

                    color: "#D5E2EA"

                    font.pixelSize: 13
                }
            }


            // Logout
            Rectangle {
                x: 14
                y: parent.height - 78

                width: parent.width - 28
                height: 43

                radius: 12

                color: "#0315222E"

                property bool hovered: false

                MouseArea {
                    anchors.fill: parent

                    hoverEnabled: true

                    onEntered: parent.hovered = true
                    onExited: parent.hovered = false

                    cursorShape: Qt.PointingHandCursor
                }

                Rectangle {
                    visible: parent.hovered

                    anchors.fill: parent

                    radius: parent.radius

                    color: "#08FFFFFF"
                }

                LucideIcon {
                    x: 14

                    anchors.verticalCenter: parent.verticalCenter

                    name: "log-out"

                    iconSize: 20
                    iconColor: "#FDBA74"
                }

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 48

                    anchors.verticalCenter: parent.verticalCenter

                    text: "Logout Account"

                    color: "#D5E2EA"

                    font.pixelSize: 13
                }
            }
        }


        // ========================================================
        // CUSTOM WINDOW TITLE / DRAG REGION
        // ========================================================

        MouseArea {
            id: windowDragArea

            x: 282
            y: 0

            width: root.width - 430
            height: 24

            z: 20

            acceptedButtons: Qt.LeftButton
            hoverEnabled: true

            onPressed: function(mouse) {
                if (mouse.button === Qt.LeftButton) {
                    root.startSystemMove()
                }
            }

            onDoubleClicked: {
                if (root.visibility === Window.Maximized)
                    root.showNormal()
                else
                    root.showMaximized()
            }
        }


        // Small centered brand title in custom chrome
        Text {
            x: 292
            y: 6

            z: 21

            text: "Renewly"

            color: "#DCEAF2"

            font.pixelSize: 11
            font.bold: true

            opacity: 0.72

            visible: root.width < 1250
        }


        // ========================================================
        // CUSTOM WINDOW CONTROLS
        // ========================================================
        // IMPORTANT:
        // These controls are parented directly to root.
        // Therefore they stay attached to the actual native window
        // when the window is maximized.
        // ========================================================

        Row {
            id: windowControls

            parent: root

            anchors.right: parent.right
            anchors.rightMargin: 18

            y: 7

            z: 100

            spacing: 4


            // ====================================================
            // MINIMIZE
            // ====================================================

            Rectangle {
                width: 42
                height: 32

                radius: 10

                color: minimizeArea.containsMouse
                       ? "#18FFFFFF"
                       : "#08FFFFFF"

                border.width: 1

                border.color: minimizeArea.containsMouse
                              ? "#45FFFFFF"
                              : "#18FFFFFF"

                Text {
                    anchors.centerIn: parent

                    text: "−"

                    color: "#E7F1F6"

                    font.pixelSize: 17
                    font.bold: true
                }

                MouseArea {
                    id: minimizeArea

                    anchors.fill: parent

                    hoverEnabled: true

                    cursorShape: Qt.PointingHandCursor

                    onClicked: root.showMinimized()
                }
            }


            // ====================================================
            // MAXIMIZE / RESTORE
            // ====================================================

            Rectangle {
                width: 42
                height: 32

                radius: 10

                color: maximizeArea.containsMouse
                       ? "#18FFFFFF"
                       : "#08FFFFFF"

                border.width: 1

                border.color: maximizeArea.containsMouse
                              ? "#45FFFFFF"
                              : "#18FFFFFF"

                Text {
                    anchors.centerIn: parent

                    text: root.visibility === Window.Maximized
                          ? "❐"
                          : "□"

                    color: "#E7F1F6"

                    font.pixelSize: 13
                }

                MouseArea {
                    id: maximizeArea

                    anchors.fill: parent

                    hoverEnabled: true

                    cursorShape: Qt.PointingHandCursor

                    onClicked: {
                        if (root.visibility === Window.Maximized)
                            root.showNormal()
                        else
                            root.showMaximized()
                    }
                }
            }


            // ====================================================
            // CLOSE
            // ====================================================

            Rectangle {
                width: 42
                height: 32

                radius: 10

                color: closeArea.containsMouse
                       ? "#B83B5266"
                       : "#08FFFFFF"

                border.width: 1

                border.color: closeArea.containsMouse
                              ? "#F2A5B5"
                              : "#18FFFFFF"

                Text {
                    anchors.centerIn: parent

                    text: "×"

                    color: closeArea.containsMouse
                           ? "#FFFFFF"
                           : "#E7F1F6"

                    font.pixelSize: 18
                }

                MouseArea {
                    id: closeArea

                    anchors.fill: parent

                    hoverEnabled: true

                    cursorShape: Qt.PointingHandCursor

                    onClicked: root.close()
                }
            }
        }


        // ========================================================
        // MAIN CONTENT
        // ========================================================

        Item {
            id: content

            x: 282
            y: 18

            width: parent.width - 300
            height: parent.height - 36


            // ====================================================
            // TOP BAR
            // ====================================================

            Row {
                x: 4
                y: 6

                width: parent.width - 8
                height: 48

                spacing: 12


                // Search
                Rectangle {
                    width: 380
                    height: 44

                    radius: 14

                    color: "#0F101C28"

                    border.width: 1
                    border.color: "#58FFFFFF"

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 1

                        radius: 13

                        z: 0

                        gradient: Gradient {
                            GradientStop {
                                position: 0.00
                                color: "#16FFFFFF"
                            }

                            GradientStop {
                                position: 0.45
                                color: "#06FFFFFF"
                            }

                            GradientStop {
                                position: 1.00
                                color: "#0918D9E8"
                            }
                        }
                    }

                    LucideIcon {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        name: "search"

                        iconSize: 19

                        iconColor: "#B7D0E0"
                    }

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 45

                        anchors.verticalCenter: parent.verticalCenter

                        text: "Search plans, notes, payments..."

                        color: "#A4BBC9"

                        font.pixelSize: 12
                    }
                }


                Item {
                    width: parent.width - 530
                    height: 1
                }


                // Theme
                Rectangle {
                    width: 44
                    height: 44

                    radius: 14

                    color: "#0F101C28"

                    border.width: 1
                    border.color: "#50FFFFFF"

                    LucideIcon {
                        anchors.centerIn: parent

                        name: "moon"

                        iconSize: 19

                        iconColor: "#DDD6FE"
                    }

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        cursorShape: Qt.PointingHandCursor
                    }
                }


                // Notifications
                Rectangle {
                    width: 44
                    height: 44

                    radius: 14

                    color: "#0F101C28"

                    border.width: 1
                    border.color: "#50FFFFFF"

                    LucideIcon {
                        anchors.centerIn: parent

                        name: "bell-ring"

                        iconSize: 19

                        iconColor: "#A5F3FC"
                    }

                    MouseArea {
                        anchors.fill: parent

                        hoverEnabled: true

                        cursorShape: Qt.PointingHandCursor
                    }
                }
            }


            // ====================================================
            // PAGE HEADING
            // ====================================================

            Column {
                x: 4
                y: 86

                spacing: 5

                Text {
                    text: "Good afternoon"

                    color: "#D1E1EA"

                    font.pixelSize: 13
                }

                Text {
                    text: "Dashboard"

                    color: "#FFFFFF"

                    font.pixelSize: 31
                    font.bold: true
                }

                Text {
                    text: "Manage your subscriptions and upcoming renewals."

                    color: "#C0D1DB"

                    font.pixelSize: 12
                }
            }


            // ====================================================
            // METRIC CARDS
            // ====================================================

            Row {
                x: 4
                y: 190

                width: parent.width - 8
                height: 132

                spacing: 14


                // Total Plans
                Rectangle {
                    width: (parent.width - 42) / 4
                    height: 132

                    radius: 22

                    color: "#1710202C"

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 1

                        radius: 18

                        z: 0

                        gradient: Gradient {
                            GradientStop {
                                position: 0.00
                                color: "#18FFFFFF"
                            }

                            GradientStop {
                                position: 0.34
                                color: "#05FFFFFF"
                            }

                            GradientStop {
                                position: 0.72
                                color: "#0717D9E8"
                            }

                            GradientStop {
                                position: 1.00
                                color: "#05FFFFFF"
                            }
                        }
                    }

                    border.width: 1
                    border.color: "#42FFFFFF"

                    Column {
                        x: 20
                        y: 18

                        spacing: 8

                        Row {
                            spacing: 9

                            LucideIcon {
                                name: "layers-3"

                                iconSize: 19
                                iconColor: "#7DD3FC"
                            }

                            Text {
                                text: "TOTAL PLANS"

                                color: "#B5CAD7"

                                font.pixelSize: 9
                                font.bold: true
                                font.letterSpacing: 1.1
                            }
                        }

                        Text {
                            text: "24"

                            color: "#FFFFFF"

                            font.pixelSize: 31
                            font.bold: true
                        }

                        Text {
                            text: "All active subscriptions"

                            color: "#B0C3CF"

                            font.pixelSize: 10
                        }
                    }
                }


                // Active Plans
                Rectangle {
                    width: (parent.width - 42) / 4
                    height: 132

                    radius: 22

                    color: "#1511262B"

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 1

                        radius: 18

                        z: 0

                        gradient: Gradient {
                            GradientStop {
                                position: 0.00
                                color: "#18FFFFFF"
                            }

                            GradientStop {
                                position: 0.34
                                color: "#05FFFFFF"
                            }

                            GradientStop {
                                position: 0.72
                                color: "#0717D9E8"
                            }

                            GradientStop {
                                position: 1.00
                                color: "#05FFFFFF"
                            }
                        }
                    }

                    border.width: 1
                    border.color: "#42FFFFFF"

                    Column {
                        x: 20
                        y: 18

                        spacing: 8

                        Row {
                            spacing: 9

                            LucideIcon {
                                name: "circle-check"

                                iconSize: 19
                                iconColor: "#5EEAD4"
                            }

                            Text {
                                text: "ACTIVE PLANS"

                                color: "#B5CAD7"

                                font.pixelSize: 9
                                font.bold: true
                                font.letterSpacing: 1.1
                            }
                        }

                        Text {
                            text: "18"

                            color: "#FFFFFF"

                            font.pixelSize: 31
                            font.bold: true
                        }

                        Text {
                            text: "Currently active"

                            color: "#B0C3CF"

                            font.pixelSize: 10
                        }
                    }
                }


                // Due Soon
                Rectangle {
                    width: (parent.width - 42) / 4
                    height: 132

                    radius: 22

                    color: "#152E2517"

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 1

                        radius: 18

                        z: 0

                        gradient: Gradient {
                            GradientStop {
                                position: 0.00
                                color: "#18FFFFFF"
                            }

                            GradientStop {
                                position: 0.34
                                color: "#05FFFFFF"
                            }

                            GradientStop {
                                position: 0.72
                                color: "#0717D9E8"
                            }

                            GradientStop {
                                position: 1.00
                                color: "#05FFFFFF"
                            }
                        }
                    }

                    border.width: 1
                    border.color: "#42FFFFFF"

                    Column {
                        x: 20
                        y: 18

                        spacing: 8

                        Row {
                            spacing: 9

                            LucideIcon {
                                name: "clock-3"

                                iconSize: 19
                                iconColor: "#FCD34D"
                            }

                            Text {
                                text: "DUE SOON"

                                color: "#B5CAD7"

                                font.pixelSize: 9
                                font.bold: true
                                font.letterSpacing: 1.1
                            }
                        }

                        Text {
                            text: "5"

                            color: "#FFFFFF"

                            font.pixelSize: 31
                            font.bold: true
                        }

                        Text {
                            text: "Within 30 days"

                            color: "#B0C3CF"

                            font.pixelSize: 10
                        }
                    }
                }


                // Monthly Spending
                Rectangle {
                    width: (parent.width - 42) / 4
                    height: 132

                    radius: 22

                    color: "#15241B36"

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 1

                        radius: 18

                        z: 0

                        gradient: Gradient {
                            GradientStop {
                                position: 0.00
                                color: "#18FFFFFF"
                            }

                            GradientStop {
                                position: 0.34
                                color: "#05FFFFFF"
                            }

                            GradientStop {
                                position: 0.72
                                color: "#0717D9E8"
                            }

                            GradientStop {
                                position: 1.00
                                color: "#05FFFFFF"
                            }
                        }
                    }

                    border.width: 1
                    border.color: "#42FFFFFF"

                    Column {
                        x: 20
                        y: 18

                        spacing: 8

                        Row {
                            spacing: 9

                            LucideIcon {
                                name: "wallet"

                                iconSize: 19
                                iconColor: "#D8B4FE"
                            }

                            Text {
                                text: "MONTHLY SPENDING"

                                color: "#B5CAD7"

                                font.pixelSize: 9
                                font.bold: true
                                font.letterSpacing: 1.1
                            }
                        }

                        Text {
                            text: "₹12,450"

                            color: "#FFFFFF"

                            font.pixelSize: 28
                            font.bold: true
                        }

                        Text {
                            text: "Estimated monthly cost"

                            color: "#B0C3CF"

                            font.pixelSize: 10
                        }
                    }
                }
            }


            // ====================================================
            // MY PLANS
            // ====================================================

            Rectangle {
                id: plansPanel

                x: 4
                y: 342

                width: parent.width * 0.68

                height: parent.height - 360

                radius: 24

                color: "#1C101C27"

                border.width: 1
                border.color: "#50FFFFFF"


                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1

                    radius: 23

                    z: 0

                    gradient: Gradient {
                        GradientStop {
                            position: 0.00
                            color: "#16FFFFFF"
                        }

                        GradientStop {
                            position: 0.30
                            color: "#07FFFFFF"
                        }

                        GradientStop {
                            position: 0.72
                            color: "#0715D8E8"
                        }

                        GradientStop {
                            position: 1.00
                            color: "#09FFFFFF"
                        }
                    }
                }


                Text {
                    x: 20
                    y: 20

                    text: "My Plans"

                    color: "#FFFFFF"

                    font.pixelSize: 19
                    font.bold: true
                }


                Rectangle {
                    x: 108
                    y: 19

                    width: 34
                    height: 22

                    radius: 11

                    color: "#382B5E78"

                    border.width: 1
                    border.color: "#28FFFFFF"

                    Text {
                        anchors.centerIn: parent

                        text: "24"

                        color: "#B7E7FF"

                        font.pixelSize: 10
                        font.bold: true
                    }
                }


                Text {
                    x: 20
                    y: 50

                    text: "Your subscriptions and renewal status"

                    color: "#B5C8D4"

                    font.pixelSize: 10
                }


                // Table header
                Row {
                    x: 20
                    y: 88

                    width: parent.width - 40
                    height: 35

                    Text {
                        width: parent.width * 0.32

                        text: "PLAN"

                        color: "#9EB5C3"

                        font.pixelSize: 9
                        font.bold: true
                        font.letterSpacing: 1
                    }

                    Text {
                        width: parent.width * 0.20

                        text: "RENEWAL"

                        color: "#9EB5C3"

                        font.pixelSize: 9
                        font.bold: true
                        font.letterSpacing: 1
                    }

                    Text {
                        width: parent.width * 0.18

                        text: "AMOUNT"

                        color: "#9EB5C3"

                        font.pixelSize: 9
                        font.bold: true
                        font.letterSpacing: 1
                    }

                    Text {
                        width: parent.width * 0.30

                        text: "STATUS"

                        color: "#9EB5C3"

                        font.pixelSize: 9
                        font.bold: true
                        font.letterSpacing: 1
                    }
                }


                Rectangle {
                    x: 20
                    y: 123

                    width: parent.width - 40
                    height: 1

                    color: "#25FFFFFF"
                }


                Column {
                    x: 20
                    y: 136

                    width: parent.width - 40

                    spacing: 3


                    // Netflix
                    Rectangle {
                        width: parent.width
                        height: 58

                        radius: 13

                        color: "#0DFFFFFF"

                        border.width: 1
                        border.color: "#0BFFFFFF"

                        Row {
                            anchors.fill: parent

                            anchors.leftMargin: 14
                            anchors.rightMargin: 14

                            Text {
                                width: parent.width * 0.32

                                anchors.verticalCenter: parent.verticalCenter

                                text: "Netflix"

                                color: "#FFFFFF"

                                font.pixelSize: 12
                                font.bold: true
                            }

                            Text {
                                width: parent.width * 0.20

                                anchors.verticalCenter: parent.verticalCenter

                                text: "12 Oct 2026"

                                color: "#C1D1DA"

                                font.pixelSize: 11
                            }

                            Text {
                                width: parent.width * 0.18

                                anchors.verticalCenter: parent.verticalCenter

                                text: "₹649"

                                color: "#D8E4EB"

                                font.pixelSize: 11
                            }

                            Rectangle {
                                width: 74
                                height: 24

                                anchors.verticalCenter: parent.verticalCenter

                                radius: 12

                                color: "#284B7C70"

                                Text {
                                    anchors.centerIn: parent

                                    text: "Active"

                                    color: "#99F6E4"

                                    font.pixelSize: 10
                                    font.bold: true
                                }
                            }
                        }
                    }


                    // Spotify
                    Rectangle {
                        width: parent.width
                        height: 58

                        radius: 13

                        color: "#06FFFFFF"

                        Row {
                            anchors.fill: parent

                            anchors.leftMargin: 14
                            anchors.rightMargin: 14

                            Text {
                                width: parent.width * 0.32

                                anchors.verticalCenter: parent.verticalCenter

                                text: "Spotify Premium"

                                color: "#FFFFFF"

                                font.pixelSize: 12
                                font.bold: true
                            }

                            Text {
                                width: parent.width * 0.20

                                anchors.verticalCenter: parent.verticalCenter

                                text: "18 Oct 2026"

                                color: "#C1D1DA"

                                font.pixelSize: 11
                            }

                            Text {
                                width: parent.width * 0.18

                                anchors.verticalCenter: parent.verticalCenter

                                text: "₹119"

                                color: "#D8E4EB"

                                font.pixelSize: 11
                            }

                            Rectangle {
                                width: 74
                                height: 24

                                anchors.verticalCenter: parent.verticalCenter

                                radius: 12

                                color: "#493D2266"

                                Text {
                                    anchors.centerIn: parent

                                    text: "Due Soon"

                                    color: "#FDE68A"

                                    font.pixelSize: 10
                                    font.bold: true
                                }
                            }
                        }
                    }


                    // Adobe
                    Rectangle {
                        width: parent.width
                        height: 58

                        radius: 13

                        color: "#06FFFFFF"

                        Row {
                            anchors.fill: parent

                            anchors.leftMargin: 14
                            anchors.rightMargin: 14

                            Text {
                                width: parent.width * 0.32

                                anchors.verticalCenter: parent.verticalCenter

                                text: "Adobe Creative Cloud"

                                color: "#FFFFFF"

                                font.pixelSize: 12
                                font.bold: true
                            }

                            Text {
                                width: parent.width * 0.20

                                anchors.verticalCenter: parent.verticalCenter

                                text: "22 Oct 2026"

                                color: "#C1D1DA"

                                font.pixelSize: 11
                            }

                            Text {
                                width: parent.width * 0.18

                                anchors.verticalCenter: parent.verticalCenter

                                text: "₹1,675"

                                color: "#D8E4EB"

                                font.pixelSize: 11
                            }

                            Rectangle {
                                width: 74
                                height: 24

                                anchors.verticalCenter: parent.verticalCenter

                                radius: 12

                                color: "#284B7C70"

                                Text {
                                    anchors.centerIn: parent

                                    text: "Active"

                                    color: "#99F6E4"

                                    font.pixelSize: 10
                                    font.bold: true
                                }
                            }
                        }
                    }
                }
            }


            // ====================================================
            // QUICK PLAN DETAILS
            // ====================================================

            Rectangle {
                id: detailsPanel

                x: plansPanel.x + plansPanel.width + 14

                y: 342

                width: parent.width - plansPanel.width - 22

                height: plansPanel.height

                radius: 24

                color: "#1C101C27"

                border.width: 1
                border.color: "#50FFFFFF"


                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1

                    radius: 23

                    z: 0

                    gradient: Gradient {
                        GradientStop {
                            position: 0.00
                            color: "#16FFFFFF"
                        }

                        GradientStop {
                            position: 0.30
                            color: "#07FFFFFF"
                        }

                        GradientStop {
                            position: 0.72
                            color: "#0715D8E8"
                        }

                        GradientStop {
                            position: 1.00
                            color: "#09FFFFFF"
                        }
                    }
                }


                Text {
                    x: 20
                    y: 20

                    text: "Quick Plan Details"

                    color: "#FFFFFF"

                    font.pixelSize: 18
                    font.bold: true
                }


                Text {
                    x: 20
                    y: 50

                    text: "Selected subscription"

                    color: "#B5C8D4"

                    font.pixelSize: 10
                }


                Rectangle {
                    x: 20
                    y: 88

                    width: parent.width - 40
                    height: 92

                    radius: 18

                    color: "#20162736"

                    border.width: 1
                    border.color: "#42FFFFFF"


                    Row {
                        x: 15

                        anchors.verticalCenter: parent.verticalCenter

                        spacing: 13

                        Rectangle {
                            width: 48
                            height: 48

                            radius: 15

                            color: "#32305A78"

                            LucideIcon {
                                anchors.centerIn: parent

                                name: "play"

                                iconSize: 23
                                iconColor: "#FDA4AF"
                            }
                        }


                        Column {
                            anchors.verticalCenter: parent.verticalCenter

                            spacing: 4

                            Text {
                                text: "Netflix"

                                color: "#FFFFFF"

                                font.pixelSize: 14
                                font.bold: true
                            }

                            Text {
                                text: "Entertainment"

                                color: "#B0C5D1"

                                font.pixelSize: 10
                            }
                        }
                    }
                }


                Text {
                    x: 20
                    y: 205

                    text: "RENEWAL DATE"

                    color: "#9EB5C3"

                    font.pixelSize: 9
                    font.bold: true
                    font.letterSpacing: 1
                }


                Text {
                    x: 20
                    y: 226

                    text: "12 October 2026"

                    color: "#FFFFFF"

                    font.pixelSize: 13
                    font.bold: true
                }


                Text {
                    x: 20
                    y: 270

                    text: "AMOUNT"

                    color: "#9EB5C3"

                    font.pixelSize: 9
                    font.bold: true
                    font.letterSpacing: 1
                }


                Text {
                    x: 20
                    y: 291

                    text: "₹649 / month"

                    color: "#FFFFFF"

                    font.pixelSize: 13
                    font.bold: true
                }


                Rectangle {
                    x: 20
                    y: 338

                    width: parent.width - 40
                    height: 1

                    color: "#25FFFFFF"
                }


                Text {
                    x: 20
                    y: 362

                    text: "STATUS"

                    color: "#9EB5C3"

                    font.pixelSize: 9
                    font.bold: true
                    font.letterSpacing: 1
                }


                Rectangle {
                    x: 20
                    y: 387

                    width: 78
                    height: 27

                    radius: 14

                    color: "#284B7C70"

                    Text {
                        anchors.centerIn: parent

                        text: "Active"

                        color: "#99F6E4"

                        font.pixelSize: 10
                        font.bold: true
                    }
                }
            }
        }
    }
}