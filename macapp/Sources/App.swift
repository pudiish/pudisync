import SwiftUI

@main
struct PudiSyncApp: App {
    @StateObject private var store = HubStore()

    var body: some Scene {
        Window("PudiSync", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 820, minHeight: 600)
        }
        .defaultSize(width: 1180, height: 820)
        .commands { LightingCommands(store: store) }

        MenuBarExtra {
            MenuBarPanel().environmentObject(store)
        } label: {
            Image(systemName: store.scene == .off ? "light.min" : "light.max")
        }
        .menuBarExtraStyle(.window)

        Settings {
            ConnectionCard().environmentObject(store).padding(20).frame(width: 440).background(Theme.bg)
                .sheet(isPresented: $store.showingPair) { PairSheet().environmentObject(store) }
        }
    }
}

/// The phone page's seven sections, as its rail shows them on a wide screen.
enum Page: Hashable {
    case overview, monitor, bed, mouse, looks, calibration, setup

    var title: String {
        switch self {
        case .overview: return "Overview"
        case .monitor: return "Monitor strip"
        case .bed: return "Bed strip"
        case .mouse: return "Mouse"
        case .looks: return "Looks"
        case .calibration: return "Calibration"
        case .setup: return "Setup"
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var store: HubStore
    @State private var page: Page? = .overview

    var body: some View {
        NavigationSplitView {
            List(selection: $page) {
                Label("Overview", systemImage: "lightbulb").tag(Page.overview)
                // Each light's own section, with its state at a glance.
                DeviceRow(title: "Monitor strip", detail: stripDetail, symbol: "display", lit: stripLit,
                          alert: store.status?.wledReachable == false).tag(Page.monitor)
                DeviceRow(title: "Bed strip", detail: bedDetail, symbol: "bed.double", lit: bedLit, alert: false)
                    .tag(Page.bed)
                DeviceRow(title: "Mouse", detail: mouseDetail, symbol: "computermouse",
                          lit: store.status?.mouse?.connected == true && store.status?.mouseSync == true, alert: false)
                    .tag(Page.mouse)
                Label("Looks", systemImage: "square.stack.3d.up").tag(Page.looks)
                Label("Calibration", systemImage: "scope").tag(Page.calibration)
                Label("Setup", systemImage: "gearshape").tag(Page.setup)
            }
            .navigationSplitViewColumnWidth(min: 210, ideal: 230)
            .safeAreaInset(edge: .bottom) { brand }
        } detail: {
            VStack(spacing: 0) {
                if let text = store.link.banner(address: store.address, now: Date()) ?? store.problem {
                    Banner(text: text, fault: store.link != .online)
                }
                if let error = store.status?.lastError, store.online { Banner(text: error, fault: true) }
                switch page ?? .overview {
                case .overview: HomePage { page = $0 }
                case .monitor: CardsPage { HeroCard(); PictureCard(); PartsCard() }
                case .bed: CardsPage { Card(title: "Bed strip") { BedRow { page = $0 } }; BedWiringCard() }
                case .mouse: CardsPage { Card { MouseRow() } }
                case .looks: CardsPage { LooksCard() }
                case .calibration: CardsPage { FineColourCard(); CalibrateCard() }
                case .setup: CardsPage { ConnectionCard(); AboutCard() }
                }
            }
            .background(Theme.bg)
            .navigationTitle((page ?? .overview).title)
            .toolbar {
                ToolbarItem(placement: .primaryAction) { StatusPill() }
            }
        }
        .tint(Theme.accent)
        .sheet(isPresented: $store.showingPair) { PairSheet().environmentObject(store) }
    }

    private var brand: some View {
        HStack(spacing: 0) {
            Text("Pudi").foregroundStyle(Theme.ink)
            Text("Sync").foregroundStyle(Theme.accent)
        }
        .font(.system(size: 15, weight: .bold))
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18).padding(.vertical, 14)
    }

    private var stripLit: Bool { store.scene != .off && store.status?.wledReachable != false }

    private var stripDetail: String {
        guard store.online, let status = store.status else { return "Hub offline" }
        if status.wledReachable == false { return "Board not answering" }
        switch store.scene {
        case .screen: return "Following the screen"
        case .colour: return colourName("#" + (status.wledCol ?? "ffffff"))
        case .effect: return store.effectName(status.wledFx)
        case .off: return "Off"
        }
    }

    private var bedLit: Bool {
        guard let status = store.status, let leds = status.bedLeds else { return false }
        return bedView(leds: leds, mode: status.bedMode ?? "screen", look: status.bedLook ?? "", effect: "",
                       scene: store.scene).lit
    }

    private var bedDetail: String {
        guard let status = store.status, let leds = status.bedLeds, leds > 0 else { return "Not set up" }
        return ["screen": "Matches the scene", "colour": "Its own colour", "effect": "Its own effect", "off": "Off"][
            status.bedMode ?? "screen"] ?? ""
    }

    private var mouseDetail: String {
        guard let mouse = store.status?.mouse, mouse.connected else { return "Not plugged in" }
        if let permission = mouse.permission, permission != "granted" { return "Needs Input Monitoring" }
        return store.status?.mouseSync == true ? "Follows the screen" : "Its own lighting"
    }
}

/// What the lights are doing, in the window's toolbar.
struct StatusPill: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        let scene = store.scene
        HStack(spacing: 7) {
            Lamp(on: store.online && scene != .off, colour: store.status?.wledReachable == false ? Theme.bad
                 : scene == .screen ? Theme.ok : Theme.accent)
            Text(words(scene)).font(.system(size: 12.5, weight: .medium)).foregroundStyle(Theme.ink)
        }
        .padding(.horizontal, 10)
    }

    private func words(_ scene: LightScene) -> String {
        guard store.online else { return "Hub offline" }
        switch scene {
        case .screen:
            return store.status?.outputFps.map { String(format: "Syncing · %.0f fps", $0) } ?? "Syncing"
        case .colour: return "Colour"
        case .effect: return "Effect"
        case .off: return "Off"
        }
    }
}

