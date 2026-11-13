import SwiftUI

/// The strip round the screen, in its live colours. Dragging across it selects
/// a run of LEDs; locked runs show their locked colour.
struct LEDFrame: View {
    @EnvironmentObject private var store: HubStore
    @Binding var selection: ClosedRange<Int>?
    var pick: RGB
    @State private var dragFrom: Int?

    var body: some View {
        GeometryReader { geo in
            let status = store.status
            let rect = CGRect(origin: .zero, size: geo.size)
            let layout = status.map {
                $0.layout == "frame" ? LEDLayout.frame(counts: $0.edges, in: rect)
                    : LEDLayout.strip(count: $0.ledCount, in: rect)
            }
            Canvas { context, _ in
                guard let status, let layout else { return }
                context.fill(Path(roundedRect: layout.screen, cornerRadius: 3), with: .color(Color(hex: "#0f0f0d")))
                context.stroke(Path(roundedRect: layout.screen, cornerRadius: 3), with: .color(Theme.ruleHi))
                let caption = selection.map { "LEDS \($0.lowerBound)–\($0.upperBound)" } ?? "DRAG ACROSS THE LEDS"
                context.draw(Text(caption).font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(Theme.dim), at: CGPoint(x: layout.screen.midX, y: layout.screen.midY))
                var locked = [Int: RGB]()
                for zone in status.zones where zone.mode == "solid" {
                    for led in zone.start...max(zone.start, zone.end) { locked[led] = RGB(hex: zone.color) }
                }
                for led in layout.leds {
                    let picked = selection?.contains(led.index) ?? false
                    let live = status.preview.isEmpty ? nil : RGB(hex: status.preview[
                        swatchIndex(led: led.index, ledCount: status.ledCount, swatches: status.preview.count)])
                    let colour = picked ? pick : locked[led.index] ?? (live?.isLit == true ? live! : nil)
                    let size = picked ? layout.size * 1.5 : layout.size
                    let box = CGRect(x: led.point.x - size / 2, y: led.point.y - size / 2, width: size, height: size)
                    let fill = colour.map { Color(.sRGB, red: $0.r, green: $0.g, blue: $0.b) } ?? Color(hex: "#24231f")
                    context.drawLayer { layer in
                        if colour != nil { layer.addFilter(.shadow(color: fill.opacity(0.9), radius: size * 0.9)) }
                        layer.fill(Path(roundedRect: box, cornerRadius: 1.5), with: .color(fill))
                    }
                }
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { drag in
                    guard let layout else { return }
                    if dragFrom == nil { dragFrom = layout.nearest(to: drag.startLocation) }
                    guard let from = dragFrom, let to = layout.nearest(to: drag.location) else { return }
                    selection = min(from, to)...max(from, to)
                }
                .onEnded { _ in dragFrom = nil })
        }
        .aspectRatio(frameAspect, contentMode: .fit)
        .background(Theme.inset, in: RoundedRectangle(cornerRadius: 4))
        .accessibilityLabel("The LEDs round the screen; drag across them to select")
    }

    private var frameAspect: CGFloat {
        guard let status = store.status, status.layout == "frame" else { return 1 / 0.3 }
        let ratio = min(max(status.screenMmH / max(1, status.screenMmW), 0.35), 1)
        return CGFloat(1 / ratio)
    }
}
