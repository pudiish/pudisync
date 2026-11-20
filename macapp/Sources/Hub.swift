import Foundation
import SwiftUI

/// One look of yours as the hub stores it. Only its description is read; the settings it holds are the hub's business.
struct LookInfo: Decodable {
    var note: String?
}

/// The reply to an action that answers with less than the full status.
struct Ack: Decodable {
    var ok: Bool?
}

struct MouseInfo: Decodable, Equatable {
    var connected: Bool
    var name: String?
    var permission: String?
}

struct Safety: Decodable, Equatable {
    var powerMa: Int?
}

/// The subset of /api/status the app shows.
struct HubStatus: Decodable {
    var running: Bool
    var mode: String
    var outputFps: Double?
    var brightness: Double
    var saturation: Double
    var immersion: Double
    var darkBoost: Double?
    var adaptive: Double?
    var hueBalance: Double?
    var blend: Double
    var profile: String
    var userProfiles: [String: LookInfo]
    var mouseSync: Bool
    var mouse: MouseInfo?
    var preview: [String]
    var ledCount: Int
    var layout: String
    var edgeBottom: Int
    var edgeRight: Int
    var edgeTop: Int
    var edgeLeft: Int
    var screenMmW: Double
    var screenMmH: Double
    var zones: [Zone]
    var wledReachable: Bool?
    var wledIp: String
    // Optional, so an older hub still decodes.
    var wledBri: Int?
    var wledOn: Bool?
    var wledFx: Int?
    var wledCol: String?
    var whiteKelvin: Double?
    var contrast: Double?
    var spread: Double?
    var colourWeight: Double?
    var colourMatch: Bool?
    var wbR: Double?
    var wbG: Double?
    var wbB: Double?
    var boardUrls: [String]?
    var bedLeds: Int?
    var bedPin: Int?
    var bedMode: String?
    var bedLook: String?
    var bedSide: String?
    var bedColour: String?
    var bedEffect: Int?
    var bedSpeed: Double?
    var bedBrightness: Double?
    var bedPowerMa: Int?
    var bedReverse: Bool?
    var safety: Safety?
    var lastError: String?
    var audioReact: String?
    var trippy: Bool?
    var calm: Bool?

    var syncing: Bool { mode == "sync" }
    var scene: LightScene { sceneOf(mode: mode, running: running, wledOn: wledOn, wledFx: wledFx) }
    var edges: [Int] { [edgeBottom, edgeRight, edgeTop, edgeLeft] }
    var myProfiles: [String] { userProfiles.keys.sorted { $0.localizedStandardCompare($1) == .orderedAscending } }

    /// What the person wrote about one of their looks, if anything.
    func note(of name: String) -> String? {
        guard let note = userProfiles[name]?.note, !note.isEmpty else { return nil }
        return note
    }

    func number(_ key: String) -> Double? {
        switch key {
        case "brightness": return brightness
        case "saturation": return saturation
        case "immersion": return immersion
        case "dark_boost": return darkBoost
        case "adaptive": return adaptive
        case "hue_balance": return hueBalance
        case "blend": return blend
        case "white_kelvin": return whiteKelvin
        case "contrast": return contrast
        case "spread": return spread
        case "colour_weight": return colourWeight
        case "wb_r": return wbR
        case "wb_g": return wbG
        case "wb_b": return wbB
        case "bed_brightness": return bedBrightness
        case "bed_speed": return bedSpeed
        case "master": return wledBri.map(Double.init)
        default: return nil
        }
    }
}

struct Effect: Decodable, Hashable {
    var fx: Int
    var name: String
    var about: String?
    var speed: Int?
    var intensity: Int?
}

struct EffectList: Decodable {
    var effects: [Effect]
}

struct Camera: Decodable, Hashable {
    var name: String
    var builtin: Bool
}

struct CameraJob: Decodable {
    var state: String
    var task: String?
    var camera: String?
    var log: [String]
    var cameras: [Camera]?
}

