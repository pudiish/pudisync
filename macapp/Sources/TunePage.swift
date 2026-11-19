import SwiftUI

func settleSeconds(_ blend: Double) -> String { String(format: "%.2f s", 0.06 * pow(0.8 / 0.06, 1 - blend)) }

struct PictureCard: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        Card(title: "Picture") {
            Text("How the light follows the screen.").hint()
            settingFader("brightness", "Sync brightness", store: store)
            settingFader("saturation", "Colour", range: 0...3, store: store)
            settingFader("white_kelvin", "Warmth", range: 2700...6500, step: 100,
                         format: { "\(Int($0)) K" }, store: store)
            settingFader("immersion", "Immersion",
                         hint: "Lifts deep colours and floods the room with the scene's colour. Skin and greys stay true.",
                         format: offOrPercent, store: store)
            settingFader("adaptive", "Adaptive brightness",
                         hint: "Each LED takes its brightness from what it faces, so a bright screen no longer floods the wall.",
                         format: offOrPercent, store: store)
            settingFader("hue_balance", "Colour balance",
                         hint: "Lifts the colours an LED shows dimly, like blue and deep red, when there is brightness to spare. Nothing is dimmed.",
                         format: offOrPercent, store: store)
            settingFader("dark_boost", "Dark scenes",
                         hint: "How much the light still shows and reacts when the whole picture is dark.",
                         format: offOrPercent, store: store)
            settingFader("blend", "Response", range: 0.01...1, format: settleSeconds, store: store)
        }
    }
}

struct LooksCard: View {
    @EnvironmentObject private var store: HubStore
    @State private var newName = ""
    @State private var confirmDelete: String?

    var body: some View {
        let active = store.status?.profile ?? ""
        let mine = store.status?.myProfiles ?? []
        Card(title: "Looks") {
            FlowRow {
                ForEach(HubStore.builtinProfiles, id: \.0) { name, title in
                    Chip(title: title, chosen: active == name) { store.pickStyle(name) }
                }
                ForEach(mine, id: \.self) { name in
                    Chip(title: name, chosen: active == name) { store.pickStyle(name) }
                        .contextMenu { Button("Delete \(name)…", role: .destructive) { confirmDelete = name } }
                }
            }
            HStack {
                TextField("Name, e.g. Late night", text: $newName).textFieldStyle(.roundedBorder).onSubmit(save)
                Button("Save this look", action: save).disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Text("A look keeps the picture: brightness, colour, warmth, immersion, response and whether the mouse follows. "
                 + "Right-click one of yours to delete it.").hint()
        }
        .confirmationDialog("Delete the look \(confirmDelete ?? "")?", isPresented: Binding(
            get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } })) {
            Button("Delete", role: .destructive) {
                if let name = confirmDelete { store.deleteProfile(name) }
                confirmDelete = nil
            }
        } message: {
            Text("The lights keep their current look.")
        }
    }

    private func save() {
        let name = newName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        store.saveProfile(name)
        newName = ""
    }
}

struct FineColourCard: View {
    @EnvironmentObject private var store: HubStore
    @State private var open = false

    var body: some View {
        Card {
            DisclosureGroup(isExpanded: $open) {
                VStack(alignment: .leading, spacing: 14) {
                    SwitchRow(title: "Match the screen's colours", detail: "Corrects the strip so its colours read as the screen's.",
                              isOn: store.status?.colourMatch ?? true) { store.set("colour_match", $0 ? "1" : "0") }
                    settingFader("contrast", "Contrast", range: 0.5...2.5, step: 0.05,
                                 format: { String(format: "%.2f×", $0) }, store: store)
                    settingFader("spread", "Spread", range: 0...0.8, store: store)
                    settingFader("colour_weight", "Colour weight", store: store)
                    Text("White balance").font(.system(size: 12.5, weight: .medium)).foregroundStyle(Theme.dim)
                    settingFader("wb_r", "Red", range: 0.2...1, format: { String(format: "%.2f", $0) }, store: store)
                    settingFader("wb_g", "Green", range: 0.2...1, format: { String(format: "%.2f", $0) }, store: store)
                    settingFader("wb_b", "Blue", range: 0.2...1, format: { String(format: "%.2f", $0) }, store: store)
                }
                .padding(.top, 12)
            } label: {
                Text("Fine colour").font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink)
            }
        }
    }
}

