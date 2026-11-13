import Foundation
import SwiftUI

/// A value the app does not read, so its shape may change freely on the hub.
struct Ignored: Decodable {
    init(from decoder: Decoder) throws {}
}

struct Zone: Codable, Equatable, Hashable {
    var start: Int
    var end: Int
    var mode: String
    var color: String
    var brightness: Double
    var keep: Bool?
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
    var blend: Double
    var profile: String
    var userProfiles: [String: Ignored]
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
    // Optional, so an older hub without a bed strip still decodes.
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

    var syncing: Bool { mode == "sync" }
    var edges: [Int] { [edgeBottom, edgeRight, edgeTop, edgeLeft] }
    var myProfiles: [String] { userProfiles.keys.sorted { $0.localizedStandardCompare($1) == .orderedAscending } }

    func number(_ key: String) -> Double? {
        switch key {
        case "brightness": return brightness
        case "saturation": return saturation
        case "immersion": return immersion
        case "blend": return blend
        case "bed_brightness": return bedBrightness
        case "bed_speed": return bedSpeed
        default: return nil
        }
    }
}

struct Effect: Decodable, Hashable {
    var fx: Int
    var name: String
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
    static let builtinProfiles = [("movie", "Cinema"), ("game", "Gaming"), ("enriched", "Enriched")]

    @Published private(set) var status: HubStatus?
    @Published private(set) var link = LinkState.connecting
    @Published var problem: String?
    @Published private(set) var pairing: [PairTarget] = []
    @Published var showingPair = false
    @Published private(set) var effects: [Effect] = []
    @Published private(set) var cameras: [Camera] = []
    @Published private(set) var camera: CameraJob?
    @Published var address: String {
        didSet { UserDefaults.standard.set(address, forKey: "hubAddress") }
    }
    @Published var pin: String {
        didSet { UserDefaults.standard.set(pin, forKey: "hubPin") }
    }

    private var pending: [String: Task<Void, Never>] = [:]
    private var cameraPoll: Task<Void, Never>?

    init() {
        address = UserDefaults.standard.string(forKey: "hubAddress") ?? "127.0.0.1:8080"
        pin = UserDefaults.standard.string(forKey: "hubPin") ?? ""
        Task { await self.poll() }
    }

    private var client: HubClient { HubClient(address: address, pin: pin) }

    var online: Bool { link == .online }

    private func poll() async {
        while !Task.isCancelled {
            await refresh()
            try? await Task.sleep(for: .seconds(1))
        }
    }

    func refresh() async {
        do {
            status = try await client.get("/api/status")
            if !online { problem = nil }
            link = link.after(nil, at: Date())
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
        Task {
            do {
                status = try await client.get(path, query)
                problem = nil
            } catch {
                report(error)
            }
        }
    }

    func setSync(_ on: Bool) { act(on ? "/api/sync/on" : "/api/sync/off") }

    func set(_ key: String, _ value: String) { act("/api/set", [key: value]) }

    /// Slider moves arrive many times a second; only the last one in a short
    /// pause is sent.
    func send(_ key: String, _ value: Double) {
        setLater(key, String(format: "%.3f", value))
    }

    /// Same pause for values that are not numbers, such as a colour being dragged.
    func setLater(_ key: String, _ value: String) {
        pending[key]?.cancel()
        pending[key] = Task {
            try? await Task.sleep(for: .milliseconds(120))
            guard !Task.isCancelled else { return }
            set(key, value)
        }
    }

    func apply(profile name: String) {
        act("/api/profile/\(name.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? name)")
    }

    func saveProfile(_ name: String) { act("/api/profiles/save", ["name": name]) }

    func deleteProfile(_ name: String) { act("/api/profiles/delete", ["name": name]) }

    // MARK: bed strip

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

    /// Gives the board its second output for the bed strip, then restarts it.
    func setupBed(leds: String, amps: String, pin: Int) {
        act("/api/bed/setup", ["leds": leds, "amps": amps, "pin": String(pin)])
    }

    // MARK: locked colours (solid zones on the hub)

    var locks: [(offset: Int, zone: Zone)] {
        (status?.zones ?? []).enumerated().filter { $0.element.mode == "solid" }.map { ($0.offset, $0.element) }
    }

    private func sendZones(_ zones: [Zone]) {
        guard let data = try? JSONEncoder().encode(zones), let json = String(data: data, encoding: .utf8) else { return }
        set("zones", json)
    }

    func lock(_ range: ClosedRange<Int>, colour: RGB, brightness: Double, keep: Bool) {
        var zones = status?.zones ?? []
        zones.append(Zone(start: range.lowerBound, end: range.upperBound, mode: "solid", color: colour.hex,
                          brightness: brightness, keep: keep))
        sendZones(zones)
    }

    func unlock(at offset: Int) {
        var zones = status?.zones ?? []
        guard zones.indices.contains(offset) else { return }
        zones.remove(at: offset)
        sendZones(zones)
    }

    func unlockAll() { sendZones((status?.zones ?? []).filter { $0.mode != "solid" }) }

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