struct HubError: LocalizedError {
    let message: String
    var kind: HubFailure = .failed
    var errorDescription: String? { message }
}

/// One QR code another device can scan, from /api/pair.
struct PairTarget: Decodable, Hashable {
    var label: String
    var url: String
    var modules: [String]
}

struct PairList: Decodable {
    var targets: [PairTarget]
}

/// Plain GETs against the hub's HTTP API; every action is a URL there.
struct HubClient {
    var address: String
    var pin: String

    func get<T: Decodable>(_ path: String, _ query: [String: String] = [:]) async throws -> T {
        guard var parts = URLComponents(string: "http://\(address)\(path)") else {
            throw HubError(message: "The hub address \(address) is not valid.")
        }
        if !query.isEmpty {
            parts.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = parts.url else { throw HubError(message: "The hub address \(address) is not valid.") }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 6)
        if !pin.isEmpty { request.setValue(pin, forHTTPHeaderField: "X-Ambilight-Pin") }
        let data: Data, response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: request)
        } catch {
            throw HubError(message: "Can't reach the hub at \(address). Is it running?", kind: .unreachable)
        }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 403 { throw HubError(message: "The hub has a PIN. Enter it in Settings.", kind: .locked) }
        if code == 429 {
            throw HubError(message: "Too many wrong PINs from this Mac. Wait a minute, then try again.", kind: .throttled)
        }
        if !(200..<300).contains(code) {
            let said = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            throw HubError(message: said ?? "The hub answered \(code).")
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: data)
    }
}

@MainActor
final class HubStore: ObservableObject {
    static let builtinProfiles = [("vivid", "Vivid"), ("immersive", "Immersive")]
    /// The same words as the phone page's STYLES (ambilight/web/app.js).
    static let styleHints = ["vivid": "The default. Deep shadows, full highlights and a warm, rich picture; a light wash still lights the room in dark scenes.",
                             "immersive": "Fills the room with the scene's colour: gentler contrast, a cooler white, a wide wash that lights the walls in dark scenes, and a glide that quickens in action."]
    /// How long a click shows before the hub's reply has to agree with it.
    static let holdSeconds = 2.0

    @Published private(set) var status: HubStatus?
    @Published private(set) var link = LinkState.connecting
    @Published var problem: String?
    @Published private(set) var pairing: [PairTarget] = []
    @Published var showingPair = false
    @Published private(set) var effects: [Effect] = []
    @Published private(set) var cameras: [Camera] = []
    @Published private(set) var camera: CameraJob?
    /// A scene clicked a moment ago, shown until the hub agrees or the hold runs out.
    @Published private(set) var heldScene: (scene: LightScene, at: Date)?
    @Published private(set) var lastColour = swatches[0].hex
    @Published private(set) var lastEffect: Int?
    @Published var effectSpeed = 128.0
    @Published var effectIntensity = 128.0
    @Published var address: String {
        didSet { UserDefaults.standard.set(address, forKey: "hubAddress") }
    }
    @Published var pin: String {
        didSet { UserDefaults.standard.set(pin, forKey: "hubPin") }
    }

    private var pending: [String: Task<Void, Never>] = [:]
    private var lastSent: [String: Date] = [:]
    private var cameraPoll: Task<Void, Never>?
    /// Requests are numbered so a slow reply never overwrites a newer one.
    private var asked = 0
    private var shown = 0

    init() {
        address = UserDefaults.standard.string(forKey: "hubAddress") ?? "127.0.0.1:8080"
        pin = UserDefaults.standard.string(forKey: "hubPin") ?? ""
        Task { await self.poll() }
    }

    private var client: HubClient { HubClient(address: address, pin: pin) }

    var online: Bool { link == .online }

    /// What every light shows, counting a click the hub has not caught up with yet.
    var scene: LightScene {
        guard let status else { return .off }
        if let held = heldScene, held.scene != status.scene, Date().timeIntervalSince(held.at) < Self.holdSeconds {
            return held.scene
        }
        return status.scene
    }

