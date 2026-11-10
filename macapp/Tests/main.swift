// The app's pure logic, checked without Xcode: ./build.sh test
import CoreGraphics
import Foundation

var passed = 0
var failed: [String] = []

func check(_ name: String, _ condition: Bool, _ detail: Any = "") {
    if condition {
        passed += 1
        print("  [PASS] \(name)")
    } else {
        failed.append(name)
        print("  [FAIL] \(name)  -- \(detail)")
    }
}

print("LED frame layout")
let counts = [35, 14, 35, 13]
let rect = CGRect(x: 0, y: 0, width: 600, height: 340)
let frame = LEDLayout.frame(counts: counts, in: rect)
check("every LED of the strip is placed", frame.leds.count == 97, frame.leds.count)
let first = frame.leds[0].point, lastBottom = frame.leds[34].point, firstRight = frame.leds[35].point
check("LED 0 sits at the bottom left", first.x < 40 && first.y > 300, first)
check("the bottom edge runs left to right", lastBottom.x > 560 && abs(lastBottom.y - first.y) < 0.01, lastBottom)
check("the right edge starts at the bottom and climbs", firstRight.x > 560 && firstRight.y > frame.leds[48].point.y)
check("the top edge runs right to left", frame.leds[49].point.x > frame.leds[83].point.x)
check("the left edge runs top to bottom", frame.leds[84].point.y < frame.leds[96].point.y)
check("the screen sits inside the LEDs", rect.contains(frame.screen) && frame.screen.minX > first.x)
check("pointing at an LED picks it", frame.nearest(to: frame.leds[20].point) == 20)
check("pointing at the middle of the screen picks nothing",
      frame.nearest(to: CGPoint(x: rect.midX, y: rect.midY)) == nil)
let strip = LEDLayout.strip(count: 60, in: rect)
check("a straight strip places every LED left to right",
      strip.leds.count == 60 && strip.leds[0].point.x < strip.leds[59].point.x)

print("Colours")
check("hex colours read and write back", RGB(hex: "#ff8000")?.hex == "#ff8000")
check("a hex without # reads too", RGB(hex: "20e050") == RGB(r: 32.0 / 255, g: 224.0 / 255, b: 80.0 / 255))
check("nonsense is refused", RGB(hex: "#zzz") == nil && RGB(hex: "#12345") == nil)
check("near black counts as off", RGB(hex: "#040301")?.isLit == false && RGB(hex: "#361d06")?.isLit == true)
check("preview swatches spread over the strip",
      swatchIndex(led: 0, ledCount: 97, swatches: 40) == 0 && swatchIndex(led: 96, ledCount: 97, swatches: 40) == 39)

print("Mouse zones")
var preview = [String](repeating: "#000000", count: 40)
for i in 0..<5 { preview[i] = "#ff0000" }
for i in 10..<14 { preview[i] = "#0000ff" }
let zones = mouseZones(preview: preview, ledCount: 97, bottomEdge: 35, frame: true)
check("three zones from the bottom edge", zones.count == 3)
check("the left zone takes the bottom left's colour", zones[0].r > 0.4 && zones[0].b == 0, zones)
check("the right zone takes the bottom right's colour", zones[2].b > 0.4 && zones[2].r == 0, zones)
check("no preview, no zones", mouseZones(preview: [], ledCount: 97, bottomEdge: 35, frame: true).isEmpty)

print("Ambient choices")
let tablesJSON = """
{"modes": [{"name": "movie", "label": "Cinema", "about": "Glides gently."},
           {"name": "game", "label": "Gaming", "about": "Follows the action."}],
 "glows": [{"name": "deep-sea", "label": "Deep sea", "about": "Blues.", "fx": 101, "color": null,
            "speed": 100, "intensity": 128},
           {"name": "ember", "label": "Ember", "about": "Reds.", "fx": 88, "color": "ff4010",
            "speed": 96, "intensity": 224}],
 "bed": [{"name": "exactly", "label": "Exactly", "about": "Edges.",
          "set": {"bed_mode": "screen", "bed_look": "side", "bed_side": "whole"}}]}
"""
let tables = try? JSONDecoder().decode(AmbientTables.self, from: Data(tablesJSON.utf8))
check("the hub's tables decode", tables?.modes.map(\.name) == ["movie", "game"] && tables?.glows.count == 2,
      String(describing: tables))
let exactly = tables!.bed[0]
check("a glow without a colour decodes", tables!.glows[0].color == nil && tables!.glows[1].color == "ff4010")
check("words decode", exactly.set["bed_look"] == .text("side"))
check("a choice becomes one /api/set query", exactly.query == ["bed_mode": "screen", "bed_look": "side", "bed_side": "whole"],
      exactly.query)
var current: [String: PresetValue] = ["bed_mode": .text("screen"), "bed_look": .text("side"), "bed_side": .text("whole"),
                                      "bed_brightness": .number(0.6)]
check("a choice is lit while its settings match", ambientMatches(exactly) { current[$0] })
current["bed_side"] = .text("bottom")
check("one word off and it no longer matches", !ambientMatches(exactly) { current[$0] })
let dim = AmbientPreset(name: "dim", label: "Dim", about: "", set: ["bed_brightness": .number(0.6)])
check("a number in a choice reads back as a number", dim.query == ["bed_brightness": "0.6"], dim.query)
current["bed_brightness"] = .number(0.604)
check("numbers match to within 0.005, as on the phone", ambientMatches(dim) { current[$0] })
current["bed_brightness"] = .number(0.65)
check("a moved fader means it no longer matches", !ambientMatches(dim) { current[$0] })
check("a setting the app cannot read never matches", !ambientMatches(exactly) { _ in nil })
check("Cinema's 0.3 settles in 0.37 s and Gaming's 0.7 in 0.13 s, as the hub works it out",
      abs(settleSeconds(blend: 0.3) - 0.37) < 0.005 && abs(settleSeconds(blend: 0.7) - 0.13) < 0.005,
      (settleSeconds(blend: 0.3), settleSeconds(blend: 0.7)))

print("\n\(passed) passed, \(failed.count) failed")
exit(failed.isEmpty ? 0 : 1)
