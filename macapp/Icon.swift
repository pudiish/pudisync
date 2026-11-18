// Renders the one PudiSync logo, ambilight/web/icon.svg, as a square PNG.
// Every raster icon (the Mac app's, the iPhone Home Screen's) comes from that
// file through here, so the logo is only ever drawn in one place.
//   swift Icon.swift <icon.svg> <out.png> <side in px> [margin in px]
// The margin is for the Mac: its icons sit on Apple's grid (an 824 px tile in a
// 1024 px canvas). iOS and browsers mask or crop icons themselves, so theirs fill the square.
import AppKit

let args = CommandLine.arguments
guard args.count == 4 || args.count == 5, let side = Int(args[3]), side > 0,
      let margin = Double(args.count == 5 ? args[4] : "0"), margin >= 0, margin * 2 < Double(side) else {
    FileHandle.standardError.write("usage: swift Icon.swift <icon.svg> <out.png> <side> [margin]\n".data(using: .utf8)!)
    exit(2)
}
guard let svg = NSImage(contentsOf: URL(fileURLWithPath: args[1])), svg.isValid else {
    FileHandle.standardError.write("cannot read SVG: \(args[1])\n".data(using: .utf8)!)
    exit(1)
}
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side,
                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                 colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
rep.size = NSSize(width: side, height: side)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
NSGraphicsContext.current?.imageInterpolation = .high
svg.draw(in: NSRect(x: margin, y: margin, width: Double(side) - 2 * margin, height: Double(side) - 2 * margin))
NSGraphicsContext.restoreGraphicsState()
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: args[2]))
