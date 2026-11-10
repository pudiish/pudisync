import Foundation

/// One value in an Ambient preset, as /api/ambient sends it: a number or a word.
enum PresetValue: Decodable, Equatable {
    case number(Double)
    case text(String)

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let number = try? value.decode(Double.self) {
            self = .number(number)
        } else {
            self = .text(try value.decode(String.self))
        }
    }

    /// The value as /api/set takes it.
    var query: String {
        switch self {
        case .number(let number): return String(number)
        case .text(let text): return text
        }
    }
}

/// A named choice that sets settings, such as how closely the bed follows (ambilight/ambient.py).
struct AmbientPreset: Decodable, Equatable {
    var name: String
    var label: String
    var about: String
    var set: [String: PresetValue]

    var query: [String: String] { self.set.mapValues(\.query) }
}

/// A way of following the screen: one of the hub's built-in profiles, applied by name.
struct AmbientMode: Decodable, Equatable {
    var name: String
    var label: String
    var about: String
}

/// A light the board runs for the room while the screen is not followed.
struct AmbientGlow: Decodable, Equatable {
    var name: String
    var label: String
    var about: String
    var fx: Int
    var color: String?
    var speed: Int
    var intensity: Int
}

/// Everything the Ambient page offers, from GET /api/ambient.
struct AmbientTables: Decodable, Equatable {
    var modes: [AmbientMode]
    var glows: [AmbientGlow]
    var bed: [AmbientPreset]
}

/// True while every setting of `preset` still has its value: numbers to within
/// 0.005, as on the phone page. A setting the app cannot read never matches.
func ambientMatches(_ preset: AmbientPreset, current: (String) -> PresetValue?) -> Bool {
    preset.set.allSatisfy { key, want in
        switch (want, current(key)) {
        case let (.number(a), .number(b)?): return abs(a - b) < 0.005
        case let (.text(a), .text(b)?): return a == b
        default: return false
        }
    }
}

/// How long the light takes to settle on a new colour at a Response ("blend")
/// setting, as the hub's smoothing.settle_seconds and the phone page work it out.
func settleSeconds(blend: Double) -> Double {
    0.06 * pow(0.8 / 0.06, 1 - blend)
}
