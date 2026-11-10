"""QR codes for pairing a phone or tablet, drawn by macOS's own CoreImage.

pyobjc-framework-Quartz is already used for screen capture, so this needs no
new dependency. Clients get the bare module matrix and draw it themselves, at
whatever size suits their screen.
"""
import functools


@functools.lru_cache(maxsize=16)
def modules(text):
    """The QR code for `text`: rows of "1" (dark) and "0" (light), no quiet zone.

    Raises RuntimeError when CoreImage is not available.
    """
    try:
        import Quartz
        from Foundation import NSData
    except ImportError as exc:
        raise RuntimeError("QR codes need pyobjc-framework-Quartz, which is macOS only") from exc
    data = text.encode()
    generator = Quartz.CIFilter.filterWithName_("CIQRCodeGenerator")
    generator.setValue_forKey_(NSData.dataWithBytes_length_(data, len(data)), "inputMessage")
    generator.setValue_forKey_("M", "inputCorrectionLevel")   # survives a smudged screen
    image = generator.outputImage()
    extent = image.extent()
    picture = Quartz.CIContext.contextWithOptions_(None).createCGImage_fromRect_(image, extent)
    raw = bytes(Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(picture)))
    row_bytes = Quartz.CGImageGetBytesPerRow(picture)
    pixel_bytes = Quartz.CGImageGetBitsPerPixel(picture) // 8
    side = int(extent.size.width)     # CoreImage draws one pixel per module
    rows = ["".join("1" if raw[y * row_bytes + x * pixel_bytes] < 128 else "0" for x in range(side))
            for y in range(side)]
    return trim(rows)


def trim(rows):
    """Drop the light border CoreImage adds; the finder patterns make every code's edges dark."""
    rows = [row for row in rows if "1" in row]
    first = min(row.index("1") for row in rows)
    last = max(row.rindex("1") for row in rows)
    return tuple(row[first:last + 1] for row in rows)
