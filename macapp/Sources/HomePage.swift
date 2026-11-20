import SwiftUI

/// Every light at once, like the phone page's Home: one power button, one brightness, one scene,
/// then each light's own options. Two columns when the window is wide.
struct HomePage: View {
    let go: (Page) -> Void

    var body: some View {
        GeometryReader { geo in
            ScrollView {
                if geo.size.width > 900 {
                    HStack(alignment: .top, spacing: 16) {
                        VStack(spacing: 16) { HeroCard(); SceneCard() }
                        VStack(spacing: 16) { MasterCard(); LightsCard(go: go) }
                    }
                    .padding(20)
                } else {
                    VStack(spacing: 16) { HeroCard(); MasterCard(); SceneCard(); LightsCard(go: go) }
                        .frame(maxWidth: 640).padding(20).frame(maxWidth: .infinity)
                }
            }
        }
    }
}

/// The power button for every light, what they show, and a live picture of the strip.
struct HeroCard: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        let status = store.status
        let scene = store.scene
        let view = heroView(online: store.online, boardReachable: status?.wledReachable, scene: scene,
                            colour: status?.wledCol, effect: store.effectName(status?.wledFx))
        let glow = glowColour(view.glow, scene: scene)
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 18) {
                PowerButton(on: scene != .off && store.online, glow: glow) { store.togglePower() }
                    .disabled(!store.online)
                VStack(alignment: .leading, spacing: 3) {
                    Text("All lights").font(.system(size: 12.5, weight: .medium)).foregroundStyle(Theme.dim)
                    Text(view.state).font(.system(size: 24, weight: .bold)).foregroundStyle(Theme.ink)
                        .lineLimit(2).minimumScaleFactor(0.8)
                    Text(view.hint).hint()
                }
                Spacer(minLength: 0)
                if scene == .screen, let status { meters(status) }
            }
            LEDFrame()
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background {
            ZStack {
                Theme.surface
                RadialGradient(colors: [glow.opacity(0.28), .clear], center: .topLeading, startRadius: 0, endRadius: 360)
            }
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        }
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).stroke(Theme.line))
        .animation(.easeOut(duration: 0.3), value: view)
    }

    private func meters(_ status: HubStatus) -> some View {
        HStack(spacing: 18) {
            if let fps = status.outputFps { meter("fps", String(format: "%.0f", fps)) }
            if let ma = status.safety?.powerMa { meter("amps", String(format: "%.2f", Double(ma) / 1000)) }
        }
    }

    private func meter(_ unit: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(unit).font(.system(size: 11.5)).foregroundStyle(Theme.dim)
            Text(value).font(.system(size: 17, weight: .semibold, design: .monospaced)).monospacedDigit()
                .foregroundStyle(Theme.ink)
        }
    }

    private func glowColour(_ glow: Glow, scene: LightScene) -> Color {
        switch glow {
        case .fault: return Theme.bad
        case .off: return .clear
        case .on: return Theme.ok
        case .effect: return scene == .colour ? Color(hex: store.status?.wledCol.map { "#" + $0 } ?? store.lastColour) : Theme.accent
        }
    }
}

struct PowerButton: View {
    let on: Bool
    let glow: Color
    var size: CGFloat = 76
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "power").font(.system(size: size * 0.36, weight: .semibold))
                .foregroundStyle(on ? Theme.accentInk : Theme.dim)
                .frame(width: size, height: size)
                .background(on ? Theme.accent : Theme.raised, in: Circle())
                .overlay(Circle().stroke(on ? .clear : Theme.lineHi))
                .shadow(color: on ? glow.opacity(0.7) : .clear, radius: 18)
                .contentShape(Circle())
        }
        .buttonStyle(PressScale())
        .accessibilityLabel("All lights")
        .accessibilityValue(on ? "On" : "Off")
        .help(on ? "Turn every light off" : "Start screen sync")
    }
}

struct PressScale: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.scaleEffect(configuration.isPressed ? 0.95 : 1)
            .animation(.easeOut(duration: 0.1), value: configuration.isPressed)
    }
}

/// The board's own brightness: every light, whatever it shows.
struct MasterCard: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        Card {
            Fader(label: "Brightness", range: 1...255, hint: "Every light on the board, whatever it shows.",
                  format: { "\(max(1, Int(($0 / 2.55).rounded())))%" },
                  read: { $0.wledBri.map(Double.init) },
                  write: { value, final in store.setMasterBrightness(value, final: final) })
        }
    }
}

