import SwiftUI

/// The quick controls behind the menu-bar icon: the same power, brightness and mode as Home, and under the
/// chosen mode only what that mode needs: looks and the sound for Screen, colours, or effects.
struct MenuBarPanel: View {
    @EnvironmentObject private var store: HubStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        let status = store.status
        let scene = store.scene
        let view = heroView(online: store.online, boardReachable: status?.wledReachable, scene: scene,
                            colour: status?.wledCol, effect: store.effectName(status?.wledFx))
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 14) {
                PowerButton(on: scene != .off && store.online, glow: Theme.accent, size: 46) { store.togglePower() }
                    .disabled(!store.online)
                VStack(alignment: .leading, spacing: 1) {
                    Text("All lights").font(.system(size: 11.5)).foregroundStyle(Theme.dim)
                    Text(view.state).font(.system(size: 16, weight: .bold)).foregroundStyle(Theme.ink).lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            Fader(label: "Brightness", range: 1...255, format: { "\(max(1, Int(($0 / 2.55).rounded())))%" },
                  read: { $0.wledBri.map(Double.init) },
                  write: { value, final in store.setMasterBrightness(value, final: final) })
            VStack(alignment: .leading, spacing: 8) {
                Segmented(icons: [(LightScene.screen, "Screen", "display"), (.colour, "Colour", "paintpalette"),
                                  (.effect, "Effect", "sparkles")], chosen: scene == .off ? nil : scene) { store.pick($0) }
                Text(MenuBarPanel.caption(scene)).hint()
            }
            modeBody(scene)
            if status?.mouse?.connected == true {
                Divider()
                SwitchRow(title: "Mouse follows the screen", isOn: status?.mouseSync ?? false) {
                    store.set("mouse_sync", $0 ? "1" : "0")
                }
            }
            Divider()
            HStack {
                Button("Open PudiSync") {
                    openWindow(id: "main")
                    NSApp.activate(ignoringOtherApps: true)
                }
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
            }
        }
        .padding(16)
        .frame(width: 340)
        .background(Theme.bg)
        .tint(Theme.accent)
        .onAppear { store.loadEffects() }
        // The menu bar panel can open before the main window ever does.
        .onAppear { Appearance.apply(UserDefaults.standard.string(forKey: Appearance.key) ?? "") }
    }

    static func caption(_ scene: LightScene) -> String {
        switch scene {
        case .screen: return "The lights follow what is on your display."
        case .colour: return "One colour on every light."
        case .effect: return "A moving pattern, run by the board."
        case .off: return "Everything is off. Pick a mode to start."
        }
    }

    @ViewBuilder private func modeBody(_ scene: LightScene) -> some View {
        switch scene {
        case .screen:
            VStack(alignment: .leading, spacing: 14) {
                LookPicker()
                Divider()
                SoundControls()
            }
        case .colour:
            HStack(spacing: 2) {
                ForEach(swatches, id: \.hex) { swatch in
                    SwatchButton(hex: swatch.hex, name: swatch.name,
                                 chosen: "#" + (store.status?.wledCol ?? "") == swatch.hex, size: 24) { store.runColour(swatch.hex) }
                }
            }
        case .effect:
            VStack(alignment: .leading, spacing: 10) {
                if store.effects.isEmpty { Text("Loading effects…").hint() }
                FlowRow(spacing: 6) {
                    ForEach(store.effects, id: \.fx) { effect in
                        Chip(title: effect.name, chosen: store.status?.wledFx == effect.fx) { store.runEffect(effect.fx) }
                    }
                }
                LocalFader(label: "Speed", value: $store.effectSpeed) { store.retuneEffect() }
                LocalFader(label: "Intensity", value: $store.effectIntensity) { store.retuneEffect() }
            }
        case .off:
            EmptyView()
        }
    }
}
