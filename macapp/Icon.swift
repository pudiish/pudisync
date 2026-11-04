// Draws the app icon (the hub's icon.svg: a screen ringed by amber LEDs) as a 1024 px PNG.
// Run by build.sh: swift Icon.swift <out.png>
import AppKit

let side = 1024
let scale = CGFloat(side) / 512
guard let path = CommandLine.arguments.dropFirst().first,
      let ctx = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0,
                          space: CGColorSpace(name: CGColorSpace.sRGB)!,
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(1) }

func colour(_ hex: UInt32) -> CGColor {
    CGColor(srgbRed: CGFloat(hex >> 16 & 0xFF) / 255, green: CGFloat(hex >> 8 & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

// SVG coordinates run top-down; CoreGraphics runs bottom-up.
func box(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> CGRect {
    CGRect(x: x * scale, y: (512 - y - h) * scale, width: w * scale, height: h * scale)
}

func rounded(_ rect: CGRect, _ radius: CGFloat) -> CGPath {
    CGPath(roundedRect: rect, cornerWidth: radius * scale, cornerHeight: radius * scale, transform: nil)
}

ctx.addPath(rounded(box(0, 0, 512, 512), 96)); ctx.setFillColor(colour(0x0c0c0b)); ctx.fillPath()
ctx.addPath(rounded(box(120, 156, 272, 168), 6)); ctx.setFillColor(colour(0x151513)); ctx.fillPath()
ctx.addPath(rounded(box(120, 156, 272, 168), 6)); ctx.setStrokeColor(colour(0x3b3a35)); ctx.setLineWidth(6 * scale); ctx.strokePath()
var leds: [(CGFloat, CGFloat)] = []
for x in stride(from: CGFloat(92), through: 372, by: 56) { leds += [(x, 352), (x, 104)] }
for y in [CGFloat(168), 228, 288] { leds += [(68, y), (420, y)] }
ctx.setShadow(offset: .zero, blur: 18 * scale, color: colour(0xffb000))
ctx.setFillColor(colour(0xffb000))
for (x, y) in leds { ctx.addPath(rounded(box(x, y, 24, 24), 3)); ctx.fillPath() }
ctx.setShadow(offset: .zero, blur: 10 * scale, color: colour(0x4be37a))
ctx.setFillColor(colour(0x4be37a))
ctx.fillEllipse(in: box(360, 292, 16, 16))

guard let image = ctx.makeImage(),
      let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: path))
