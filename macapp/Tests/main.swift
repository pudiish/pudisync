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

print("The scene every light shows")
check("sync, a plain colour, an effect and off are told apart",
      [sceneOf(mode: "sync", running: true, wledOn: true, wledFx: 0), sceneOf(mode: "effect", running: false, wledOn: true, wledFx: 0),
       sceneOf(mode: "effect", running: false, wledOn: true, wledFx: 63), sceneOf(mode: "off", running: false, wledOn: false, wledFx: 0)]
      == [.screen, .colour, .effect, .off])
check("an older hub without a mode is read from whether it runs",
      sceneOf(mode: "", running: true, wledOn: nil, wledFx: nil) == .screen
      && sceneOf(mode: "", running: false, wledOn: false, wledFx: nil) == .off)
check("the hero names what is showing",
      heroView(online: true, boardReachable: true, scene: .colour, colour: "ff2010", effect: "").state == "Red"
      && heroView(online: true, boardReachable: true, scene: .effect, colour: nil, effect: "Pride").state == "Pride"
      && heroView(online: true, boardReachable: true, scene: .screen, colour: nil, effect: "").state == "Following your screen")
check("an unreachable hub is never shown as lights on",
      heroView(online: false, boardReachable: true, scene: .screen, colour: nil, effect: "") ==
      HeroView(glow: .off, state: "Hub offline", hint: "Waiting for the hub to answer."))
check("an unreachable board says so instead of 'Off'",
      heroView(online: true, boardReachable: false, scene: .off, colour: nil, effect: "").state == "Can't reach the board"
      && heroView(online: true, boardReachable: false, scene: .screen, colour: nil, effect: "").glow == .fault)
let beds = [("screen", LightScene.screen), ("screen", .colour), ("screen", .off), ("colour", .screen), ("off", .screen)]
    .map { bedView(leds: 120, mode: $0.0, look: "mood", effect: "Rainbow", scene: $0.1) }
check("the bed says when it matches the scene, and hides a brightness that would do nothing",
      beds.map(\.state) == ["Matches · follows the screen", "Matches · same colour", "Matches · off with everything",
                            "Its own colour", "Off"] && beds.map(\.brightness) == [true, false, false, true, false],
      beds.map(\.state))
check("a missing bed strip says it is not set up", bedView(leds: 0, mode: "screen", look: "", effect: "", scene: .screen).state == "Not set up")

print("The colour wheel")
let round = ["#80ff00", "#00ffff", "#2040ff", "#ffb46b", "#ffffff", "#000000"].map {
    RGB(hex: $0).map { c in RGB(hue: c.hsv.h, saturation: c.hsv.s, value: c.hsv.v).hex } ?? "" }
check("any colour reads back exactly as it was given", round == ["#80ff00", "#00ffff", "#2040ff", "#ffb46b", "#ffffff", "#000000"], round)
check("red at the top, then round clockwise, white at the centre",
      [Wheel.pick(x: 0, y: -1, whites: false).hex, Wheel.pick(x: 1, y: 0, whites: false).hex,
       Wheel.pick(x: 0, y: 1, whites: false).hex, Wheel.pick(x: 0, y: 0, whites: false).hex] == ["#ff0000", "#80ff00", "#00ffff", "#ffffff"])
check("a touch past the rim still gives the full colour", Wheel.pick(x: 1.4, y: 0, whites: false).hex == "#80ff00")
check("Whites run from daylight at the top to candlelight at the bottom",
      Wheel.pick(x: 0, y: -1, whites: true).hex == "#fffefa" && Wheel.pick(x: 0, y: 1, whites: true).hex == "#ff9227",
      [Wheel.pick(x: 0, y: -1, whites: true).hex, Wheel.pick(x: 0, y: 1, whites: true).hex])
check("the same 2200 K and 6500 K as the phone page", RGB(kelvin: 2200).hex == "#ff9227" && RGB(kelvin: 6500).hex == "#fffefa")
check("a white is known by its temperature, a colour is not", kelvinOf(RGB(kelvin: 4000).hex) == 4000 && kelvinOf("#00ffff") == nil)
let spot = Wheel.spot("#00ffff", whites: false)
check("a colour's thumb sits where picking it would", abs(spot.x) < 1e-9 && abs(spot.y - 1) < 1e-9, spot)
check("the arrow keys turn the hue a little", Wheel.step("#00ffff", whites: false, across: -1, up: 0) == "#00ffea")
check("...or warm a white", kelvinOf(Wheel.step(RGB(kelvin: 4000).hex, whites: true, across: -1, up: 0)) == 3900)
check("a picked shade gets a plain name",
      ["#ff2010", "#80ff00", "#00ffff", "#ff80ff", "#f2f2f2", "#ff6a00", "#c0ffc0", "#ff9227", "#fffefa"].map(colourName)
      == ["Red", "Lime", "Cyan", "Magenta", "White", "Orange", "Pale green", "Warm white", "Daylight"])
check("recent picks keep the newest first, without repeats, six at most",
      remembering("#b", in: ["#a", "#b", "#c", "#d", "#e", "#f"]) == ["#b", "#a", "#c", "#d", "#e", "#f"]
      && remembering("#g", in: ["#a", "#b", "#c", "#d", "#e", "#f"]).count == 6)

print("Parts of the strip")
let warm = Zone(start: 0, end: 9, mode: "warm", color: "", brightness: 1, keep: true)
let dark = Zone(start: 20, end: 29, mode: "off", color: "", brightness: 1, keep: true)
check("a selection takes its LEDs out of the parts it crosses",
      cutOut([warm, dark], 5, 24).map { [$0.start, $0.end] } == [[0, 4], [25, 29]])
let long = Zone(start: 0, end: 30, mode: "warm", color: "", brightness: 1, keep: true)
check("...splitting a part it falls inside", cutOut([long], 10, 19).map { [$0.start, $0.end] } == [[0, 9], [20, 30]])
let red = Zone(start: 0, end: 0, mode: "solid", color: "#ff0000", brightness: 1, keep: true)
check("giving a stretch a colour leaves the lamp on both sides",
      withPart([long], 10, 19, red).map { "\($0.start)-\($0.end) \($0.mode)" } == ["0-9 warm", "10-19 solid", "20-30 warm"])
var scene = red
scene.mode = "sync"
check("handing a stretch back to the scene leaves no part there",
      withPart([long], 10, 19, scene).map { [$0.start, $0.end] } == [[0, 9], [20, 30]])
check("a part shows its own colour, the lamp's warm white, or nothing",
      [partTint(red), partTint(long), partTint(dark)] == ["#ff0000", lampColour, nil])

// looks
let builtinWords = ["vivid": "Deep shadows.", "immersive": "Fills the room."]
check("a built-in look says its own words",
      lookHint(active: "vivid", builtin: builtinWords, mine: ["Day"], note: nil) == "Deep shadows.")
check("one of yours says what you wrote",
      lookHint(active: "Day", builtin: builtinWords, mine: ["Day"], note: "Bright and plain") == "Bright and plain")
check("...or how to write it when you have not",
      lookHint(active: "Day", builtin: builtinWords, mine: ["Day"], note: nil).contains("Describe")
      && lookHint(active: "Day", builtin: builtinWords, mine: ["Day"], note: "").contains("Describe"))
check("a look tuned by hand says to save it",
      lookHint(active: "", builtin: builtinWords, mine: ["Day"], note: nil).contains("Save it as a look"))

print("\n\(passed) passed, \(failed.count) failed")
exit(failed.isEmpty ? 0 : 1)