/// What every light shows: the screen, a colour, or an effect. Each choice acts on the first click.
struct SceneCard: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        let scene = store.scene
        Card(title: "Scene") {
            Segmented(icons: [(LightScene.screen, "Screen", "display"), (.colour, "Colour", "paintpalette"),
                              (.effect, "Effect", "sparkles")], chosen: scene == .off ? nil : scene) { store.pick($0) }
            switch scene {
            case .colour: colours
            case .effect: effects
            default: styles
            }
        }
    }

    private var styles: some View {
        VStack(alignment: .leading, spacing: 14) {
            LookPicker()
            Divider()
            SoundControls(withCalm: true)
        }
    }

    private var colours: some View {
        let showing = store.scene == .colour ? store.status?.wledCol.map { "#" + $0 } ?? store.lastColour : store.lastColour
        return VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 4) {
                ForEach(swatches, id: \.hex) { swatch in
                    SwatchButton(hex: swatch.hex, name: swatch.name, chosen: showing == swatch.hex) { store.runColour(swatch.hex) }
                }
            }
            ColourWheel(hex: showing, label: "Colour for every light") { hex, final in store.runColour(hex, final: final) }
        }
    }

    private var effects: some View {
        let current = store.scene == .effect ? store.status?.wledFx : nil
        return VStack(alignment: .leading, spacing: 14) {
            if store.effects.isEmpty { Text("Loading effects…").hint() }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 8)], spacing: 8) {
                ForEach(store.effects, id: \.fx) { effect in
                    EffectTile(effect: effect, chosen: current == effect.fx) { store.runEffect(effect.fx) }
                }
            }
            if current != nil {
                LocalFader(label: "Speed", value: $store.effectSpeed) { store.retuneEffect() }
                LocalFader(label: "Intensity", value: $store.effectIntensity) { store.retuneEffect() }
            }
        }
    }
}

struct EffectTile: View {
    let effect: Effect
    let chosen: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 2) {
                Text(effect.name).font(.system(size: 13.5, weight: .semibold)).foregroundStyle(Theme.ink)
                if let about = effect.about { Text(about).font(.system(size: 11.5)).foregroundStyle(Theme.dim).lineLimit(2) }
            }
            .frame(maxWidth: .infinity, minHeight: 56, alignment: .topLeading)
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(Theme.raised, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(chosen ? Theme.accent : Theme.line,
                                                                                    lineWidth: chosen ? 2 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }
}

/// A 0–255 slider kept on this Mac, such as an effect's speed.
struct LocalFader: View {
    let label: String
    @Binding var value: Double
    let changed: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.ink)
                Spacer()
                Text("\(Int((value / 2.55).rounded()))%").readout()
            }
            Slider(value: $value, in: 0...255).tint(Theme.accent).onChange(of: value) { _, _ in changed() }
        }
    }
}

/// Each light on its own: the monitor strip, the bed and the mouse.
struct LightsCard: View {
    @EnvironmentObject private var store: HubStore
    let go: (Page) -> Void

    var body: some View {
        Card(title: "Lights") {
            VStack(spacing: 0) {
                monitor
                Divider().overlay(Theme.line).padding(.vertical, 12)
                BedRow(go: go)
                Divider().overlay(Theme.line).padding(.vertical, 12)
                MouseRow()
            }
        }
    }

    private var monitor: some View {
        let status = store.status
        let parts = store.parts.count
        let detail = status.map { "\($0.ledCount) LEDs" + (parts > 0 ? " · \(parts) part\(parts == 1 ? "" : "s") set" : "") }
        return LightRow(icon: "display", title: "Monitor strip", detail: detail ?? "",
                        lit: store.scene != .off && status?.wledReachable != false) {
            Button("Parts") { go(.monitor) }.help("Give part of the strip its own colour, a lamp, or switch it off")
        }
    }
}

/// An icon, a name and a line of state, with room for a control on the right.
struct LightRow<Trailing: View>: View {
    let icon: String
    let title: String
    let detail: String
    let lit: Bool
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon).font(.system(size: 15, weight: .medium))
                .foregroundStyle(lit ? Theme.accent : Theme.dim)
                .frame(width: 38, height: 38)
                .background(Theme.raised, in: RoundedRectangle(cornerRadius: 11, style: .continuous))
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink)
                Text(detail).font(.system(size: 12)).foregroundStyle(Theme.dim).lineLimit(2)
            }
            Spacer(minLength: 8)
            trailing
        }
    }
}

struct BedRow: View {
    @EnvironmentObject private var store: HubStore
    let go: (Page) -> Void

    private static let modes = [("screen", "Match"), ("colour", "Colour"), ("effect", "Effect"), ("off", "Off")]
    private static let looks = [("blend", "Blend"), ("mood", "Mood"), ("side", "Edge")]
    private static let sides = [("whole", "All round"), ("bottom", "Bottom"), ("right", "Right"), ("top", "Top"), ("left", "Left")]

