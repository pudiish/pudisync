import SwiftUI

/// Built-in looks, then your own under a label of their own, and what the one in use is for.
/// The same grouping as the phone page's Home.
struct LookPicker: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        let status = store.status
        let active = status?.profile ?? ""
        let mine = status?.myProfiles ?? []
        VStack(alignment: .leading, spacing: 8) {
            label("Look")
            FlowRow {
                ForEach(HubStore.builtinProfiles, id: \.0) { name, title in
                    Chip(title: title, chosen: active == name) { store.pickStyle(name) }
                }
            }
            if !mine.isEmpty {
                label("Your looks")
                FlowRow {
                    ForEach(mine, id: \.self) { name in
                        Chip(title: name, chosen: active == name) { store.pickStyle(name) }
                    }
                }
            }
            Text(lookHint(active: active, builtin: HubStore.styleHints, mine: mine, note: status?.note(of: active))).hint()
        }
    }

    private func label(_ text: String) -> some View {
        Text(text).font(.system(size: 12.5, weight: .medium)).foregroundStyle(Theme.dim)
    }
}

/// What can move the picture beyond the look: Trippy, and following the sound. Calm stops the sound altogether.
struct SoundControls: View {
    @EnvironmentObject private var store: HubStore
    var withCalm = false

    var body: some View {
        let status = store.status
        VStack(alignment: .leading, spacing: 12) {
            SwitchRow(title: "Trippy", detail: withCalm ? "Bends and ripples the look's own colours, sound or no sound." : nil,
                      isOn: status?.trippy ?? false) { store.set("trippy", $0 ? "1" : "0") }
            Text("Follow the sound").font(.system(size: 12.5, weight: .medium)).foregroundStyle(Theme.dim)
            Segmented([("off", "Off"), ("subtle", "Subtle"), ("balanced", "Balanced"), ("expressive", "Expressive")],
                      chosen: status?.audioReact ?? "off") { store.set("audio_react", $0) }
            if withCalm {
                SwitchRow(title: "Calm", detail: "No motion from the sound at all, and the sound is not listened to.",
                          isOn: status?.calm ?? false) { store.set("calm", $0 ? "1" : "0") }
            }
        }
    }
}

/// One look as a row: its name, whether it is in use, and what it is for. Clicking it uses the look.
struct LookRow: View {
    let title: String
    let note: String?
    let inUse: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(title).font(.system(size: 14, weight: .semibold)).foregroundStyle(Theme.ink)
                    if inUse {
                        Text("In use").font(.system(size: 10.5, weight: .bold)).foregroundStyle(Theme.accentInk)
                            .padding(.horizontal, 7).padding(.vertical, 1).background(Theme.ok, in: Capsule())
                    }
                }
                if let note {
                    Text(note).font(.system(size: 12.5)).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("No description yet.").font(.system(size: 12.5).italic()).foregroundStyle(Theme.dim)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12).padding(.vertical, 9)
            .background(Theme.raised, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(inUse ? Theme.accent : Theme.line, lineWidth: inUse ? 2 : 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(inUse ? .isSelected : [])
    }
}
