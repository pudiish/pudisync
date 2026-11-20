import Foundation

// The app's reading of the hub, kept free of SwiftUI so ./build.sh test can check it.
// Each function mirrors the phone page's function of the same name (ambilight/web/index.html).

/// What every light shows: the board follows the screen, holds a colour, runs an effect, or is off.
enum LightScene: String, CaseIterable {
    case screen, colour, effect, off
}

func sceneOf(mode: String, running: Bool, wledOn: Bool?, wledFx: Int?) -> LightScene {
    let mode = mode.isEmpty ? (running ? "sync" : wledOn == true ? "effect" : "off") : mode
    switch mode {
    case "sync": return .screen
    case "off": return .off
    default: return (wledFx ?? 0) != 0 ? .effect : .colour
    }
}

/// How the big power button and the words beside it read.
enum Glow: Equatable {
    case on, effect, fault, off
}

struct HeroView: Equatable {
    var glow: Glow
    var state: String
    var hint: String
}

func heroView(online: Bool, boardReachable: Bool?, scene: LightScene, colour: String?, effect: String) -> HeroView {
    guard online else { return HeroView(glow: .off, state: "Hub offline", hint: "Waiting for the hub to answer.") }
    let fault = boardReachable == false
    let offHint = "Click to turn every light off."
    switch scene {
    case .screen:
        return fault ? HeroView(glow: .fault, state: "Syncing, board not answering",
                                hint: "The hub keeps sending; the board may be restarting.")
            : HeroView(glow: .on, state: "Following your screen", hint: offHint)
    case _ where fault:
        return HeroView(glow: .fault, state: "Can't reach the board", hint: "Click to start screen sync once it's back.")
    case .colour:
        return HeroView(glow: .effect, state: colour.map { colourName("#" + $0) } ?? "Colour", hint: offHint)
    case .effect:
        return HeroView(glow: .effect, state: effect, hint: offHint)
    case .off:
        return HeroView(glow: .off, state: "Off", hint: "Click to start screen sync.")
    }
}

// MARK: looks

/// The line under the look chips, like the phone page's: a built-in look's own words, the description
/// you wrote for one of yours, or what to do when there is none.
func lookHint(active: String, builtin: [String: String], mine: [String], note: String?) -> String {
    if let text = builtin[active] { return text }
    if mine.contains(active) { return note.flatMap { $0.isEmpty ? nil : $0 } ?? "Your look. Describe it under Looks." }
    return "Tuned by hand. Save it as a look under Looks."
}

// MARK: the bed strip

let bedLooks: [String: String] = [
    "blend": "The screen's bottom corner next to the bed, easing into the scene's colour along the strip.",
    "mood": "One colour for the whole strip: the scene's strongest.",
    "side": "One part of the screen stretched along the strip.",
]

/// What the bed row says, and whether its brightness slider means anything right now.
struct BedView: Equatable {
    var state: String
    var lit: Bool
    var brightness: Bool
    var hint: String
}

func bedView(leds: Int, mode: String, look: String, effect: String, scene: LightScene) -> BedView {
    guard leds > 0 else { return BedView(state: "Not set up", lit: false, brightness: false, hint: "") }
    switch mode {
    case "off": return BedView(state: "Off", lit: false, brightness: false, hint: "")
    case "colour": return BedView(state: "Its own colour", lit: scene != .off, brightness: true, hint: "")
    case "effect": return BedView(state: "Its own effect · \(effect)", lit: scene != .off, brightness: true, hint: "")
    default: break
    }
    switch scene {
    case .screen: return BedView(state: "Matches · follows the screen", lit: true, brightness: true, hint: bedLooks[look] ?? "")
    case .off: return BedView(state: "Matches · off with everything", lit: false, brightness: false, hint: "")
    default:
        return BedView(state: "Matches · \(scene == .colour ? "same colour" : "same effect")", lit: true, brightness: false,
                       hint: "Shows the monitor strip's colour or effect, at the master brightness.")
    }
}

// MARK: colours

