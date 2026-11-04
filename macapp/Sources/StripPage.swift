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

struct StripPage: View {
    @EnvironmentObject private var store: HubStore
    @State private var selection: ClosedRange<Int>?
    @State private var colour = Color(hex: "#ff2010")
    @State private var brightness = 1.0
    @State private var keep = true

    private static let swatches = ["#ffb46b", "#ffffff", "#ff2010", "#ff8c00", "#20e050", "#00c8ff", "#2040ff", "#e030ff"]

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                Card(title: "Live") {
                    LEDFrame(selection: $selection, pick: colour.rgb)
                    HStack {
                        Text(selection.map { "LEDs \($0.lowerBound)–\($0.upperBound) · \($0.count) selected" }
                             ?? "Drag across the LEDs to pick a stretch to lock").hint()
                        Spacer()
                        if selection != nil { Button("Clear selection") { selection = nil } }
                        Button("Select all") { selection = 0...max(0, (store.status?.ledCount ?? 1) - 1) }
                    }
                }
                HStack(alignment: .top, spacing: 16) {
                    Card(title: "Look") {
                        ProfileChoices()
                        SettingSlider(key: "brightness", label: "Brightness")
                        SettingSlider(key: "saturation", label: "Colour", range: 0...3)
                        SettingSlider(key: "immersion", label: "Immersion",
                                      hint: "Lifts deep colours and floods the room with the scene's colour. Skin and greys stay true.",
                                      format: { $0 < 0.005 ? "Off" : "\(Int(($0 * 100).rounded()))%" })
                        SettingSlider(key: "blend", label: "Response", range: 0.01...1,
                                      format: { String(format: "%.2f s", 0.06 * pow(0.8 / 0.06, 1 - $0)) })
                    }
                    Card(title: "Override") {
                        HStack(spacing: 12) {
                            ColorPicker("Colour", selection: $colour, supportsOpacity: false).labelsHidden()
                            Text("Colour").caps(Theme.ink)
                        }
                        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 4), spacing: 6) {
                            ForEach(Self.swatches, id: \.self) { hex in
                                Button { colour = Color(hex: hex) } label: {
                                    RoundedRectangle(cornerRadius: 2).fill(Color(hex: hex)).frame(height: 30)
                                        .overlay(RoundedRectangle(cornerRadius: 2)
                                            .stroke(colour.rgb.hex == hex ? Theme.amber : .black, lineWidth: 2))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        VStack(alignment: .leading, spacing: 6) {
                            HStack { Text("Brightness").caps(Theme.ink); Spacer()
                                Text("\(Int((brightness * 100).rounded()))%").readout() }
                            Slider(value: $brightness, in: 0...1).tint(Theme.amber)
                        }
                        HStack(spacing: 4) {
                            ChoiceButton(title: "Keep", chosen: keep) { keep = true }
                            ChoiceButton(title: "Until restart", chosen: !keep) { keep = false }
                        }
                        Button {
                            guard let range = selection else { return }
                            store.lock(range, colour: colour.rgb, brightness: brightness, keep: keep)
                            selection = nil
                        } label: {
                            Text("Lock colour").caps(Color(hex: "#1a1200")).frame(maxWidth: .infinity, minHeight: 36)
                                .background(selection == nil ? Theme.amber.opacity(0.4) : Theme.amber,
                                            in: RoundedRectangle(cornerRadius: 4))
                        }
                        .buttonStyle(.plain)
                        .disabled(selection == nil)
                        .keyboardShortcut(.return, modifiers: .command)
                    }
                }
                Card(title: "Locked") {
                    if store.locks.isEmpty {
                        Text("Nothing locked: the whole strip follows the screen.").hint()
                    } else {
                        ForEach(store.locks, id: \.offset) { item in
                            HStack(spacing: 10) {
                                RoundedRectangle(cornerRadius: 2).fill(Color(hex: item.zone.color)).frame(width: 14, height: 14)
                                Text("LEDs \(item.zone.start)–\(item.zone.end) · \(Int((item.zone.brightness * 100).rounded()))%")
                                    .font(.system(size: 13, design: .monospaced)).foregroundStyle(Theme.ink)
                                Spacer()
                                Text(item.zone.keep == false ? "Until restart" : "Kept")
                                    .caps(item.zone.keep == false ? Theme.dim : Theme.green)
                                Button("Unlock") { store.unlock(at: item.offset) }
                            }
                            .padding(8)
                            .overlay(RoundedRectangle(cornerRadius: 4).stroke(Theme.rule))
                        }
                        Button("Unlock everything") { store.unlockAll() }
                    }
                }
            }
            .padding(20)
        }
    }
}
