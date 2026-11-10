import SwiftUI

/// The feel of a TV's ambient-light menu on top of the hub's own settings: what
/// the light follows, the sync mode, room glows, the lights round the room, the
/// wall and the switch-off fade. The choices come from the hub (/api/ambient),
/// the same ones the phone page's Ambient tab offers.
struct AmbientPage: View {
    @EnvironmentObject private var store: HubStore
    var openCalibrate: () -> Void

    var body: some View {
        let status = store.status
        ScrollView {
            VStack(spacing: 16) {
                followCard(status)
                if let tables = store.ambient {
                    screenCard(tables, status)
                    glowCard(tables, status)
                    roomCard(tables, status)
                } else {
                    Card(title: "Screen") { Text("Loading the choices from the hub…").hint() }
                }
                wallCard(status)
            }
            .padding(20)
        }
        .onAppear { store.loadAmbient() }
    }

    // MARK: cards

    private func followCard(_ status: HubStatus?) -> some View {
        let follow = following(status)
        return Card(title: "Ambient") {
            HStack(spacing: 4) {
                ChoiceButton(title: "Screen", chosen: follow == "screen") { store.setSync(true) }
                ChoiceButton(title: "Glow", chosen: follow == "glow") {
                    if let glow = startingGlow { store.run(glow: glow) }
                }
                ChoiceButton(title: "Off", chosen: follow == "off") { store.switchOff() }
            }
            Text(followHint(status, follow)).hint()
        }
    }

    private func screenCard(_ tables: AmbientTables, _ status: HubStatus?) -> some View {
        Card(title: "Screen") {
            Text("Sync mode").caps()
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: 8)], spacing: 8) {
                ForEach(tables.modes, id: \.name) { mode in
                    AmbientTile(title: mode.label, about: mode.about, chosen: status?.profile == mode.name) {
                        store.apply(profile: mode.name)
                    }
                }
            }
            Text(String(format: "Settles on a new colour in %.2f s.", settleSeconds(blend: status?.blend ?? 0.3))).hint()
            SettingSlider(key: "brightness", label: "Brightness")
            SettingSlider(key: "saturation", label: "Colour", range: 0...3)
            SettingSlider(key: "white_kelvin", label: "Warmth", range: 2700...6500,
                          format: { "\(Int(($0 / 100).rounded()) * 100) K" })
        }
    }

    private func glowCard(_ tables: AmbientTables, _ status: HubStatus?) -> some View {
        Card(title: "Room glow") {
            Text("Runs on the controller itself, so it keeps glowing while the Mac sleeps.").hint()
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 180), spacing: 8)], spacing: 8) {
                ForEach(tables.glows, id: \.name) { glow in
                    AmbientTile(title: glow.label, about: glow.about,
                                chosen: status?.mode == "effect" && store.glowName == glow.name,
                                chip: glow.color.map { Color(hex: "#" + $0) }) {
                        store.run(glow: glow)
                    }
                }
            }
            BoardBrightnessSlider()
        }
    }

    private func roomCard(_ tables: AmbientTables, _ status: HubStatus?) -> some View {
        Card(title: "Around the room") {
            if (status?.bedLeds ?? 0) > 0 {
                Text("Bed strip follows").caps()
                HStack(spacing: 4) {
                    ForEach(tables.bed, id: \.name) { choice in
                        ChoiceButton(title: choice.label, chosen: matches(choice, status)) { store.apply(preset: choice) }
                    }
                }
                Text(tables.bed.first { matches($0, status) }?.about
                     ?? "Set on the Bed strip page: its own effect, colour or one edge.").hint()
                SettingSlider(key: "bed_brightness", label: "Bed brightness")
            } else {
                Text("No bed strip yet. Set one up on the Bed strip page.").hint()
            }
            Toggle(isOn: Binding(get: { status?.mouseSync ?? false }, set: { store.set("mouse_sync", $0 ? "1" : "0") })) {
                Text("Mouse follows the screen").caps(Theme.ink)
            }
            .toggleStyle(.switch).tint(Theme.green)
            Text(status?.mouse?.connected == true ? "Its lights take the colours along the bottom of the screen."
                 : "No Logitech LIGHTSYNC mouse is plugged in.").hint()
        }
    }

    private func wallCard(_ status: HubStatus?) -> some View {
        Card(title: "Wall and switch-off") {
            Text((status?.ledMatrix ?? []).count == 9
                 ? "Your wall is measured: the webcam calibration already allows for its colour."
                 : "Your wall isn't measured yet. A coloured wall tints the light; the webcam calibration measures it "
                   + "and corrects for it.").hint()
            KeyButton(title: "Open Calibrate", action: openCalibrate)
            SettingSlider(key: "off_fade_s", label: "Switch-off fade", range: 0...10,
                          hint: "How long the light takes to fade out when you choose Off.",
                          format: { $0 < 0.05 ? "At once" : String(format: "%.1f s", $0) })
        }
    }

    // MARK: state

    private func following(_ status: HubStatus?) -> String {
        guard let status else { return "" }
        if status.fading == true || status.mode == "off" { return "off" }
        return status.syncing ? "screen" : "glow"
    }

    private func followHint(_ status: HubStatus?, _ follow: String) -> String {
        if status?.fading == true { return "Fading out…" }
        switch follow {
        case "screen": return "The light follows your screen."
        case "glow": return "Glowing: " + (store.ambient?.glows.first { $0.name == store.glowName }?.label
                                            ?? "an effect or colour") + "."
        case "off": return "The lights are off."
        default: return ""
        }
    }

    /// The glow the Glow button brings back: the last one picked, else warm white.
    private var startingGlow: AmbientGlow? {
        let glows = store.ambient?.glows ?? []
        return glows.first { $0.name == store.glowName } ?? glows.first { $0.name == "warm-white" } ?? glows.first
    }

    private func matches(_ preset: AmbientPreset, _ status: HubStatus?) -> Bool {
        guard let status else { return false }
        return ambientMatches(preset) { status.presetValue($0) }
    }
}

