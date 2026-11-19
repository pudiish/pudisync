import SwiftUI

/// The hub's address and PIN; also shown under PudiSync ▸ Settings.
struct ConnectionCard: View {
    @EnvironmentObject private var store: HubStore
    @State private var address = ""
    @State private var pin = ""

    var body: some View {
        Card(title: "Hub") {
            HStack(spacing: 8) {
                Lamp(on: store.online)
                Text(store.online ? "Connected to \(store.address)" : "Not connected to \(store.address)")
                    .font(.system(size: 13)).foregroundStyle(Theme.ink)
            }
            TextField("Hub address", text: $address, prompt: Text("127.0.0.1:8080")).textFieldStyle(.roundedBorder)
            SecureField("PIN, if the hub has one", text: $pin).textFieldStyle(.roundedBorder)
            Text("The hub runs on this Mac and starts at login. Use another address only for a hub on another computer.").hint()
            HStack {
                Button("Add a phone or iPad…") { store.showingPair = true }
                Spacer()
                Button("Save") {
                    store.address = address.trimmingCharacters(in: .whitespaces)
                    store.pin = pin
                    Task { await store.refresh() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .onAppear {
            address = store.address
            pin = store.pin
        }
    }
}

struct BedWiringCard: View {
    @EnvironmentObject private var store: HubStore
    @State private var leds = ""
    @State private var amps = ""
    @State private var pin = 5

    static let pins = [("TX", 1), ("D1", 5), ("D2", 4), ("D5", 14), ("D6", 12), ("D7", 13), ("RX", 3)]

    var body: some View {
        let status = store.status
        Card(title: "Bed strip") {
            Text("Wire it as in docs/wiring.md, then tell the board how many LEDs it has and where it plugs in.").hint()
            HStack(alignment: .bottom, spacing: 12) {
                field("LEDs") { TextField("LEDs", text: $leds).textFieldStyle(.roundedBorder).labelsHidden() }
                field("Data pin") {
                    Picker("Data pin", selection: $pin) { ForEach(Self.pins, id: \.1) { Text($0.0).tag($0.1) } }
                        .labelsHidden().fixedSize()
                }
                field("Power limit (A)") { TextField("Amps", text: $amps).textFieldStyle(.roundedBorder).labelsHidden() }
            }
            Text("The amps are the bed's share of the adapter both strips may be on. The board restarts for about "
                 + "10 seconds. TX suits a long strip; RX leaves the board the least memory, so keep strips there short.").hint()
            Button("Apply and restart the board") { store.setupBed(leds: leds, amps: amps, pin: pin) }
                .disabled(Int(leds) == nil || Double(amps) == nil)
            SwitchRow(title: "First LED is the far end", detail: "Leave off when the strip starts next to the monitor.",
                      isOn: status?.bedReverse ?? false) { store.set("bed_reverse", $0 ? "1" : "0") }
        }
        .onAppear(perform: fillForm)
        .onChange(of: store.status?.bedLeds) { _, _ in fillForm() }
    }

    private func field<Content: View>(_ label: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.dim)
            content()
        }
    }

    private func fillForm() {
        guard let status = store.status else { return }
        leds = String(status.bedLeds ?? 0)
        amps = String(format: "%.1f", Double(status.bedPowerMa ?? 2000) / 1000)
        pin = status.bedPin ?? 5
    }
}

struct CalibrateCard: View {
    @EnvironmentObject private var store: HubStore
    @State private var chosen = ""

    var body: some View {
        let job = store.camera
        let usable = store.cameras.filter { !$0.builtin }
        let working = job?.state == "working"
        Card(title: "Webcam calibration") {
            Text("Aim a USB webcam at the monitor and the glow around it. For about a minute the monitor shows test colours "
                 + "while the strip is matched to them. The Mac's built-in camera is never used.").hint()
            Picker("Webcam", selection: $chosen) {
                if chosen.isEmpty { Text("No webcam plugged in").tag("") }
                ForEach(usable, id: \.name) { Text($0.name).tag($0.name) }
                ForEach(store.cameras.filter(\.builtin), id: \.name) {
                    Text("\($0.name) — built-in, never used").tag("builtin:\($0.name)")
                }
            }
            .onChange(of: chosen) { _, new in if new.hasPrefix("builtin:") { chosen = preferredWebcam(store.cameras) } }
            HStack {
                Group {
                    Button("Calibrate") { store.startCamera(task: "calibrate", camera: chosen) }
                    Button("Check during a video") { store.startCamera(task: "check", camera: chosen) }
                }
                .disabled(working || chosen.isEmpty)
                Spacer()
                Button("Refresh list") { store.loadCameras() }.disabled(working)
            }
            if let job, job.state != "idle" {
                Text(stateLine(job)).font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(job.state == "error" ? Theme.bad : job.state == "done" ? Theme.ok : Theme.accent)
                ScrollView {
                    Text(job.log.joined(separator: "\n"))
                        .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.dim)
                        .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                }
                .frame(maxHeight: 180)
                .padding(10)
                .background(Theme.well, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
        }
        .onAppear { store.loadCameras() }
        .onChange(of: store.cameras) { _, cameras in
            if chosen.isEmpty || !cameras.contains(where: { $0.name == chosen && !$0.builtin }) {
                chosen = preferredWebcam(cameras)
            }
        }
    }

    /// A phone's camera only when picked by hand, as the hub's own default (camrun.pick_camera).
    private func preferredWebcam(_ cameras: [Camera]) -> String {
        cameras.first { !$0.builtin && !$0.name.localizedCaseInsensitiveContains("iphone") }?.name ?? ""
    }

    private func stateLine(_ job: CameraJob) -> String {
        switch job.state {
        case "working": return job.task == "check" ? "Checking — play a video…" : "Calibrating — keep the webcam still…"
        case "done": return job.task == "check" ? "Check finished" : "Calibration finished"
        default: return "Stopped"
        }
    }
}

struct AboutCard: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        Card(title: "More") {
            Text("Strip setup, screen capture, the PIN and diagnostics are on the phone page's Settings tab.").hint()
            HStack {
                Button("Open the phone page") { open("http://\(store.address)/#settings") }
                if let board = store.status?.boardUrls?.first { Button("Open the board's own page") { open(board) } }
                Spacer()
            }
        }
    }

    private func open(_ link: String) {
        if let url = URL(string: link) { NSWorkspace.shared.open(url) }
    }
}
