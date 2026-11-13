import SwiftUI

/// Every light on one page, like the phone page's Lights tab: the monitor strip,
/// the bed and the mouse, each offering screen sync, a colour, an effect or off.
struct LightsPage: View {
    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                MonitorCard()
                BedPage()
                MousePage()
            }
            .padding(20)
        }
    }
}

struct MonitorCard: View {
    @EnvironmentObject private var store: HubStore
    /// A pane the user opened; nil shows what the strip is doing.
    @State private var opened: String?
    @State private var selection: ClosedRange<Int>?
    @State private var lockColour = Color(hex: "#ff2010")
    @State private var lockBrightness = 1.0
    @State private var keep = true
    @State private var colour = Color(hex: "#ffb46b")
    @State private var effect: Int?
    @State private var showLocks = false

    private static let modes = [("sync", "Screen sync"), ("colour", "Colour"), ("effect", "Effect"), ("off", "Off")]
    static let swatches = ["#ffb46b", "#ffffff", "#ff2010", "#ff8c00", "#20e050", "#00c8ff", "#2040ff", "#e030ff"]

    var body: some View {
        let shown = opened ?? store.status?.mode ?? "sync"
        Card(title: "Monitor strip") {
            LEDFrame(selection: $selection, pick: lockColour.rgb)
            HStack(spacing: 4) {
                ForEach(Self.modes, id: \.0) { value, title in
                    ChoiceButton(title: title, chosen: shown == value) { choose(value) }
                }
            }
            switch shown {
            case "sync": locks
            case "colour": colours
            case "effect": effects
            default: Text("The monitor strip is off. Pick screen sync, a colour or an effect to light it.").hint()
            }
        }
        .onAppear { store.loadEffects() }
        .onChange(of: store.status?.mode) { _, mode in
            // Sync or off chosen anywhere (the phone, the board) wins over a pane left open here.
            if mode == "sync" || mode == "off" { opened = nil }
        }
    }

    private func choose(_ value: String) {
        opened = value
        if value == "sync" { store.setSync(true) }
        if value == "off" { store.lightsOff() }
    }

    private var colours: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 12) {
                ColorPicker("Colour", selection: Binding(get: { colour }, set: {
                    colour = $0
                    store.runColourLater(String($0.rgb.hex.dropFirst()))
                }), supportsOpacity: false).labelsHidden()
                Text("Pick any colour, or a swatch").hint()
            }
            swatchGrid(selected: colour.rgb.hex) { hex in
                colour = Color(hex: hex)
                store.runColour(String(hex.dropFirst()))
            }
        }
    }

    private var effects: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 4)], spacing: 4) {
            ForEach(store.effects, id: \.fx) { item in
                ChoiceButton(title: item.name, chosen: effect == item.fx) {
                    effect = item.fx
                    store.runEffect(item.fx)
                }
            }
        }
    }

    private var locks: some View {
        DisclosureGroup(isExpanded: $showLocks) {
            VStack(alignment: .leading, spacing: 12) {
                Text(selection.map { "LEDs \($0.lowerBound)–\($0.upperBound) · \($0.count) selected" }
                     ?? "Drag across the LEDs above to pick a stretch; the rest keeps following the screen.").hint()
                HStack(spacing: 12) {
                    ColorPicker("Colour", selection: $lockColour, supportsOpacity: false).labelsHidden()
                    if selection != nil { Button("Clear selection") { selection = nil } }
                    Button("Select all") { selection = 0...max(0, (store.status?.ledCount ?? 1) - 1) }
                }
                swatchGrid(selected: lockColour.rgb.hex) { lockColour = Color(hex: $0) }
                VStack(alignment: .leading, spacing: 6) {
                    HStack { Text("Brightness").caps(Theme.ink); Spacer()
                        Text("\(Int((lockBrightness * 100).rounded()))%").readout() }
                    Slider(value: $lockBrightness, in: 0...1).tint(Theme.amber)
                }
                HStack(spacing: 4) {
                    ChoiceButton(title: "Keep", chosen: keep) { keep = true }
                    ChoiceButton(title: "Until restart", chosen: !keep) { keep = false }
                }
                Button {
                    guard let range = selection else { return }
                    store.lock(range, colour: lockColour.rgb, brightness: lockBrightness, keep: keep)
                    selection = nil
                } label: {
                    Text("Lock colour").caps(Color(hex: "#1a1200")).frame(maxWidth: .infinity, minHeight: 36)
                        .background(selection == nil ? Theme.amber.opacity(0.4) : Theme.amber,
                                    in: RoundedRectangle(cornerRadius: 4))
                }
                .buttonStyle(.plain)
                .disabled(selection == nil)
                .keyboardShortcut(.return, modifiers: .command)
                lockedList
            }
            .padding(.top, 8)
        } label: {
            Text(store.locks.isEmpty ? "Lock part of the strip" : "Lock part of the strip · \(store.locks.count) locked")
                .caps(Theme.ink)
        }
    }

    @ViewBuilder private var lockedList: some View {
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

    private func swatchGrid(selected: String, pick: @escaping (String) -> Void) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 8), spacing: 6) {
            ForEach(Self.swatches, id: \.self) { hex in
                Button { pick(hex) } label: {
                    RoundedRectangle(cornerRadius: 2).fill(Color(hex: hex)).frame(height: 30)
                        .overlay(RoundedRectangle(cornerRadius: 2).stroke(selected == hex ? Theme.amber : .black, lineWidth: 2))
                }
                .buttonStyle(.plain)
            }
        }
    }
}

/// How the light looks while it follows the screen: the mode, the sliders and your own profiles.
struct PicturePage: View {
    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                ProfilesPage()
                Card(title: "Adjust") {
                    SettingSlider(key: "brightness", label: "Brightness")
                    SettingSlider(key: "saturation", label: "Colour", range: 0...3)
                    SettingSlider(key: "immersion", label: "Immersion",
                                  hint: "Lifts deep colours and floods the room with the scene's colour. Skin and greys stay true.",
                                  format: offOrPercent)
                    SettingSlider(key: "dark_boost", label: "Dark scenes",
                                  hint: "How much the light still shows and reacts when the whole picture is dark. "
                                      + "Bright scenes are unchanged.",
                                  format: offOrPercent)
                    SettingSlider(key: "blend", label: "Response", range: 0.01...1,
                                  format: { String(format: "%.2f s", 0.06 * pow(0.8 / 0.06, 1 - $0)) })
                }
            }
            .padding(20)
        }
    }
}

/// Things done once: the bed strip's wiring and the webcam calibration.
struct SetupPage: View {
    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                BedPage(wiring: true)
                CalibratePage()
            }
            .padding(20)
        }
    }
}

func offOrPercent(_ value: Double) -> String { value < 0.005 ? "Off" : "\(Int((value * 100).rounded()))%" }