/// One editor for every part of the monitor strip: drag across the LEDs, choose what that stretch shows, apply.
struct PartsCard: View {
    @EnvironmentObject private var store: HubStore
    @State private var selection: ClosedRange<Int>?
    @State private var mode = "solid"
    @State private var colour = swatches[2].hex
    @State private var brightness = 1.0
    @State private var keep = true

    private static let modes = [("sync", "Scene"), ("warm", "Lamp"), ("solid", "Colour"), ("off", "Off")]

    var body: some View {
        let parts = store.parts
        Card(title: "Parts of the strip") {
            Text("Give a stretch of the monitor strip a lamp's warm light, a colour of its own, or switch it off. "
                 + "Scene hands it back to whatever every light shows.").hint()
            LEDFrame(selection: $selection, pick: pickTint)
            HStack {
                Button("Select all") { selection = 0...max(0, (store.status?.ledCount ?? 1) - 1) }
                if selection != nil { Button("Clear selection") { selection = nil } }
                Spacer()
            }
            Segmented(Self.modes, chosen: mode) { mode = $0 }
            if mode == "solid" {
                HStack(spacing: 4) {
                    ForEach(swatches, id: \.hex) { swatch in
                        SwatchButton(hex: swatch.hex, name: swatch.name, chosen: colour == swatch.hex, size: 24) { colour = swatch.hex }
                    }
                }
                ColourWheel(hex: colour, label: "Colour for the selection") { hex, _ in colour = hex }
            }
            if mode == "warm" || mode == "solid" {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text("Brightness").font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.ink)
                        Spacer()
                        Text("\(Int((brightness * 100).rounded()))%").readout()
                    }
                    Slider(value: $brightness, in: 0...1).tint(Theme.accent)
                }
            }
            if mode != "sync" {
                SwitchRow(title: "Keep after the hub restarts", isOn: keep) { keep = $0 }
            }
            Button {
                apply()
            } label: {
                Text("Apply").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent).tint(Theme.accent).controlSize(.large)
            .disabled(selection == nil)
            .keyboardShortcut(.return, modifiers: .command)
            if parts.isEmpty {
                Text("No parts set: the whole strip shows the scene.").hint()
            } else {
                VStack(spacing: 6) {
                    ForEach(Array(parts.enumerated()), id: \.offset) { index, zone in row(zone, index: index, of: parts) }
                }
                Button("Clear every part") { store.setParts([]) }
            }
        }
    }

    private var pickTint: String? {
        switch mode {
        case "solid": return colour
        case "warm": return lampColour
        default: return nil
        }
    }

    private func row(_ zone: Zone, index: Int, of parts: [Zone]) -> some View {
        HStack(spacing: 10) {
            Circle().fill(partTint(zone).map { Color(hex: $0) } ?? Theme.well).frame(width: 14, height: 14)
                .overlay(Circle().stroke(Theme.lineHi))
            Text("LEDs \(zone.start)–\(zone.end) · \(partNames[zone.mode] ?? zone.mode)"
                 + (zone.mode == "off" ? "" : " · \(Int((zone.brightness * 100).rounded()))%"))
                .font(.system(size: 13)).foregroundStyle(Theme.ink)
            Spacer()
            Text(zone.keep == false ? "Until restart" : "Kept").font(.system(size: 11.5)).foregroundStyle(Theme.dim)
            Button("Edit") { edit(zone) }
            Button("Remove") { store.setParts(parts.enumerated().filter { $0.offset != index }.map(\.element)) }
        }
        .padding(.leading, 12).padding(.trailing, 6).padding(.vertical, 6)
        .background(Theme.raised, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private func edit(_ zone: Zone) {
        selection = zone.start...zone.end
        mode = zone.mode
        if zone.mode == "solid", let tint = partTint(zone) { colour = tint }
        brightness = zone.brightness
        keep = zone.keep != false
    }

    private func apply() {
        guard let range = selection else { return }
        let part = Zone(start: range.lowerBound, end: range.upperBound, mode: mode, color: colour,
                        brightness: brightness, keep: keep)
        store.setParts(withPart(store.parts, range.lowerBound, range.upperBound, part))
        selection = nil
    }
}