struct DeviceRow: View {
    let title: String
    let detail: String
    let symbol: String
    let lit: Bool
    let alert: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbol).frame(width: 22).foregroundStyle(lit ? Theme.accent : Theme.dim)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(detail).font(.system(size: 11)).foregroundStyle(alert ? Theme.bad : Theme.dim).lineLimit(1)
            }
            Spacer()
            Lamp(on: lit || alert, colour: alert ? Theme.bad : Theme.ok)
        }
        .padding(.vertical, 3)
    }
}

struct Banner: View {
    let text: String
    let fault: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: fault ? "exclamationmark.triangle.fill" : "info.circle")
                .foregroundStyle(fault ? Theme.bad : Theme.accent)
            Text(text).font(.system(size: 13)).foregroundStyle(Theme.ink)
            Spacer()
        }
        .padding(12)
        .background(Theme.surface)
        .overlay(alignment: .leading) { Rectangle().fill(fault ? Theme.bad : Theme.accent).frame(width: 3) }
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.line).frame(height: 1) }
    }
}

/// The quick controls behind the menu-bar icon: the same power, brightness and scene as Home.
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
            Segmented(icons: [(LightScene.screen, "Screen", "display"), (.colour, "Colour", "paintpalette"),
                              (.effect, "Effect", "sparkles")], chosen: scene == .off ? nil : scene) { store.pick($0) }
            if scene == .screen {
                HStack(spacing: 6) {
                    ForEach(HubStore.builtinProfiles, id: \.0) { name, title in
                        Chip(title: title, chosen: status?.profile == name) { store.pickStyle(name) }
                    }
                }
            }
            if scene == .colour {
                HStack(spacing: 2) {
                    ForEach(swatches, id: \.hex) { swatch in
                        SwatchButton(hex: swatch.hex, name: swatch.name, chosen: "#" + (status?.wledCol ?? "") == swatch.hex,
                                     size: 24) { store.runColour(swatch.hex) }
                    }
                }
            }
            if status?.mouse?.connected == true {
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
        .frame(width: 320)
        .background(Theme.bg)
        .tint(Theme.accent)
    }
}

struct LightingCommands: Commands {
    @ObservedObject var store: HubStore

    var body: some Commands {
        CommandMenu("Lights") {
            Button(store.scene == .off ? "Turn Lights On" : "Turn Lights Off") { store.togglePower() }
                .keyboardShortcut("l", modifiers: [.command, .shift])
            Button("Follow the Screen") { store.startSync() }
                .keyboardShortcut("s", modifiers: [.command, .shift])
            Divider()
            ForEach(Array(HubStore.builtinProfiles.enumerated()), id: \.offset) { index, profile in
                Button(profile.1) { store.pickStyle(profile.0) }
                    .keyboardShortcut(KeyEquivalent(Character("\(index + 1)")), modifiers: .command)
            }
            // Your looks take the digits after the built-in styles, up to ⌘9.
            ForEach(Array((store.status?.myProfiles ?? []).prefix(9 - HubStore.builtinProfiles.count).enumerated()),
                    id: \.offset) { index, name in
                Button(name) { store.pickStyle(name) }
                    .keyboardShortcut(KeyEquivalent(Character("\(index + HubStore.builtinProfiles.count + 1)")),
                                      modifiers: .command)
            }
            Divider()
            Button(store.status?.mouseSync == true ? "Mouse: Its Own Lighting" : "Mouse: Follow the Screen") {
                store.set("mouse_sync", store.status?.mouseSync == true ? "0" : "1")
            }
            .keyboardShortcut("m", modifiers: [.command, .shift])
            Button("Clear Parts of the Strip") { store.setParts([]) }
                .disabled(store.parts.isEmpty)
            Divider()
            Button("Add a Phone or iPad…") { store.showingPair = true }
        }
    }
}
