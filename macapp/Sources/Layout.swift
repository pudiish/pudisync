import CoreGraphics
import Foundation

/// Where each LED sits when the strip is drawn round the screen, in the hub's
/// LED order: bottom left to right, right bottom to top, top right to left,
/// left top to bottom (processing.frame_colors). y grows downward.
struct LEDLayout {
    struct LED {
        let index: Int
        let point: CGPoint
    }

    let leds: [LED]
    let size: CGFloat
    let screen: CGRect

    static func frame(counts: [Int], in rect: CGRect) -> LEDLayout {
        let inset: CGFloat = 16
        let x0 = rect.minX + inset, y0 = rect.minY + inset
        let x1 = rect.maxX - inset, y1 = rect.maxY - inset
        let across = CGFloat(max(1, counts[0], counts[2])), down = CGFloat(max(1, counts[1], counts[3]))
        let size = min(max(min((x1 - x0) / across, (y1 - y0) / down) * 0.72, 3), 14)
        let sides: [(CGFloat) -> CGPoint] = [
            { t in CGPoint(x: x0 + t * (x1 - x0), y: y1) },
            { t in CGPoint(x: x1, y: y1 - t * (y1 - y0)) },
            { t in CGPoint(x: x1 - t * (x1 - x0), y: y0) },
            { t in CGPoint(x: x0, y: y0 + t * (y1 - y0)) },
        ]
        var leds: [LED] = []
        for (side, count) in counts.prefix(4).enumerated() where count > 0 {
            for k in 0..<count {
                leds.append(LED(index: leds.count, point: sides[side]((CGFloat(k) + 0.5) / CGFloat(count))))
            }
        }
        let screen = CGRect(x: x0 + 18, y: y0 + 18, width: max(8, x1 - x0 - 36), height: max(8, y1 - y0 - 36))
        return LEDLayout(leds: leds, size: size, screen: screen)
    }

    static func strip(count: Int, in rect: CGRect) -> LEDLayout {
        let n = max(1, count), span = rect.width - 24
        let size = min(max(span / CGFloat(n) * 0.72, 3), 14)
        let leds = (0..<n).map { i in
            LED(index: i, point: CGPoint(x: rect.minX + 12 + (CGFloat(i) + 0.5) / CGFloat(n) * span, y: rect.maxY - 16))
        }
        return LEDLayout(leds: leds, size: size, screen: CGRect(x: rect.minX + 12, y: rect.minY + 10,
                                                                 width: span, height: max(8, rect.height - 44)))
    }

    /// The LED under a pointer, if one is close enough to mean it.
    func nearest(to point: CGPoint) -> Int? {
        var best: LED?
        var bestDistance = CGFloat.infinity
        for led in leds {
            let distance = hypot(led.point.x - point.x, led.point.y - point.y)
            if distance < bestDistance {
                best = led
                bestDistance = distance
            }
        }
        return bestDistance <= max(24, size * 4) ? best?.index : nil
    }
}

/// The hub sends a few dozen preview swatches for the whole strip; LED `led`
/// shows the one covering its share of the strip.
func swatchIndex(led: Int, ledCount: Int, swatches: Int) -> Int {
    guard swatches > 0 else { return 0 }
    return min(swatches - 1, led * swatches / max(1, ledCount))
}

struct RGB: Equatable {
    var r: Double
    var g: Double
    var b: Double

    init(r: Double, g: Double, b: Double) {
        self.r = r
        self.g = g
        self.b = b
    }

    init?(hex: String) {
        var text = hex.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, let value = UInt32(text, radix: 16) else { return nil }
        r = Double(value >> 16 & 0xFF) / 255
        g = Double(value >> 8 & 0xFF) / 255
        b = Double(value & 0xFF) / 255
    }

    var hex: String {
        let byte = { (v: Double) in Int((min(max(v, 0), 1) * 255).rounded()) }
        return String(format: "#%02x%02x%02x", byte(r), byte(g), byte(b))
    }

    /// Dark enough to count as off on the wall (the web page's isLit rule).
    var isLit: Bool { (r * 0.299 + g * 0.587 + b * 0.114) * 255 > 14 }
}

/// What the mouse's left, middle and right zones show: the bottom edge's
/// thirds when the strip runs round the screen, else the whole strip's
/// (mouse.zone_colours on the hub).
func mouseZones(preview: [String], ledCount: Int, bottomEdge: Int, frame: Bool) -> [RGB] {
    let span = frame && bottomEdge >= 3 ? bottomEdge : ledCount
    guard span >= 3, !preview.isEmpty else { return [] }
    return (0..<3).map { third in
        let start = third * span / 3, end = (third + 1) * span / 3
        let colours = (start..<end).compactMap {
            RGB(hex: preview[swatchIndex(led: $0, ledCount: ledCount, swatches: preview.count)])
        }
        let count = Double(max(1, colours.count))
        return RGB(r: colours.map(\.r).reduce(0, +) / count, g: colours.map(\.g).reduce(0, +) / count,
                   b: colours.map(\.b).reduce(0, +) / count)
    }
}
