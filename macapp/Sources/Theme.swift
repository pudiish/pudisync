import AppKit
import SwiftUI

/// The phone page's palette, light or dark with the Mac, so the app and the page read as one product.
enum Theme {
    // The same values as ambilight/web/app.css, where their contrast is checked.
    static let bg = adaptive("#171a17", "#eeece3")
    static let surface = adaptive("#20241f", "#f8f7f1")
    static let raised = adaptive("#292e28", "#ecebe3")
    static let well = adaptive("#121512", "#e2e0d6")
    static let line = adaptive("#3b4139", "#d5d4ca")
    static let lineHi = adaptive("#737b6d", "#83887c")
    static let ink = adaptive("#f0f0e8", "#20231f")
    static let dim = adaptive("#b4b9ae", "#5f645a")
    static let faint = adaptive("#6b7266", "#9a9e93")
    static let ledOff = adaptive("#2e332d", "#d9d7cd")
    static let accent = adaptive("#a1b18e", "#4e6044")
    static let accentInk = adaptive("#171a17", "#ffffff")
    static let ok = adaptive("#70c69a", "#2b6e4d")
    static let bad = adaptive("#f08b83", "#b3443d")
    static let radius: CGFloat = 12

    private static func adaptive(_ dark: String, _ light: String) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let rgb = RGB(hex: appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light)
                ?? RGB(r: 0, g: 0, b: 0)
            return NSColor(srgbRed: rgb.r, green: rgb.g, blue: rgb.b, alpha: 1)
        })
    }
}

extension Color {
    init(hex: String) {
        let rgb = RGB(hex: hex) ?? RGB(r: 0, g: 0, b: 0)
        self.init(.sRGB, red: rgb.r, green: rgb.g, blue: rgb.b)
    }
}

extension Text {
    func hint() -> some View {
        font(.system(size: 12.5)).foregroundStyle(Theme.dim).fixedSize(horizontal: false, vertical: true)
    }

    func readout() -> some View {
        font(.system(size: 13, weight: .semibold, design: .monospaced)).monospacedDigit().foregroundStyle(Theme.ink)
    }
}

struct Card<Content: View>: View {
    var title: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let title { Text(title).font(.system(size: 15, weight: .semibold)).foregroundStyle(Theme.ink) }
            content
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous).stroke(Theme.line))
    }
}

struct Lamp: View {
    var on: Bool
    var colour = Theme.ok

    var body: some View {
        Circle().fill(on ? colour : Theme.ledOff).frame(width: 8, height: 8)
            .shadow(color: on ? colour.opacity(0.8) : .clear, radius: 4)
    }
}

/// One choice among a few, like the phone page's segmented buttons; `big` adds an icon above each label.
struct Segmented<Value: Hashable>: View {
    let options: [(value: Value, title: String, icon: String?)]
    let chosen: Value?
    var big = false
    let pick: (Value) -> Void

    init(_ options: [(Value, String)], chosen: Value?, pick: @escaping (Value) -> Void) {
        self.options = options.map { ($0.0, $0.1, nil) }
        self.chosen = chosen
        self.pick = pick
    }

    init(icons options: [(Value, String, String)], chosen: Value?, pick: @escaping (Value) -> Void) {
        self.options = options.map { ($0.0, $0.1, Optional($0.2)) }
        self.chosen = chosen
        self.big = true
        self.pick = pick
    }

