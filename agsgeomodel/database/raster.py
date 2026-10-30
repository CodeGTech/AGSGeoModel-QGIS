"""Minimal GeoTIFF writer/reader (float32, deflate) and 2DM mesh writer — no GDAL needed.

GeoTIFF: little-endian baseline TIFF, one band, strips of 16 rows, Adobe Deflate (zlib)
compression, GeoKeys for a projected EPSG CRS (PixelIsArea) and ``GDAL_NODATA``. Rows are
written north-up (the model grid stores row 0 in the south and is flipped here).

2DM (MDAL "MESH2D"): triangulated surface with node elevations, loadable as a QGIS mesh layer and
rendered in the QGIS 3D view with a mesh 3D renderer.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

from ..core.grid import Grid2D

NODATA = -9999.0


def _ifd_entry(tag: int, typ: int, count: int, value_or_offset: bytes) -> bytes:
    return struct.pack("<HHI", tag, typ, count) + value_or_offset.ljust(4, b"\0")


def write_geotiff(
    path: str | Path,
    grid: Grid2D,
    values: np.ndarray,
    epsg: int | None = None,
    nodata: float = NODATA,
    compress: bool = True,
) -> Path:
    path = Path(path)
    v = np.asarray(values, dtype=np.float32)
    if v.shape != grid.shape:
        raise ValueError("values do not match grid")
    img = np.where(np.isfinite(v), v, np.float32(nodata))[::-1].astype("<f4")  # north-up
    ny, nx = img.shape
    rps = 16
    strips = []
    for r in range(0, ny, rps):
        raw = img[r : r + rps].tobytes()
        strips.append(zlib.compress(raw, 6) if compress else raw)
    x_ul = grid.x0 - grid.dx / 2
    y_ul = grid.y0 + (grid.ny - 0.5) * grid.dy
    geokeys = [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, int(epsg or 32767)]
    nodata_txt = f"{nodata:g}".encode() + b"\0"
    tags: dict[int, tuple[int, int, bytes]] = {
        256: (4, 1, struct.pack("<I", nx)),
        257: (4, 1, struct.pack("<I", ny)),
        258: (3, 1, struct.pack("<H", 32)),
        259: (3, 1, struct.pack("<H", 8 if compress else 1)),
        262: (3, 1, struct.pack("<H", 1)),
        277: (3, 1, struct.pack("<H", 1)),
        278: (4, 1, struct.pack("<I", rps)),
        284: (3, 1, struct.pack("<H", 1)),
        339: (3, 1, struct.pack("<H", 3)),
        33550: (12, 3, struct.pack("<3d", grid.dx, grid.dy, 0.0)),
        33922: (12, 6, struct.pack("<6d", 0.0, 0.0, 0.0, x_ul, y_ul, 0.0)),
        34735: (3, len(geokeys), struct.pack(f"<{len(geokeys)}H", *geokeys)),
        42113: (2, len(nodata_txt), nodata_txt),
    }
    n_strips = len(strips)
    # layout: header(8) | IFD | out-of-line tag data | strip offsets/counts | strip data
    n_tags = len(tags) + 2
    ifd_size = 2 + 12 * n_tags + 4
    off = 8 + ifd_size
    blobs: list[bytes] = []
    entries: dict[int, bytes] = {}
    for tag, (typ, count, payload) in sorted(tags.items()):
        if len(payload) <= 4:
            entries[tag] = _ifd_entry(tag, typ, count, payload)
        else:
            if off % 2:
                blobs.append(b"\0")
                off += 1
            entries[tag] = _ifd_entry(tag, typ, count, struct.pack("<I", off))
            blobs.append(payload)
            off += len(payload)
    if off % 2:
        blobs.append(b"\0")
        off += 1
    offsets_pos = off
    off += 4 * n_strips
    counts_pos = off
    off += 4 * n_strips
    data_offsets = []
    for s in strips:
        data_offsets.append(off)
        off += len(s)
    if n_strips == 1:
        entries[273] = _ifd_entry(273, 4, 1, struct.pack("<I", data_offsets[0]))
        entries[279] = _ifd_entry(279, 4, 1, struct.pack("<I", len(strips[0])))
    else:
        entries[273] = _ifd_entry(273, 4, n_strips, struct.pack("<I", offsets_pos))
        entries[279] = _ifd_entry(279, 4, n_strips, struct.pack("<I", counts_pos))
    out = bytearray(b"II*\0" + struct.pack("<I", 8))
    out += struct.pack("<H", n_tags)
    for tag in sorted(entries):
        out += entries[tag]
    out += struct.pack("<I", 0)
    for b in blobs:
        out += b
    out += struct.pack(f"<{n_strips}I", *data_offsets)
    out += struct.pack(f"<{n_strips}I", *[len(s) for s in strips])
    for s in strips:
        out += s
    path.write_bytes(bytes(out))
    return path


def read_geotiff(path: str | Path) -> tuple[Grid2D, np.ndarray]:
    """Reader for the files written by :func:`write_geotiff` (and similar simple float GeoTIFFs)."""
    b = Path(path).read_bytes()
    if b[:4] != b"II*\0":
        raise ValueError("only little-endian classic TIFF is supported")
    (ifd,) = struct.unpack_from("<I", b, 4)
    (n,) = struct.unpack_from("<H", b, ifd)
    size = {1: 1, 2: 1, 3: 2, 4: 4, 11: 4, 12: 8, 16: 8}
    fmt = {1: "B", 2: "c", 3: "H", 4: "I", 11: "f", 12: "d"}
    tags = {}
    for i in range(n):
        tag, typ, count = struct.unpack_from("<HHI", b, ifd + 2 + 12 * i)
        nbytes = size[typ] * count
        pos = ifd + 2 + 12 * i + 8
        if nbytes > 4:
            (pos,) = struct.unpack_from("<I", b, pos)
        if typ == 2:
            tags[tag] = b[pos : pos + count].rstrip(b"\0").decode()
        else:
            tags[tag] = struct.unpack_from("<" + fmt[typ] * count, b, pos)
    nx, ny = tags[256][0], tags[257][0]
    offs, cnts = tags[273], tags[279]
    comp = tags.get(259, (1,))[0]
    raw = b"".join(zlib.decompress(b[o : o + c]) if comp == 8 else b[o : o + c] for o, c in zip(offs, cnts))
    img = np.frombuffer(raw, dtype="<f4").reshape(ny, nx).astype(float)
    sx, sy, _ = tags[33550]
    tp = tags[33922]
    nod = float(tags.get(42113, str(NODATA)))
    img = np.where(img == nod, np.nan, img)[::-1]
    grid = Grid2D(tp[3] + sx / 2, tp[4] - (ny - 0.5) * sy, sx, sy, nx, ny)
    return grid, img


def write_2dm(path: str | Path, grid: Grid2D, values: np.ndarray, vertical_exaggeration: float = 1.0) -> Path:
    """Write a surface as an MDAL 2DM triangle mesh (nodes with NaN values are omitted)."""
    path = Path(path)
    v = np.asarray(values, float)
    X, Y = grid.mesh()
    ok = np.isfinite(v)
    node_id = np.full(v.shape, 0, dtype=np.int64)
    node_id[ok] = np.arange(1, ok.sum() + 1)
    lines = ["MESH2D"]
    xs, ys, zs = X[ok], Y[ok], v[ok] * vertical_exaggeration
    lines += [f"ND {i} {x:.3f} {y:.3f} {z:.3f}" for i, x, y, z in zip(range(1, len(xs) + 1), xs, ys, zs)]
    eid = 1
    for j in range(grid.ny - 1):
        a = node_id[j, :-1]
        b = node_id[j, 1:]
        c = node_id[j + 1, 1:]
        d = node_id[j + 1, :-1]
        for n1, n2, n3, n4 in zip(a, b, c, d):
            if n1 and n2 and n3:
                lines.append(f"E3T {eid} {n1} {n2} {n3} 1")
                eid += 1
            if n1 and n3 and n4:
                lines.append(f"E3T {eid} {n1} {n3} {n4} 1")
                eid += 1
    path.write_text("\n".join(lines) + "\n")
    return path
