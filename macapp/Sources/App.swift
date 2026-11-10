import SwiftUI

@main
struct AmbilightHubApp: App {
    @StateObject private var store = HubStore()

    var body: some Scene {
        Window("Ambilight Hub", id: "main") {
            ContentView()
                .environmentObject(store)
                .frame(minWidth: 960, minHeight: 640)
                .preferredColorScheme(.dark)
        }
        .defaultSize(width: 1120, height: 780)
        .commands { LightingCommands(store: store) }

        MenuBarExtra {
            MenuBarPanel().environmentObject(store).preferredColorScheme(.dark)
        } label: {
            Image(systemName: store.status?.syncing == true ? "light.max" : "light.min")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView().environmentObject(store).preferredColorScheme(.dark)
        }
    }
}

enum Page: Hashable {
    case strip, mouse, bed, profiles, calibrate
}

struct ContentView: View {
    @EnvironmentObject private var store: HubStore
    @State private var page: Page? = .strip

    var body: some View {
        NavigationSplitView {
            List(selection: $page) {
                Section("Devices") {
                    DeviceRow(title: "LED strip", detail: stripDetail, symbol: "tv", lit: stripLit,
                              alert: store.status?.wledReachable == false).tag(Page.strip)
                    DeviceRow(title: store.status?.mouse?.name.map(shortMouseName) ?? "Mouse", detail: mouseDetail,
                              symbol: "computermouse", lit: store.status?.mouse?.connected == true && store.status?.mouseSync == true,
                              alert: false).tag(Page.mouse)
                    DeviceRow(title: "Bed strip", detail: bedDetail, symbol: "bed.double",
                              lit: (store.status?.bedLeds ?? 0) > 0 && store.status?.bedMode != "off",
                              alert: false).tag(Page.bed)
                }
                Section("Lighting") {
                    Label("Profiles", systemImage: "square.stack").tag(Page.profiles)
                    Label("Calibrate", systemImage: "camera.viewfinder").tag(Page.calibrate)
                }
            }
            .navigationSplitViewColumnWidth(min: 220, ideal: 240)
        } detail: {
            VStack(spacing: 0) {
                Header(page: page ?? .strip)
                if let text = store.link.banner(address: store.address, now: Date()) ?? store.problem {
                    Banner(text: text, fault: store.link != .online)
                }
                switch page ?? .strip {
                case .strip: StripPage()
                case .mouse: MousePage()
                case .bed: BedPage()
                case .profiles: ProfilesPage()
                case .calibrate: CalibratePage()
                }
            }
            .background(Theme.bg)
        }
        .sheet(isPresented: $store.showingPair) { PairSheet().environmentObject(store) }
    }

    private var stripLit: Bool { store.status?.syncing == true && store.status?.wledReachable != false }

    private var stripDetail: String {
        guard store.online, let status = store.status else { return "Hub offline" }
        if status.wledReachable == false { return "Controller not answering" }
        return status.syncing ? "Syncing · \(status.wledIp)" : "Standby · \(status.wledIp)"
    }

    private var mouseDetail: String {
        guard let mouse = store.status?.mouse, mouse.connected else { return "Not plugged in" }
        if let permission = mouse.permission, permission != "granted" { return "Needs Input Monitoring" }
        return store.status?.mouseSync == true ? "Following the screen" : "Own lighting"
    }

    private var bedDetail: String {
        guard let status = store.status, let count = status.bedLeds, count > 0 else { return "Not set up" }
        let what = ["screen": "Follows the screen", "effect": "Effect", "colour": "Colour", "off": "Off"]
        return "\(count) LEDs · \(what[status.bedMode ?? "screen"] ?? "")"
    }

    private func shortMouseName(_ name: String) -> String {
        name.replacingOccurrences(of: " Gaming Mouse", with: "")
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
            Image(systemName: symbol).frame(width: 22).foregroundStyle(lit ? Theme.amber : Theme.dim)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(detail).font(.system(size: 11)).foregroundStyle(alert ? Theme.red : Theme.dim)
            }
            Spacer()
            Lamp(on: lit || alert, colour: alert ? Theme.red : Theme.green)
        }
        .padding(.vertical, 3)
    }
}

struct Header: View {
    @EnvironmentObject private var store: HubStore
    let page: Page

    var body: some View {
        let status = store.status
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 22, weight: .bold)).foregroundStyle(Theme.ink)
                HStack(spacing: 14) {
                    Label(status?.syncing == true ? "Syncing" : "Standby", systemImage: "dot.radiowaves.left.and.right")
                    if let fps = status?.outputFps, status?.syncing == true { Text(String(format: "%.0f fps", fps)) }
                    if let ma = status?.safety?.powerMa { Text(String(format: "%.2f A", Double(ma) / 1000)) }
                    if let profile = status?.profile, !profile.isEmpty { Text("Profile: \(profileTitle(profile))") }
                }
                .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.dim)
            }
            Spacer()
            Text("Screen sync").caps(Theme.ink)
            Toggle("Screen sync", isOn: Binding(get: { status?.syncing ?? false }, set: { store.setSync($0) }))
                .toggleStyle(.switch).tint(Theme.green).labelsHidden().controlSize(.large)
        }
        .padding(.horizontal, 20).padding(.vertical, 16)
        .background(Theme.panel)
        .overlay(alignment: .bottom) { Rectangle().fill(Theme.rule).frame(height: 1) }
    }

    private var title: String {
        switch page {
        case .strip: return "LED strip"
        case .mouse: return store.status?.mouse?.name ?? "Mouse"
        case .bed: return "Bed strip"
        case .profiles: return "Profiles"
        case .calibrate: return "Calibrate"
        }
    }

    private func profileTitle(_ name: String) -> String {
        HubStore.builtinProfiles.first { $0.0 == name }?.1 ?? name
    }
}

