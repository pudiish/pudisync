import SwiftUI

struct MousePage: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        let status = store.status
        let mouse = status?.mouse
        let zones = status.map {
            mouseZones(preview: $0.preview, ledCount: $0.ledCount, bottomEdge: $0.edgeBottom, frame: $0.layout == "frame")
        } ?? []
        VStack(spacing: 16) {
            Card(title: "Mouse") {
                HStack(spacing: 28) {
                    ZStack(alignment: .bottom) {
                        Image(systemName: "computermouse.fill")
                            .resizable().scaledToFit().frame(height: 120)
                            .foregroundStyle(Theme.raised)
                        HStack(spacing: 10) {
                            ForEach(Array(zones.enumerated()), id: \.offset) { _, rgb in
                                let colour = Color(.sRGB, red: rgb.r, green: rgb.g, blue: rgb.b)
                                Capsule().fill(colour).frame(width: 18, height: 6)
                                    .shadow(color: colour, radius: 6)
                            }
                        }
                        .opacity(status?.mouseSync == true && status?.syncing == true ? 1 : 0.25)
                        .padding(.bottom, 14)
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle(isOn: Binding(get: { status?.mouseSync ?? false },
                                             set: { store.set("mouse_sync", $0 ? "1" : "0") })) {
                            Text("Follow the screen").caps(Theme.ink)
                        }
                        .toggleStyle(.switch).tint(Theme.green)
                        Text(explanation(mouse)).hint()
                        if let permission = mouse?.permission, mouse?.connected == true, permission != "granted" {
                            Button("Open Input Monitoring settings") {
                                NSWorkspace.shared.open(URL(string:
                                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent")!)
                            }
                        }
                    }
                }
            }
        }
    }

    private func explanation(_ mouse: MouseInfo?) -> String {
        guard let mouse, mouse.connected else { return "No Logitech LIGHTSYNC mouse (G102/G203) is plugged in." }
        if let permission = mouse.permission, permission != "granted" {
            return "macOS needs Input Monitoring for Python, which runs the hub, before the lights can be set."
        }
        return "\(mouse.name ?? "The mouse") is ready."
    }
}

struct BedPage: View {
    @EnvironmentObject private var store: HubStore
    /// The one-time wiring form (Setup) rather than the everyday controls (Lights).
    var wiring = false
    @State private var leds = ""
    @State private var amps = ""
    @State private var pin = 5

    private static let modes = [("screen", "Screen sync"), ("colour", "Colour"), ("effect", "Effect"), ("off", "Off")]
    private static let looks = [("blend", "Blend"), ("mood", "Mood"), ("side", "Edge")]
    private static let sides = [("whole", "All round"), ("bottom", "Bottom"), ("right", "Right"), ("top", "Top"), ("left", "Left")]
    private static let pins = [("D1", 5), ("D2", 4), ("D5", 14), ("D6", 12), ("D7", 13), ("RX", 3)]
    private static let lookHints = [
        "blend": "For a strip low on the wall beside the monitor: the bottom-right corner of the screen next to it, "
            + "easing into the scene's colour along the strip.",
        "mood": "One colour for the whole strip, moving with the monitor strip: the scene's strongest.",
        "side": "One part of the screen stretched along the strip.",
    ]

    var body: some View {
        let status = store.status
        VStack(spacing: 16) {
            if wiring {
                stripCard(status)
            } else if (status?.bedLeds ?? 0) > 0 {
                controls(status)
            } else {
                Card(title: "Bed strip") {
                    Text("No bed strip is set up yet. Wire it as in docs/wiring.md, then enter its details under "
                         + "Setup → Bed strip wiring.").hint()
                }
            }
        }
        .onAppear {
            store.loadEffects()
            fillForm()
        }
        .onChange(of: store.status?.bedLeds) { _, _ in fillForm() }
    }

    private func controls(_ status: HubStatus?) -> some View {
        let mode = status?.bedMode ?? "screen", look = status?.bedLook ?? "blend"
        return Card(title: "Bed strip") {
            Text("\(status?.bedLeds ?? 0) LEDs · up to " + String(format: "%.1f A", Double(status?.bedPowerMa ?? 0) / 1000)).caps()
            choices("bed_mode", Self.modes, current: mode)
            if mode == "screen" {
                choices("bed_look", Self.looks, current: look)
                Text(Self.lookHints[look] ?? "").hint()
                if look == "side" { choices("bed_side", Self.sides, current: status?.bedSide ?? "whole") }
            }
            if mode == "effect" {
                Picker("Effect", selection: Binding(get: { status?.bedEffect ?? 9 },
                                                    set: { store.set("bed_effect", String($0)) })) {
                    ForEach(effectChoices(current: status?.bedEffect ?? 9), id: \.fx) { Text($0.name).tag($0.fx) }
                }
                SettingSlider(key: "bed_speed", label: "Speed", range: 0...255, format: { String(Int($0.rounded())) })
            }
            if mode == "colour" {
                ColorPicker("Colour", selection: colour(status), supportsOpacity: false)
            }
            SettingSlider(key: "bed_brightness", label: "Brightness")
        }
    }