let swatches: [(name: String, hex: String)] = [
    ("Warm white", "#ffb46b"), ("Cool white", "#ffffff"), ("Red", "#ff2010"), ("Amber", "#ff8c00"),
    ("Green", "#20e050"), ("Cyan", "#00c8ff"), ("Blue", "#2040ff"), ("Magenta", "#e030ff"),
]

private let hueNames: [(upTo: Double, name: String)] = [
    (18, "Red"), (40, "Orange"), (66, "Yellow"), (100, "Lime"), (155, "Green"), (195, "Cyan"),
    (220, "Sky blue"), (255, "Blue"), (285, "Purple"), (320, "Magenta"), (345, "Pink"), (360, "Red"),
]

let warmestKelvin = 2200, coolestKelvin = 6500

struct HSV: Equatable {
    var h: Double
    var s: Double
    var v: Double
}

extension RGB {
    /// 0–255 per channel, rounded as the web page rounds.
    var bytes: [Int] { [r, g, b].map { Int((min(max($0, 0), 1) * 255).rounded()) } }

    init(bytes: [Int]) {
        self.init(r: Double(bytes[0]) / 255, g: Double(bytes[1]) / 255, b: Double(bytes[2]) / 255)
    }

    var hsv: HSV {
        let v = max(r, g, b), d = v - min(r, g, b)
        var h = 0.0
        if d > 0 {
            if v == r { h = ((g - b) / d + 6).truncatingRemainder(dividingBy: 6) }
            else if v == g { h = (b - r) / d + 2 }
            else { h = (r - g) / d + 4 }
        }
        return HSV(h: h * 60, s: v > 0 ? d / v : 0, v: v)
    }

    /// A colour from the wheel: hue in degrees, saturation and value 0–1, snapped to whole bytes.
    init(hue h: Double, saturation s: Double, value v: Double = 1) {
        func channel(_ n: Double) -> Double {
            let k = (n + h / 60).truncatingRemainder(dividingBy: 6)
            return v - v * s * max(0, min(k, 4 - k, 1))
        }
        self.init(bytes: RGB(r: channel(5), g: channel(3), b: channel(1)).bytes)
    }

    /// A white of that colour temperature as an RGB strip makes it (Tanner Helland's fit to the blackbody curve).
    init(kelvin: Int) {
        let t = Double(kelvin) / 100
        let r = t <= 66 ? 255 : 329.698727446 * pow(t - 60, -0.1332047592)
        let g = t <= 66 ? 99.4708025861 * log(t) - 161.1195681661 : 288.1221695283 * pow(t - 60, -0.0755148492)
        let b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * log(t - 10) - 305.0447927307
        self.init(bytes: [r, g, b].map { Int(min(255, max(0, $0)).rounded()) })
    }
}

/// The temperature a colour is the white of, or nil when it is a colour rather than a white.
func kelvinOf(_ hex: String) -> Int? {
    guard let want = RGB(hex: hex)?.bytes else { return nil }
    var best: Int?, gap = 7
    for k in stride(from: warmestKelvin, through: coolestKelvin, by: 50) {
        let d = zip(RGB(kelvin: k).bytes, want).reduce(0) { $0 + abs($1.0 - $1.1) }
        if d < gap { best = k; gap = d }
    }
    return best
}

/// A plain name for a shade: a swatch's own, a white by temperature, else by hue.
func colourName(_ hex: String) -> String {
    let hex = hex.lowercased()
    if let swatch = swatches.first(where: { $0.hex == hex }) { return swatch.name }
    if let k = kelvinOf(hex) {
        return k < 3300 ? "Warm white" : k < 4800 ? "Natural white" : k < 5800 ? "Cool white" : "Daylight"
    }
    guard let hsv = RGB(hex: hex)?.hsv else { return "Colour" }
    if hsv.s < 0.12 { return "White" }
    let name = hueNames.first { hsv.h < $0.upTo }?.name ?? "Red"
    return hsv.s < 0.45 ? "Pale \(name.lowercased())" : name
}

