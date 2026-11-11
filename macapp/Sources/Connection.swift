import Foundation

/// How a request to the hub failed, so each case is explained differently.
enum HubFailure: Equatable {
    case unreachable, locked, throttled, failed
}

/// What the app knows about its link to the hub.
enum LinkState: Equatable {
    case connecting
    case online
    case locked
    case throttled
    case offline(since: Date)

    /// The state after a poll; `nil` means the hub answered.
    func after(_ failure: HubFailure?, at now: Date) -> LinkState {
        switch failure {
        case nil, .failed?:
            return .online      // the hub answered, even if with an error
        case .locked?:
            return .locked
        case .throttled?:
            return .throttled
        case .unreachable?:
            if case .offline = self { return self }     // keep when it went quiet
            return .offline(since: now)
        }
    }

    /// The banner to show, or nil when there is nothing to say.
    func banner(address: String, now: Date) -> String? {
        switch self {
        case .connecting, .online:
            return nil
        case .locked:
            return "The hub has a PIN. Enter it in Ambilight Hub ▸ Settings."
        case .throttled:
            return "Too many wrong PINs from this Mac. Wait a minute, then try again."
        case .offline(let since):
            return "Can't reach the hub at \(address) · \(LinkState.ago(now.timeIntervalSince(since))). "
                + "It starts at login; check it is running, or change the address in Settings. Retrying."
        }
    }

    static func ago(_ seconds: TimeInterval) -> String {
        let s = Int(seconds.rounded())
        if s < 5 { return "just now" }
        if s < 60 { return "for \(s) s" }
        if s < 3600 { return "for \(Int((Double(s) / 60).rounded())) min" }
        return "for \(Int((Double(s) / 3600).rounded())) h"
    }
}

/// A QR code as the hub sends it: rows of "1" (dark) and "0" (light).
struct QRMatrix: Equatable {
    let dark: [[Bool]]
    var size: Int { dark.count }

    /// Nil unless the rows form a square of 0s and 1s.
    init?(rows: [String]) {
        guard !rows.isEmpty, rows.allSatisfy({ $0.count == rows.count && $0.allSatisfy { $0 == "0" || $0 == "1" } })
        else { return nil }
        dark = rows.map { $0.map { $0 == "1" } }
    }
}