    private func stripCard(_ status: HubStatus?) -> some View {
        Card(title: "Bed strip wiring") {
            Toggle(isOn: Binding(get: { status?.bedReverse ?? false },
                                 set: { store.set("bed_reverse", $0 ? "1" : "0") })) {
                Text("First LED is the far end").caps(Theme.ink)
            }
            .toggleStyle(.switch).tint(Theme.green)
            Text("Leave off when the strip starts next to the monitor.").hint()
            HStack(alignment: .bottom, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("LEDs").caps()
                    TextField("LEDs", text: $leds).textFieldStyle(.roundedBorder).labelsHidden()
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Data pin").caps()
                    Picker("Data pin", selection: $pin) {
                        ForEach(Self.pins, id: \.1) { Text($0.0).tag($0.1) }
                    }
                    .labelsHidden().fixedSize()
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Power limit (A)").caps()
                    TextField("Amps", text: $amps).textFieldStyle(.roundedBorder).labelsHidden()
                }
            }
            Text("The amps are the bed's share of the adapter, which both strips may be on. The board restarts for "
                 + "about 10 seconds. RX gives the board the least memory to spare, so keep strips there short.").hint()
            Button("Apply and restart the board") { store.setupBed(leds: leds, amps: amps, pin: pin) }
                .disabled(Int(leds) == nil || Double(amps) == nil)
        }
    }

    private func choices(_ key: String, _ options: [(String, String)], current: String?) -> some View {
        HStack(spacing: 4) {
            ForEach(options, id: \.0) { value, title in
                ChoiceButton(title: title, chosen: current == value) { store.set(key, value) }
            }
        }
    }

    /// The curated effects, plus the one in use if it is not among them.
    private func effectChoices(current: Int) -> [Effect] {
        store.effects.contains { $0.fx == current } ? store.effects : store.effects + [Effect(fx: current, name: "Effect \(current)")]
    }

    private func colour(_ status: HubStatus?) -> Binding<Color> {
        Binding(get: { Color(hex: "#" + (status?.bedColour ?? "ffb060").replacingOccurrences(of: "#", with: "")) },
                set: { store.setLater("bed_colour", String($0.rgb.hex.dropFirst())) })
    }

    private func fillForm() {
        guard let status = store.status else { return }
        leds = String(status.bedLeds ?? 0)
        amps = String(format: "%.1f", Double(status.bedPowerMa ?? 2000) / 1000)
        pin = status.bedPin ?? 5
    }
}

struct ProfilesPage: View {
    @EnvironmentObject private var store: HubStore
    @State private var newName = ""
    @State private var confirmDelete: String?

    var body: some View {
        let active = store.status?.profile ?? ""
        VStack(spacing: 16) {
            Card(title: "Built in") {
                ForEach(HubStore.builtinProfiles, id: \.0) { name, title in
                    row(title: title, detail: name == "movie" ? "Glides gently between colours."
                            : name == "enriched" ? "Sharp and true, with deep blacks and bright highlights."
                            : "Follows the action quickly.", active: active == name) {
                        store.apply(profile: name)
                    }
                }
            }
            Card(title: "Yours") {
                let mine = store.status?.myProfiles ?? []
                if mine.isEmpty { Text("Save the current look to make a profile of your own.").hint() }
                ForEach(mine, id: \.self) { name in
                    row(title: name, detail: "Brightness, colour, immersion, response and mouse.",
                        active: active == name, delete: { confirmDelete = name }) {
                        store.apply(profile: name)
                    }
                }
                HStack {
                    TextField("Name, e.g. Late night", text: $newName).textFieldStyle(.roundedBorder)
                        .onSubmit(save)
                    Button("Save this look", action: save)
                        .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                Text("A profile keeps the whole look: brightness, colour, immersion, warmth, response and whether "
                     + "the mouse follows. Up to 24 letters, digits, spaces, - or _.").hint()
            }
        }
        .confirmationDialog("Delete the profile \(confirmDelete ?? "")?", isPresented: Binding(
            get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } })) {
            Button("Delete", role: .destructive) {
                if let name = confirmDelete { store.deleteProfile(name) }
                confirmDelete = nil
            }
        } message: {
            Text("The current look stays as it is.")
        }
    }

    private func save() {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        store.saveProfile(name)
        newName = ""
    }

    private func row(title: String, detail: String, active: Bool, delete: (() -> Void)? = nil,
                     apply: @escaping () -> Void) -> some View {
        HStack(spacing: 12) {
            Lamp(on: active, colour: Theme.amber)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink)
                Text(detail).hint()
            }
            Spacer()
            if let delete { Button("Delete", role: .destructive, action: delete) }
            Button(active ? "In use" : "Use", action: apply).disabled(active)
        }
        .padding(10)
        .overlay(RoundedRectangle(cornerRadius: 4).stroke(active ? Theme.amber.opacity(0.5) : Theme.rule))
    }
}

struct CalibratePage: View {
    @EnvironmentObject private var store: HubStore
    @State private var chosen = ""

    var body: some View {
        let job = store.camera
        let usable = store.cameras.filter { !$0.builtin }
        let working = job?.state == "working"
        VStack(spacing: 16) {
            Card(title: "Webcam calibration") {
                Text("Aim a USB webcam at the monitor and the glow around it. For about a minute the monitor "
                     + "shows test colours while the strip is matched to them. The Mac's built-in camera is "
                     + "never used.").hint()
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
                            .keyboardShortcut(.defaultAction)
                        Button("Check during a video") { store.startCamera(task: "check", camera: chosen) }
                    }
                    .disabled(working || chosen.isEmpty)
                    Spacer()
                    Button("Refresh list") { store.loadCameras() }.disabled(working)
                }
                if let job, job.state != "idle" {
                    Text(stateLine(job)).caps(job.state == "error" ? Theme.red : job.state == "done" ? Theme.green : Theme.amber)
                    ScrollView {
                        Text(job.log.joined(separator: "\n"))
                            .font(.system(size: 12, design: .monospaced)).foregroundStyle(Theme.dim)
                            .frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                    }
                    .frame(maxHeight: 180)
                    .padding(10)
                    .background(Theme.inset, in: RoundedRectangle(cornerRadius: 2))
                }
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
