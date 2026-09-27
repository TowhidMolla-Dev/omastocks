import QtQuick
import QtQuick.Layouts
import qs.Commons
import ".."

// Page header with the market sky behind it. Content goes into a RowLayout;
// `session` is exposed so a MarketStatus chip can be placed among it.
Item {
    id: root
    default property alias content: row.data
    readonly property alias session: session
    // When true the chip follows the selected row's own exchange, so a Dhaka
    // row shows Dhaka hours rather than the New York session.
    property bool selectedExchange: false
    implicitHeight: row.implicitHeight + Style.space(48)
    MarketSession { id: session; active: root.visible && StockStore.windowOpen; dhaka: root.selectedExchange && StockStore.selectedIsDse }
    MarketSky { anchors.fill: parent; session: session }
    RowLayout {
        id: row
        anchors.fill: parent
        anchors.margins: Style.space(24)
        spacing: Style.space(12)
    }
    // Painted over the content, then dissolved to reveal it. It takes no input,
    // so the header stays clickable. Replays each time the window opens here.
    StocksIntro { id: intro; anchors.fill: parent }
    Connections {
        target: StockStore
        function onWindowOpenChanged() { if (StockStore.windowOpen && root.visible) intro.play(); else if (!StockStore.windowOpen) intro.stop() }
    }
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Util.alpha(Color.foreground, .09) }
}
