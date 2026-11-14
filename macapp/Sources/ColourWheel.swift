import SwiftUI

/// The phone page's colour wheel: Any colour (hue round the rim, white at the centre) or Whites (daylight at the top,
/// candlelight below), with the last few picks under it. `pick` gets each shade while it is dragged (`final` false)
/// and once more when it is let go.
struct ColourWheel: View {
    /// What the light shows now; nil when it shows no colour of its own.
    let hex: String?
    let label: String
    let pick: (_ hex: String, _ final: Bool) -> Void

    @AppStorage("recentColours") private var recentList = ""
    @State private var whites = false
    @State private var shown = swatches[0].hex
    @State private var dragging = false
    @State private var touched = Date.distantPast
    @FocusState private var focused: Bool

    private var recent: [String] { recentList.split(separator: ",").map(String.init).filter { RGB(hex: $0) != nil } }

    var body: some View {
        VStack(spacing: 12) {
            Segmented([(false, "Any colour"), (true, "Whites")], chosen: whites) { switchTo(whites: $0) }
            disc
            Text("\(colourName(shown)) · \(whites ? kelvinOf(shown).map { "\($0) K" } ?? shown.uppercased() : shown.uppercased())")
                .font(.system(size: 12, weight: .medium, design: .monospaced)).foregroundStyle(Theme.dim)
            if !recent.isEmpty {
                HStack(spacing: 6) {
                    Text("Recent").font(.system(size: 11.5)).foregroundStyle(Theme.dim)
                    ForEach(recent, id: \.self) { colour in
                        SwatchButton(hex: colour, name: "Recent: \(colourName(colour))", chosen: colour == shown, size: 22) {
                            if kelvinOf(colour) == nil { whites = false }
                            edit(colour, final: true, keep: true)
                        }
                    }
                    Spacer(minLength: 0)
                }
            }
        }
        .onAppear { if let hex { shown = hex } }
        .onChange(of: hex) { _, new in
            guard let new, !dragging, Date().timeIntervalSince(touched) > HubStore.holdSeconds else { return }
            shown = new
            if whites && kelvinOf(new) == nil { whites = false }
        }
    }

    private var disc: some View {
        GeometryReader { geo in
            let side = min(geo.size.width, geo.size.height)
            let spot = Wheel.spot(shown, whites: whites)
            ZStack {
                if whites {
                    Circle().fill(LinearGradient(colors: [coolestKelvin, 5500, 4500, 3500, 2800, warmestKelvin]
                        .map { Color(hex: RGB(kelvin: $0).hex) }, startPoint: .top, endPoint: .bottom))
                } else {
                    // Pure hues, not the system's softer .red/.green, so the rim shows exactly what a pick sends.
                    Circle().fill(AngularGradient(colors: ["#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff", "#ff0000"]
                        .map { Color(hex: $0) }, center: .center, startAngle: .degrees(-90), endAngle: .degrees(270)))
                    Circle().fill(RadialGradient(colors: [.white, .white.opacity(0)], center: .center,
                                                 startRadius: 0, endRadius: side / 2))
                }
                Circle().stroke(Theme.lineHi)
                Circle().fill(Color(hex: shown)).frame(width: 30, height: 30)
                    .overlay(Circle().stroke(.white, lineWidth: 3))
                    .shadow(color: .black.opacity(0.4), radius: 4, y: 1)
                    .scaleEffect(dragging ? 1.25 : 1)
                    .position(x: side / 2 * (1 + spot.x), y: side / 2 * (1 + spot.y))
                    .animation(.easeOut(duration: 0.12), value: dragging)
            }
            .frame(width: side, height: side)
            .shadow(color: Color(hex: shown).opacity(0.35), radius: 24, y: 8)
            .contentShape(Circle())
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { drag in
                    dragging = true
                    focused = true
                    edit(at(drag.location, side: side), final: false, keep: false)
                }
                .onEnded { drag in
                    dragging = false
                    edit(at(drag.location, side: side), final: true, keep: true)
                })
            .frame(maxWidth: .infinity)
        }
        .frame(width: 220, height: 220)
        .focusable()
        .focused($focused)
        .onKeyPress(keys: [.leftArrow, .rightArrow, .upArrow, .downArrow]) { press in
            let across = press.key == .leftArrow ? -1 : press.key == .rightArrow ? 1 : 0
            let up = press.key == .upArrow ? 1 : press.key == .downArrow ? -1 : 0
            edit(Wheel.step(shown, whites: whites, across: across, up: up), final: true, keep: true)
            return .handled
        }
        .accessibilityElement()
        .accessibilityLabel(label)
        .accessibilityValue(colourName(shown))
        .accessibilityAdjustableAction { direction in
            edit(Wheel.step(shown, whites: whites, across: direction == .increment ? 1 : -1, up: 0), final: true, keep: true)
        }
    }

    /// The shade under a point of the disc. The outer few points count as the rim, so the edge is easy to reach.
    private func at(_ point: CGPoint, side: CGFloat) -> String {
        let r = side / 2 - 6
        return Wheel.pick(x: (point.x - side / 2) / r, y: (point.y - side / 2) / r, whites: whites).hex
    }

    private func edit(_ colour: String, final: Bool, keep: Bool) {
        shown = colour
        touched = Date()
        pick(colour, final)
        if keep { recentList = remembering(colour, in: recent).joined(separator: ",") }
    }

    /// Whites light a warm white at once, as every scene button acts on the first click, but it is not kept as a pick.
    private func switchTo(whites next: Bool) {
        guard next != whites else { return }
        whites = next
        if next { edit(RGB(kelvin: kelvinOf(shown) ?? 2700).hex, final: true, keep: false) }
    }
}
