import AppKit
import SwiftUI

/// A QR code a phone or tablet scans to open the hub, or the strip's own page.
struct PairSheet: View {
    @EnvironmentObject private var store: HubStore
    @Environment(\.dismiss) private var dismiss
    @State private var choice = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add a phone or tablet").font(.system(size: 17, weight: .semibold))
            Text("Point the other device's camera at the code and open the link it offers. It must be on the "
                 + "same Wi-Fi. If the hub has a PIN, the new device asks for it once.")
                .font(.system(size: 12)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let target = store.pairing.indices.contains(choice) ? store.pairing[choice] : store.pairing.first {
                Picker("Open", selection: $choice) {
                    ForEach(Array(store.pairing.enumerated()), id: \.offset) { index, item in
                        Text(item.label).tag(index)
                    }
                }
                if let matrix = QRMatrix(rows: target.modules) {
                    QRCodeView(matrix: matrix).frame(width: 240, height: 240).frame(maxWidth: .infinity)
                }
                HStack {
                    Text(target.url).font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
                    Spacer()
                    Button("Copy") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(target.url, forType: .string)
                    }
                }
            } else {
                Text(store.problem ?? "Asking the hub for its addresses…").font(.system(size: 13))
            }
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 380)
        .onAppear { store.loadPairing() }
    }
}

/// Draws a QR matrix in whole points, with the four-module light margin scanners need.
struct QRCodeView: View {
    let matrix: QRMatrix

    var body: some View {
        Canvas { context, size in
            let quiet = 4
            let side = min(size.width, size.height)
            let cell = (side / CGFloat(matrix.size + 2 * quiet)).rounded(.down)
            let offset = ((side - cell * CGFloat(matrix.size)) / 2).rounded(.down)
            context.fill(Path(CGRect(x: 0, y: 0, width: side, height: side)), with: .color(.white))
            for (y, row) in matrix.dark.enumerated() {
                for (x, dark) in row.enumerated() where dark {
                    let module = CGRect(x: offset + CGFloat(x) * cell, y: offset + CGFloat(y) * cell,
                                        width: cell, height: cell)
                    context.fill(Path(module), with: .color(.black))
                }
            }
        }
        .accessibilityLabel("QR code for pairing")
    }
}