/// The colour wheel's geometry, in units of its radius from the centre with y pointing down. Colour runs hue round
/// the rim (red at the top, clockwise) and white at the centre; Whites runs daylight at the top to candlelight below.
enum Wheel {
    /// The colour under a point, and where the thumb sits for it.
    static func pick(x: Double, y: Double, whites: Bool) -> (hex: String, x: Double, y: Double) {
        var x = x, y = y
        let far = hypot(x, y)
        if far > 1 { x /= far; y /= far }
        if whites {
            let k = Int(((Double(coolestKelvin) - (y + 1) / 2 * Double(coolestKelvin - warmestKelvin)) / 50).rounded()) * 50
            return (RGB(kelvin: k).hex, x, y)
        }
        var h = (atan2(x, -y) * 180 / .pi).rounded()
        if h < 0 { h += 360 }
        let s = min(1, hypot(x, y))
        let a = h * .pi / 180
        return (RGB(hue: h, saturation: s).hex, s * sin(a), -s * cos(a))
    }

    /// Where a colour sits: round the rim by hue, or top to bottom by temperature.
    static func spot(_ hex: String, whites: Bool) -> (x: Double, y: Double) {
        if whites, let k = kelvinOf(hex) {
            return (0, -1 + 2 * Double(coolestKelvin - k) / Double(coolestKelvin - warmestKelvin))
        }
        let hsv = RGB(hex: hex)?.hsv ?? HSV(h: 0, s: 0, v: 1)
        let a = hsv.h * .pi / 180
        return (hsv.s * sin(a), -hsv.s * cos(a))
    }

    /// One arrow-key step: left/right turns the hue or warms/cools, up/down changes how much colour.
    static func step(_ hex: String, whites: Bool, across: Int, up: Int) -> String {
        if whites {
            let k = min(coolestKelvin, max(warmestKelvin, (kelvinOf(hex) ?? 4000) + (across != 0 ? across : up) * 100))
            return RGB(kelvin: k).hex
        }
        let hsv = RGB(hex: hex)?.hsv ?? HSV(h: 0, s: 1, v: 1)
        let h = (hsv.h.rounded() + Double(across * 5) + 360).truncatingRemainder(dividingBy: 360)
        return RGB(hue: h, saturation: min(1, max(0, hsv.s + Double(up) * 0.05))).hex
    }
}

/// The last few shades picked, newest first, without repeats.
func remembering(_ hex: String, in list: [String], keep: Int = 6) -> [String] {
    Array(([hex] + list.filter { $0 != hex }).prefix(keep))
}

// MARK: parts of the strip

/// A run of LEDs that does something other than the scene: a lamp, a colour, or off.
struct Zone: Codable, Equatable, Hashable {
    var start: Int
    var end: Int
    var mode: String
    var color: String
    var brightness: Double
    var keep: Bool?
}

let partNames = ["sync": "Scene", "warm": "Lamp", "solid": "Colour", "off": "Off"]
let lampColour = "#ffb46b"

/// The parts with LEDs a...b taken out, splitting a part they fall inside.
func cutOut(_ zones: [Zone], _ a: Int, _ b: Int) -> [Zone] {
    zones.flatMap { zone -> [Zone] in
        if zone.end < a || zone.start > b { return [zone] }
        var pieces: [Zone] = []
        if zone.start < a { var left = zone; left.end = a - 1; pieces.append(left) }
        if zone.end > b { var right = zone; right.start = b + 1; pieces.append(right) }
        return pieces
    }
}

/// The parts after giving LEDs a...b a mode; "sync" hands them back to the scene.
func withPart(_ zones: [Zone], _ a: Int, _ b: Int, _ part: Zone) -> [Zone] {
    var rest = cutOut(zones, a, b)
    if part.mode != "sync" {
        var placed = part
        placed.start = a
        placed.end = b
        rest.append(placed)
    }
    return rest.sorted { $0.start < $1.start }
}

/// What a part shows on the preview: its colour, the lamp's warm white, or nothing.
func partTint(_ zone: Zone) -> String? {
    switch zone.mode {
    case "solid": return zone.color.hasPrefix("#") ? zone.color : "#" + zone.color
    case "warm": return lampColour
    default: return nil
    }
}