    var body: some View {
        let status = store.status
        let leds = status?.bedLeds ?? 0
        let mode = status?.bedMode ?? "screen"
        let view = bedView(leds: leds, mode: mode, look: status?.bedLook ?? "", effect: store.effectName(status?.bedEffect),
                           scene: store.scene)
        VStack(alignment: .leading, spacing: 12) {
            LightRow(icon: "bed.double", title: "Bed strip", detail: view.state, lit: view.lit) {
                if leds == 0 { Button("Set up") { go(.bed) } }
            }
            if leds > 0 {
                Segmented(Self.modes, chosen: mode) { store.set("bed_mode", $0) }
                if mode == "screen" && store.scene == .screen {
                    Segmented(Self.looks, chosen: status?.bedLook) { store.set("bed_look", $0) }
                    if status?.bedLook == "side" { Segmented(Self.sides, chosen: status?.bedSide) { store.set("bed_side", $0) } }
                }
                if !view.hint.isEmpty { Text(view.hint).hint() }
                if mode == "colour" { colour(status) }
                if mode == "effect" { effect(status) }
                if view.brightness { settingFader("bed_brightness", "Bed brightness", store: store) }
            }
        }
    }

    private func colour(_ status: HubStatus?) -> some View {
        let current = "#" + (status?.bedColour ?? "ffb060").replacingOccurrences(of: "#", with: "").lowercased()
        return VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 4) {
                ForEach(swatches, id: \.hex) { swatch in
                    SwatchButton(hex: swatch.hex, name: swatch.name, chosen: current == swatch.hex, size: 24) {
                        store.set("bed_colour", String(swatch.hex.dropFirst()))
                    }
                }
            }
            ColourWheel(hex: current, label: "Bed colour") { hex, final in
                if final { store.set("bed_colour", String(hex.dropFirst())) }
            }
        }
    }

    private func effect(_ status: HubStatus?) -> some View {
        let current = status?.bedEffect ?? 9
        let choices = store.effects.contains { $0.fx == current } ? store.effects
            : store.effects + [Effect(fx: current, name: "Effect \(current)")]
        return VStack(alignment: .leading, spacing: 12) {
            Picker("Effect", selection: Binding(get: { current }, set: { store.set("bed_effect", String($0)) })) {
                ForEach(choices, id: \.fx) { Text($0.name).tag($0.fx) }
            }
            settingFader("bed_speed", "Speed", range: 0...255, format: { "\(Int(($0 / 2.55).rounded()))%" }, store: store)
        }
    }
}

struct MouseRow: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        let status = store.status
        let mouse = status?.mouse
        VStack(alignment: .leading, spacing: 10) {
            LightRow(icon: "computermouse", title: "Mouse follows the screen", detail: detail(mouse),
                     lit: mouse?.connected == true && status?.mouseSync == true) {
                Toggle("Mouse follows the screen", isOn: Binding(get: { status?.mouseSync ?? false },
                                                                 set: { store.set("mouse_sync", $0 ? "1" : "0") }))
                    .toggleStyle(.switch).tint(Theme.accent).labelsHidden()
                    .disabled(mouse?.connected != true)
            }
            if let permission = mouse?.permission, mouse?.connected == true, permission != "granted" {
                Button("Open Input Monitoring settings") {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent") {
                        NSWorkspace.shared.open(url)
                    }
                }
            }
        }
    }

    private func detail(_ mouse: MouseInfo?) -> String {
        guard let mouse, mouse.connected else { return "No Logitech LIGHTSYNC mouse (G102/G203) is plugged in." }
        if let permission = mouse.permission, permission != "granted" {
            return "macOS needs Input Monitoring for Python, which runs the hub."
        }
        return "\(mouse.name ?? "The mouse"): takes the colours along the bottom of the screen while it syncs."
    }
}

/// Lays chips out left to right, wrapping onto new rows.
struct FlowRow: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(subviews, width: proposal.width ?? .infinity)
        return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0, height: rows.last.map { $0.y + $0.height } ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for row in arrange(subviews, width: bounds.width) {
            var x = bounds.minX
            for index in row.items {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: bounds.minY + row.y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
        }
    }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> [(items: [Int], y: CGFloat, width: CGFloat, height: CGFloat)] {
        var rows: [(items: [Int], y: CGFloat, width: CGFloat, height: CGFloat)] = []
        var items: [Int] = [], x: CGFloat = 0, y: CGFloat = 0, height: CGFloat = 0
        for (index, view) in subviews.enumerated() {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width {
                rows.append((items, y, x - spacing, height))
                y += height + spacing
                items = []; x = 0; height = 0
            }
            items.append(index)
            x += size.width + spacing
            height = max(height, size.height)
        }
        if !items.isEmpty { rows.append((items, y, x - spacing, height)) }
        return rows
    }
}