    var body: some View {
        HStack(spacing: 4) {
            ForEach(options, id: \.value) { option in
                let on = option.value == chosen
                Button { pick(option.value) } label: {
                    VStack(spacing: 3) {
                        if let icon = option.icon { Image(systemName: icon).font(.system(size: 15, weight: .medium)) }
                        Text(option.title).font(.system(size: big ? 12.5 : 13, weight: .semibold))
                    }
                    .foregroundStyle(on ? Theme.ink : Theme.dim)
                    .frame(maxWidth: .infinity, minHeight: big ? 50 : 32)
                    .background(on ? Theme.raised : .clear, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                    .shadow(color: on ? .black.opacity(0.25) : .clear, radius: 1.5, y: 1)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .padding(4)
        .background(Theme.well, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

/// A rounded pill that lights up while chosen.
struct Chip: View {
    let title: String
    let chosen: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title).font(.system(size: 13, weight: .semibold))
                .foregroundStyle(chosen ? Theme.accentInk : Theme.ink)
                .padding(.horizontal, 14).frame(minHeight: 32)
                .background(chosen ? Theme.accent : Theme.raised, in: Capsule())
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }
}

/// A round colour to click.
struct SwatchButton: View {
    let hex: String
    let name: String
    var chosen = false
    var size: CGFloat = 30
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Circle().fill(Color(hex: hex)).frame(width: size, height: size)
                .overlay(Circle().stroke(Theme.lineHi, lineWidth: 1.5))
                .padding(3)
                .overlay(Circle().stroke(chosen ? Theme.accent : .clear, lineWidth: 2.5))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .help(name)
        .accessibilityLabel(name)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }
}

/// A slider tied to one value on the hub. Polls never yank it while it is dragged, or for a moment after.
struct Fader: View {
    @EnvironmentObject private var store: HubStore
    let label: String
    var range: ClosedRange<Double> = 0...1
    var step: Double?
    var hint: String?
    var format: (Double) -> String = { "\(Int(($0 * 100).rounded()))%" }
    let read: (HubStatus) -> Double?
    let write: (Double, Bool) -> Void

    @State private var value = 0.0
    @State private var editing = false
    @State private var touched = Date.distantPast

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label).font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.ink)
                Spacer()
                Text(format(value)).readout()
            }
            slider.tint(Theme.accent).disabled(store.status.flatMap(read) == nil)
            if let hint { Text(hint).hint() }
        }
        .onAppear { if let v = store.status.flatMap(read) { value = v } }
        .onReceive(store.$status) { status in
            guard !editing, Date().timeIntervalSince(touched) > HubStore.holdSeconds, let v = status.flatMap(read) else { return }
            if abs(v - value) > 0.0005 { value = v }
        }
        .accessibilityValue(format(value))
    }

    @ViewBuilder private var slider: some View {
        let changed: (Bool) -> Void = { active in
            editing = active
            touched = Date()
            if !active { write(value, true) }
        }
        if let step {
            Slider(value: $value, in: range, step: step, onEditingChanged: changed).onChange(of: value) { _, new in moved(new) }
        } else {
            Slider(value: $value, in: range, onEditingChanged: changed).onChange(of: value) { _, new in moved(new) }
        }
    }

    private func moved(_ new: Double) {
        guard editing || Date().timeIntervalSince(touched) < 0.5 else { return }
        touched = Date()
        write(new, false)
    }
}

/// A fader for one setting in the hub's config.
@MainActor func settingFader(_ key: String, _ label: String, range: ClosedRange<Double> = 0...1, step: Double? = nil, hint: String? = nil,
                  format: @escaping (Double) -> String = { "\(Int(($0 * 100).rounded()))%" }, store: HubStore) -> Fader {
    Fader(label: label, range: range, step: step, hint: hint, format: format, read: { $0.number(key) },
          write: { value, _ in store.send(key, value) })
}

/// A labelled on/off switch with a line of explanation.
struct SwitchRow: View {
    let title: String
    var detail: String?
    let isOn: Bool
    let set: (Bool) -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 13, weight: .medium)).foregroundStyle(Theme.ink)
                if let detail { Text(detail).hint() }
            }
            Spacer(minLength: 8)
            Toggle(title, isOn: Binding(get: { isOn }, set: set)).toggleStyle(.switch).tint(Theme.accent).labelsHidden()
        }
    }
}

func offOrPercent(_ value: Double) -> String { value < 0.005 ? "Off" : "\(Int((value * 100).rounded()))%" }
