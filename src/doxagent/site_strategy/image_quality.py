"""Small bounded PNG quality check, not CAPTCHA recognition. No imaging dependency."""

import struct
import zlib


def png_quality(data):
    try:
        if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
            return "FAILED"
        width, height, bits, colour, _, _, interlace = struct.unpack(">IIBBBBB", data[16:29])
        channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
        if bits != 8 or interlace or not channels or width * height > 4_000_000:
            return "LOW_DETAIL"
        compressed = []
        pos = 8
        while pos + 12 <= len(data):
            length = struct.unpack(">I", data[pos : pos + 4])[0]
            if pos + length + 12 > len(data):
                return "FAILED"
            if data[pos + 4 : pos + 8] == b"IDAT":
                compressed.append(data[pos + 8 : pos + 8 + length])
            pos += length + 12
        stride = width * channels
        limit = (stride + 1) * height
        decoder = zlib.decompressobj()
        raw = decoder.decompress(b"".join(compressed), limit + 1)
        if len(raw) != limit:
            return "LOW_DETAIL"
        previous = bytearray(stride)
        samples = []
        for y in range(height):
            offset = y * (stride + 1)
            kind = raw[offset]
            row = bytearray(raw[offset + 1 : offset + 1 + stride])
            if kind:
                for x in range(stride):
                    left = row[x - channels] if x >= channels else 0
                    up = previous[x]
                    corner = previous[x - channels] if x >= channels else 0
                    if kind == 1:
                        value = left
                    elif kind == 2:
                        value = up
                    elif kind == 3:
                        value = (left + up) // 2
                    elif kind == 4:
                        p = left + up - corner
                        distances = (abs(p - left), abs(p - up), abs(p - corner))
                        value = (left, up, corner)[distances.index(min(distances))]
                    else:
                        return "LOW_DETAIL"
                    row[x] = (row[x] + value) & 255
            if y % max(1, height // 64) == 0:
                for x in range(0, width, max(1, width // 96)):
                    pixel = row[x * channels : x * channels + channels]
                    samples.append(sum(pixel[:3]) / 3 if channels >= 3 else pixel[0])
            previous = row
        if not samples:
            return "FAILED"
        average = sum(samples) / len(samples)
        detail = sum(abs(v - average) > 12 for v in samples) / len(samples)
        return (
            "BLANK_SUSPECTED"
            if max(samples) - min(samples) < 12
            else "READABLE"
            if detail >= 0.003
            else "LOW_DETAIL"
        )
    except (ValueError, IndexError, struct.error, zlib.error):
        return "FAILED"
