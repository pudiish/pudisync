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

print("Connection to the hub")
let t0 = Date(timeIntervalSince1970: 1_000_000)
check("a hub that answers is online", LinkState.connecting.after(nil, at: t0) == .online)
check("an error answer still means the hub is reachable", LinkState.online.after(.failed, at: t0) == .online)
check("a PIN refusal is not reported as the hub being down",
      LinkState.online.after(.locked, at: t0) == .locked
      && LinkState.locked.banner(address: "127.0.0.1:8080", now: t0)?.contains("PIN") == true
      && LinkState.locked.banner(address: "127.0.0.1:8080", now: t0)?.contains("Can't reach") == false)
check("a PIN lockout says to wait", LinkState.online.after(.throttled, at: t0)
      .banner(address: "x", now: t0)?.contains("Wait a minute") == true)
let quiet = LinkState.online.after(.unreachable, at: t0)
check("going quiet records when", quiet == .offline(since: t0))
check("staying quiet keeps the first time", quiet.after(.unreachable, at: t0.addingTimeInterval(30)) == quiet)
check("the banner says for how long",
      quiet.banner(address: "127.0.0.1:8080", now: t0.addingTimeInterval(12))?.contains("for 12 s") == true,
      quiet.banner(address: "127.0.0.1:8080", now: t0.addingTimeInterval(12)) ?? "")
check("...in minutes after a while", LinkState.ago(185) == "for 3 min" && LinkState.ago(2) == "just now")
check("the hub coming back clears it", quiet.after(nil, at: t0.addingTimeInterval(40)) == .online
      && LinkState.online.banner(address: "x", now: t0) == nil)

print("Pairing codes")
check("a square of 0s and 1s reads", QRMatrix(rows: ["101", "010", "111"])?.dark[1] == [false, true, false])
check("a ragged or foreign matrix is refused",
      QRMatrix(rows: ["10", "0"]) == nil && QRMatrix(rows: ["1x", "01"]) == nil && QRMatrix(rows: []) == nil)

print("\n\(passed) passed, \(failed.count) failed")
exit(failed.isEmpty ? 0 : 1)
