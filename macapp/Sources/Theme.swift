import SwiftUI

/// The web page's palette, so the app and the page read as one product.
enum Theme {
    static let bg = Color(hex: "#0c0c0b")
    static let panel = Color(hex: "#151513")
    static let raised = Color(hex: "#1f1e1b")
    static let inset = Color(hex: "#070706")
    static let rule = Color(hex: "#2b2a26")
    static let ruleHi = Color(hex: "#3b3a35")
    static let ink = Color(hex: "#ece6d6")
    static let dim = Color(hex: "#a49d8c")
    static let amber = Color(hex: "#ffb000")
    static let green = Color(hex: "#4be37a")
    static let red = Color(hex: "#ff6a55")
    static let off = Color(hex: "#2e2d29")
}

extension Color {
    init(hex: String) {
        let rgb = RGB(hex: hex) ?? RGB(r: 0, g: 0, b: 0)
        self.init(.sRGB, red: rgb.r, green: rgb.g, blue: rgb.b)
    }

    var rgb: RGB {
        let colour = NSColor(self).usingColorSpace(.sRGB) ?? .black
        return RGB(r: colour.redComponent, g: colour.greenComponent, b: colour.blueComponent)
    }
}

extension Text {
    func caps(_ colour: Color = Theme.dim) -> some View {
        font(.system(size: 11, weight: .semibold)).tracking(1.6).textCase(.uppercase).foregroundStyle(colour)
    }

    func readout() -> some View {
        font(.system(size: 13, weight: .semibold, design: .monospaced)).foregroundStyle(Theme.amber)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Theme.inset, in: RoundedRectangle(cornerRadius: 2))
    }

    func hint() -> some View {
        font(.system(size: 12.5)).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
    }
}

struct Card<Content: View>: View {
    let title: String
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                Text(title).caps()
                Rectangle().fill(Theme.rule).frame(height: 1)
            }
            content
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 6))
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Theme.rule))
    }
}

struct Lamp: View {
    var on: Bool
    var colour = Theme.green

    var body: some View {
        Circle().fill(on ? colour : Theme.off).frame(width: 8, height: 8)
            .shadow(color: on ? colour.opacity(0.8) : .clear, radius: 4)
    }
}

/// A raised key that lights amber while chosen, like the page's segmented buttons.
struct ChoiceButton: View {
    let title: String
    let chosen: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Lamp(on: chosen, colour: Theme.amber).frame(width: 6, height: 6)
                Text(title).caps(chosen ? Theme.ink : Theme.dim)
            }
            .frame(maxWidth: .infinity, minHeight: 34)
            .background(chosen ? Theme.panel : Theme.raised, in: RoundedRectangle(cornerRadius: 4))
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(chosen ? Theme.amber.opacity(0.5) : Theme.ruleHi))
        }
        .buttonStyle(.plain)
    }
}

/// A plain raised key with light text, readable on any panel background.
struct KeyButton: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title).caps(Theme.ink).padding(.horizontal, 12).frame(minHeight: 30)
                .background(Theme.raised, in: RoundedRectangle(cornerRadius: 4))
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(Theme.ruleHi))
        }
        .buttonStyle(.plain)
    }
}

/// A slider bound to one hub setting. Polls never yank it while it is being
/// dragged, or for a moment after, so the knob does not jump back.
struct SettingSlider: View {
    @EnvironmentObject private var store: HubStore
    let key: String
    let label: String
    var range: ClosedRange<Double> = 0...1
    var hint: String?
    var format: (Double) -> String = { "\(Int(($0 * 100).rounded()))%" }

    @State private var value = 0.0
    @State private var editing = false
    @State private var touched = Date.distantPast

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).caps(Theme.ink)
                Spacer()
                Text(format(value)).readout()
            }
            Slider(value: $value, in: range) { active in
                editing = active
                touched = Date()
            }
            .tint(Theme.amber)
            .onChange(of: value) { _, new in
                guard editing || Date().timeIntervalSince(touched) < 0.5 else { return }
                touched = Date()
                store.send(key, new)
            }
            if let hint { Text(hint).hint() }
        }
        .onAppear { if let v = store.status?.number(key) { value = v } }
        .onReceive(store.$status) { status in
            guard !editing, Date().timeIntervalSince(touched) > 2, let v = status?.number(key) else { return }
            if abs(v - value) > 0.0005 { value = v }
        }
    }
}

/// Cinema, Gaming and the profiles you saved.
struct ProfileChoices: View {
    @EnvironmentObject private var store: HubStore

    var body: some View {
        let active = store.status?.profile ?? ""
        VStack(spacing: 6) {
            HStack(spacing: 4) {
                ForEach(HubStore.builtinProfiles, id: \.0) { name, title in
                    ChoiceButton(title: title, chosen: active == name) { store.apply(profile: name) }
                }
            }
            let mine = store.status?.myProfiles ?? []
            if !mine.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 4)], spacing: 4) {
                    ForEach(mine, id: \.self) { name in
                        ChoiceButton(title: name, chosen: active == name) { store.apply(profile: name) }
                    }
                }
            }
        }
    }
}