struct Banner: View {
    let text: String
    let fault: Bool

    var body: some View {
        HStack {
            Image(systemName: fault ? "exclamationmark.triangle.fill" : "info.circle").foregroundStyle(fault ? Theme.red : Theme.amber)
            Text(text).font(.system(size: 13)).foregroundStyle(Theme.ink)
            Spacer()
        }
        .padding(12)
        .background(Theme.raised)
    }
}

/// The quick controls behind the menu-bar icon.
struct MenuBarPanel: View {
    @EnvironmentObject private var store: HubStore
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        let status = store.status
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Lamp(on: status?.syncing == true)
                Text(store.online ? (status?.syncing == true ? "Syncing" : "Standby") : "Hub offline").caps(Theme.ink)
                Spacer()
                Toggle("Screen sync", isOn: Binding(get: { status?.syncing ?? false }, set: { store.setSync($0) }))
                    .toggleStyle(.switch).tint(Theme.green).labelsHidden()
            }
            ProfileChoices()
            SettingSlider(key: "brightness", label: "Brightness")
            SettingSlider(key: "immersion", label: "Immersion",
                          format: { $0 < 0.005 ? "Off" : "\(Int(($0 * 100).rounded()))%" })
            if status?.mouse?.connected == true {
                Toggle(isOn: Binding(get: { status?.mouseSync ?? false }, set: { store.set("mouse_sync", $0 ? "1" : "0") })) {
                    Text("Mouse follows the screen").caps(Theme.ink)
                }
                .toggleStyle(.switch).tint(Theme.green)
            }
            if !store.locks.isEmpty {
                KeyButton(title: "Unlock \(store.locks.count) locked colour\(store.locks.count == 1 ? "" : "s")") {
                    store.unlockAll()
                }
            }
            Divider()
            HStack {
                KeyButton(title: "Open Ambilight Hub") {
                    openWindow(id: "main")
                    NSApp.activate(ignoringOtherApps: true)
                }
                Spacer()
                KeyButton(title: "Quit") { NSApp.terminate(nil) }
            }
        }
        .padding(16)
        .frame(width: 320)
        .background(Theme.panel)
    }
}

struct LightingCommands: Commands {
    @ObservedObject var store: HubStore

    var body: some Commands {
        CommandMenu("Lighting") {
            Button(store.status?.syncing == true ? "Stop Screen Sync" : "Start Screen Sync") {
                store.setSync(store.status?.syncing != true)
            }
            .keyboardShortcut("s", modifiers: [.command, .shift])
            Divider()
            ForEach(Array(HubStore.builtinProfiles.enumerated()), id: \.offset) { index, profile in
                Button(profile.1) { store.apply(profile: profile.0) }
                    .keyboardShortcut(KeyEquivalent(Character("\(index + 1)")), modifiers: .command)
            }
            ForEach(Array((store.status?.myProfiles ?? []).prefix(7).enumerated()), id: \.offset) { index, name in
                Button(name) { store.apply(profile: name) }
                    .keyboardShortcut(KeyEquivalent(Character("\(index + 3)")), modifiers: .command)
            }
            Divider()
            Button(store.status?.mouseSync == true ? "Mouse: Own Lighting" : "Mouse: Follow the Screen") {
                store.set("mouse_sync", store.status?.mouseSync == true ? "0" : "1")
            }
            .keyboardShortcut("m", modifiers: [.command, .shift])
            Button("Unlock All Colours") { store.unlockAll() }
                .disabled(store.locks.isEmpty)
            Divider()
            Button("Pair a Phone or iPad…") { store.showingPair = true }
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: HubStore
    @State private var address = ""
    @State private var pin = ""
    @State private var pairing = false

    var body: some View {
        Form {
            TextField("Hub address", text: $address, prompt: Text("127.0.0.1:8080"))
            SecureField("PIN (if the hub has one)", text: $pin)
            Text("The hub runs on this Mac and starts at login. Use another address only for a hub on another computer.")
                .font(.system(size: 12)).foregroundStyle(.secondary)
            HStack {
                Button("Pair a Phone or iPad…") { pairing = true }
                Spacer()
                Button("Save") {
                    store.address = address.trimmingCharacters(in: .whitespaces)
                    store.pin = pin
                    Task { await store.refresh() }
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 420)
        .sheet(isPresented: $pairing) { PairSheet().environmentObject(store) }
        .onAppear {
            address = store.address
            pin = store.pin
        }
    }
}