    func effectName(_ fx: Int?) -> String {
        guard let fx else { return "Effect" }
        return effects.first { $0.fx == fx }?.name ?? "Effect \(fx)"
    }

    private func poll() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(for: .seconds(1))
        }
    }

    private func show(_ next: HubStatus, asked number: Int) {
        guard number > shown else { return }
        shown = number
        status = next
        if let held = heldScene, held.scene == next.scene || Date().timeIntervalSince(held.at) >= Self.holdSeconds {
            heldScene = nil
        }
        if next.scene == .colour, let col = next.wledCol { lastColour = "#" + col }
        if next.scene == .effect, let fx = next.wledFx { lastEffect = fx }
    }

    func refresh() async {
        asked += 1
        let number = asked
        do {
            let next: HubStatus = try await client.get("/api/status")
            show(next, asked: number)
            if !online { problem = nil }
            link = link.after(nil, at: Date())
            if effects.isEmpty { loadEffects() }
        } catch {
            let kind = (error as? HubError)?.kind ?? .failed
            if kind == .failed { report(error) }    // the hub answered, but not with a status
            link = link.after(kind, at: Date())
        }
    }

    /// The QR codes for pairing a phone or tablet.
    func loadPairing() {
        Task {
            do {
                let list: PairList = try await client.get("/api/pair")
                pairing = list.targets
            } catch {
                report(error)
            }
        }
    }

    /// Show an action's error for a few seconds; polls keep the offline state separately.
    private func report(_ error: Error) {
        let message = error.localizedDescription
        problem = message
        Task {
            try? await Task.sleep(for: .seconds(6))
            if problem == message { problem = nil }
        }
    }

    /// Run an action whose answer is the hub's status.
    private func act(_ path: String, _ query: [String: String] = [:]) {
        asked += 1
        let number = asked
        Task {
            do {
                show(try await client.get(path, query), asked: number)
                problem = nil
            } catch {
                report(error)
            }
        }
    }

    /// Runs `action` at once, then at most every 150 ms while a control is dragged, and always the last value.
    private func live(_ key: String, final: Bool, _ action: @escaping () -> Void) {
        pending[key]?.cancel()
        let wait = 0.15 - Date().timeIntervalSince(lastSent[key] ?? .distantPast)
        if final || wait <= 0 {
            lastSent[key] = Date()
            action()
            return
        }
        pending[key] = Task {
            try? await Task.sleep(for: .seconds(wait))
            guard !Task.isCancelled else { return }
            lastSent[key] = Date()
            action()
        }
    }

    private func hold(_ scene: LightScene) { heldScene = (scene, Date()) }

    // MARK: every light at once

    func togglePower() {
        if scene == .off { startSync() } else { lightsOff() }
    }

    func startSync() {
        hold(.screen)
        act("/api/sync/on")
    }

    /// Every light off. Power alone would leave sync chosen, and its stream would light the strip again.
    func lightsOff() {
        hold(.off)
        act("/api/lights/off")
    }

    func pick(_ scene: LightScene) {
        switch scene {
        case .screen: startSync()
        case .colour: runColour(lastColour)
        case .effect:
            if let fx = lastEffect ?? effects.first?.fx { runEffect(fx) }
            else { problem = "Effects are still loading." }
        case .off: lightsOff()
        }
    }

    /// A solid colour on every light; `final` is false while the wheel is still being dragged.
    func runColour(_ hex: String, final: Bool = true) {
        lastColour = hex
        hold(.colour)
        live("colour", final: final) { self.act("/api/effect/run", ["fx": "0", "color": String(hex.dropFirst())]) }
    }

    func runEffect(_ fx: Int) {
        if fx != lastEffect, let effect = effects.first(where: { $0.fx == fx }) {
            effectSpeed = Double(effect.speed ?? 128)
            effectIntensity = Double(effect.intensity ?? 128)
        }
        lastEffect = fx
        hold(.effect)
        act("/api/effect/run", ["fx": String(fx), "speed": String(Int(effectSpeed)), "intensity": String(Int(effectIntensity)),
                                "color": String(lastColour.dropFirst())])
    }

    /// Speed or intensity moved: run the effect again with them, at most every 150 ms.
    func retuneEffect() {
        guard scene == .effect, let fx = lastEffect else { return }
        live("effect", final: false) { self.runEffect(fx) }
    }

    /// The board's own brightness: every light, whatever it shows.
    func setMasterBrightness(_ value: Double, final: Bool) {
        live("master", final: final) {
            Task {
                do {
                    let _: Ack = try await self.client.get("/api/wled/brightness", ["value": String(Int(value.rounded()))])
                    await self.refresh()
                } catch {
                    self.report(error)
                }
            }
        }
    }

    func setSync(_ on: Bool) { on ? startSync() : lightsOff() }

    func set(_ key: String, _ value: String) { act("/api/set", [key: value]) }

    /// Slider moves arrive many times a second; only the last one in a short pause is sent.
    func send(_ key: String, _ value: Double) {
        setLater(key, String(format: "%.3f", value))
    }

    /// Same pause for values that are not numbers.
    func setLater(_ key: String, _ value: String) {
        pending[key]?.cancel()
        pending[key] = Task {
            try? await Task.sleep(for: .milliseconds(120))
            guard !Task.isCancelled else { return }
            set(key, value)
        }
    }

    /// A style is a way of following the screen, so picking one starts sync too.
    func pickStyle(_ name: String) {
        apply(profile: name)
        if status?.running != true { startSync() }
    }

    func apply(profile name: String) {
        act("/api/profile/\(name.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? name)")
    }

    /// A description is sent only when there is one, so saving over a look keeps what it said.
    func saveProfile(_ name: String, note: String = "") {
        act("/api/profiles/save", note.isEmpty ? ["name": name] : ["name": name, "note": note])
    }

    func describeProfile(_ name: String, note: String) { act("/api/profiles/describe", ["name": name, "note": note]) }

    func deleteProfile(_ name: String) { act("/api/profiles/delete", ["name": name]) }

    func loadEffects() {
        guard effects.isEmpty else { return }
        Task {
            do {
                let list: EffectList = try await client.get("/api/effects")
                effects = list.effects
            } catch {
                report(error)
            }
        }
    }

    // MARK: bed strip

    /// Gives the board its second output for the bed strip, then restarts it.
    func setupBed(leds: String, amps: String, pin: Int) {
        act("/api/bed/setup", ["leds": leds, "amps": amps, "pin": String(pin)])
    }

    // MARK: parts of the monitor strip

    var parts: [Zone] { status?.zones ?? [] }

    func setParts(_ zones: [Zone]) {
        guard let data = try? JSONEncoder().encode(zones), let json = String(data: data, encoding: .utf8) else { return }
        set("zones", json)
    }

    // MARK: webcam calibration

    func loadCameras() {
        Task {
            do {
                let job: CameraJob = try await client.get("/api/camera/list")
                cameras = job.cameras ?? []
                follow(job)
            } catch {
                report(error)
            }
        }
    }

    func startCamera(task: String, camera name: String) {
        Task {
            do {
                let job: CameraJob = try await client.get("/api/camera/start",
                                                          ["task": task, "camera": name, "seconds": "60"])
                follow(job)
            } catch {
                report(error)
            }
        }
    }

    private func follow(_ job: CameraJob) {
        camera = job
        cameraPoll?.cancel()
        guard job.state == "working" else { return }
        cameraPoll = Task {
            try? await Task.sleep(for: .seconds(1))
            guard !Task.isCancelled, let next: CameraJob = try? await client.get("/api/camera/status") else { return }
            follow(next)
        }
    }
}
