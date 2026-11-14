import SwiftUI

/// The strip round the screen in its live colours, with each part of the strip in its own. With a `selection`,
/// dragging across it picks a run of LEDs to show `pick` on.
struct LEDFrame: View {
    @EnvironmentObject private var store: HubStore
    var selection: Binding<ClosedRange<Int>?>?
    var pick: String?
    @State private var dragFrom: Int?

    var body: some View {
        GeometryReader { geo in
            let status = store.status
            let rect = CGRect(origin: .zero, size: geo.size)
            let layout = status.map {
                $0.layout == "frame" ? LEDLayout.frame(counts: $0.edges, in: rect) : LEDLayout.strip(count: $0.ledCount, in: rect)
            }
            let chosen = selection?.wrappedValue
            let scene = store.scene
            Canvas { context, _ in
                guard let status, let layout else { return }
                context.fill(Path(roundedRect: layout.screen, cornerRadius: 6), with: .color(Theme.well))
                let caption = selection != nil
                    ? chosen.map { "LEDs \($0.lowerBound)–\($0.upperBound) · \($0.count) selected" } ?? "Drag across the LEDs to pick a stretch"
                    : scene == .off ? "Lights off" : scene == .effect ? store.effectName(status.wledFx) : ""
                context.draw(Text(caption).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.dim),
                             at: CGPoint(x: layout.screen.midX, y: layout.screen.midY))
                var parts = [Int: String?]()
                for zone in status.zones {
                    for led in zone.start...max(zone.start, zone.end) { parts[led] = partTint(zone) }
                }
                let solid = scene == .colour ? status.wledCol.map { "#" + $0 } : nil
                for led in layout.leds {
                    let picked = chosen?.contains(led.index) ?? false
                    // The hub's preview is the screen's colours; it goes stale once sync stops.
                    let live = !status.running || status.preview.isEmpty ? nil : status.preview[
                        swatchIndex(led: led.index, ledCount: status.ledCount, swatches: status.preview.count)]
                    // A part switched off stays dark rather than falling back to the scene.
                    let hex: String? = picked ? pick
                        : parts[led.index] ?? (scene == .off ? nil : solid ?? live.flatMap { RGB(hex: $0)?.isLit == true ? $0 : nil })
                    let size = picked ? layout.size * 1.5 : layout.size
                    let box = CGRect(x: led.point.x - size / 2, y: led.point.y - size / 2, width: size, height: size)
                    let fill = hex.map { Color(hex: $0) } ?? Theme.ledOff
                    context.drawLayer { layer in
                        if hex != nil { layer.addFilter(.shadow(color: fill.opacity(0.9), radius: size * 0.9)) }
                        layer.fill(Path(roundedRect: box, cornerRadius: size / 3), with: .color(fill))
                    }
                }
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { drag in
                    guard let selection, let layout else { return }
                    if dragFrom == nil { dragFrom = layout.nearest(to: drag.startLocation) }
                    guard let from = dragFrom, let to = layout.nearest(to: drag.location) else { return }
                    selection.wrappedValue = min(from, to)...max(from, to)
                }
                .onEnded { _ in dragFrom = nil },
                including: selection == nil ? .none : .all)
        }
        .aspectRatio(frameAspect, contentMode: .fit)
        .accessibilityLabel(selection == nil ? "The LEDs in their live colours" : "The LEDs; drag across them to select")
    }

    private var frameAspect: CGFloat {
        guard let status = store.status, status.layout == "frame" else { return 1 / 0.3 }
        let ratio = min(max(status.screenMmH / max(1, status.screenMmW), 0.35), 1)
        return CGFloat(1 / ratio)
    }
}