extension HubStatus {
    /// A setting as presets name it, for telling which preset is in use.
    func presetValue(_ key: String) -> PresetValue? {
        switch key {
        case "bed_mode": return bedMode.map(PresetValue.text)
        case "bed_look": return bedLook.map(PresetValue.text)
        case "bed_side": return bedSide.map(PresetValue.text)
        default: return number(key).map(PresetValue.number)
        }
    }
}

/// A raised tile with a title and a line about it, lit amber while chosen, like
/// the page's tiles. A glow shows its colour along the top.
struct AmbientTile: View {
    let title: String
    let about: String
    let chosen: Bool
    var chip: Color?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                if let chip {
                    RoundedRectangle(cornerRadius: 1).fill(chip).frame(height: 5)
                }
                HStack(spacing: 7) {
                    Lamp(on: chosen, colour: Theme.amber).frame(width: 6, height: 6)
                    Text(title).caps(chosen ? Theme.amber : Theme.ink)
                }
                Text(about).hint().multilineTextAlignment(.leading)
            }
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: 74, alignment: .topLeading)
            .background(chosen ? Theme.panel : Theme.raised, in: RoundedRectangle(cornerRadius: 4))
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(chosen ? Theme.amber.opacity(0.5) : Theme.ruleHi))
        }
        .buttonStyle(.plain)
    }
}

/// The board's own brightness, which a glow runs at. Not a hub setting, so it has
/// its own slider; like SettingSlider, polls never yank it mid-drag.
struct BoardBrightnessSlider: View {
    @EnvironmentObject private var store: HubStore
    @State private var value = 128.0
    @State private var editing = false
    @State private var touched = Date.distantPast

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Glow brightness").caps(Theme.ink)
                Spacer()
                Text("\(Int((value / 2.55).rounded()))%").readout()
            }
            Slider(value: $value, in: 0...255) { active in
                editing = active
                touched = Date()
            }
            .tint(Theme.amber)
            .onChange(of: value) { _, new in
                guard editing || Date().timeIntervalSince(touched) < 0.5 else { return }
                touched = Date()
                store.sendBoardBrightness(new)
            }
        }
        .onAppear { if let bri = store.status?.wledBri { value = bri } }
        .onReceive(store.$status) { status in
            guard !editing, Date().timeIntervalSince(touched) > 2, let bri = status?.wledBri else { return }
            if abs(bri - value) > 0.5 { value = bri }
        }
    }
}
