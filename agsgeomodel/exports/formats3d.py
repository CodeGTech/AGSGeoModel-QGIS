"""3D model exports: VTK (legacy), OBJ + MTL, PLY, glTF 2.0 binary (GLB), DXF R12, IFC4 (experimental).

Large world coordinates (e.g. British National Grid) lose precision in float32 formats, so
OBJ/PLY/glTF are written relative to a local origin that is recorded in the file (OBJ/PLY
comments, glTF ``extras``) and in the export manifest.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import struct
import uuid
from pathlib import Path

import numpy as np

from .mesh import TriMesh


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def local_origin(meshes: list[TriMesh]) -> np.ndarray:
    pts = np.vstack([m.vertices for m in meshes if len(m.vertices)]) if meshes else np.zeros((1, 3))
    return np.array([math.floor(pts[:, 0].min()), math.floor(pts[:, 1].min()), 0.0])


# ============================================================================ VTK (legacy)
def write_vtk_polydata(path, meshes: list[TriMesh], lines=None, binary: bool = False) -> Path:
    """Unstructured surface meshes (+ optional polylines) with a ``unit_id`` cell scalar."""
    path = Path(path)
    verts, faces, ids = [], [], []
    off = 0
    names = []
    for k, m in enumerate(meshes):
        verts.append(m.vertices)
        faces.append(m.faces + off)
        ids.append(np.full(len(m.faces), k))
        names.append(m.name)
        off += len(m.vertices)
    V = np.vstack(verts) if verts else np.zeros((0, 3))
    F = np.vstack(faces) if faces else np.zeros((0, 3), dtype=int)
    I = np.concatenate(ids) if ids else np.zeros(0, dtype=int)
    L = []
    if lines:
        base = len(V)
        pts = []
        for lk, (unit, p, q, *_rest) in enumerate(lines):
            pts.extend([p, q])
            L.append((base + 2 * lk, base + 2 * lk + 1, names.index(unit) if unit in names else -1))
        V = np.vstack([V, np.array(pts, float)])
    out = [
        "# vtk DataFile Version 3.0",
        "AGSGeoModel geological model; unit ids: " + ";".join(f"{k}={n}" for k, n in enumerate(names)),
        "ASCII",
        "DATASET POLYDATA",
        f"POINTS {len(V)} double",
    ]
    out += [f"{x:.3f} {y:.3f} {z:.3f}" for x, y, z in V]
    if len(F):
        out.append(f"POLYGONS {len(F)} {4 * len(F)}")
        out += [f"3 {a} {b} {c}" for a, b, c in F]
    if L:
        out.append(f"LINES {len(L)} {3 * len(L)}")
        out += [f"2 {a} {b}" for a, b, _ in L]
    ncell = len(F) + len(L)
    out.append(f"CELL_DATA {ncell}")
    out.append("SCALARS unit_id int 1")
    out.append("LOOKUP_TABLE default")
    out += [str(int(i)) for i in I] + [str(int(u)) for _, _, u in L]
    path.write_text("\n".join(out) + "\n")
    return path


def write_vtk_surface(
    path, grid, values: dict[str, np.ndarray], elevation_key: str, vertical_exaggeration: float = 1.0
) -> Path:
    """Structured grid surface with any number of point scalars (e.g. elevation, std, class)."""
    path = Path(path)
    X, Y = grid.mesh()
    Z = np.nan_to_num(np.asarray(values[elevation_key], float), nan=np.nan) * vertical_exaggeration
    out = [
        "# vtk DataFile Version 3.0",
        f"AGSGeoModel surface {elevation_key}",
        "ASCII",
        "DATASET STRUCTURED_GRID",
        f"DIMENSIONS {grid.nx} {grid.ny} 1",
        f"POINTS {grid.size} double",
    ]
    zz = np.where(np.isfinite(Z), Z, np.nanmean(Z) if np.isfinite(Z).any() else 0.0)
    out += [f"{x:.3f} {y:.3f} {z:.3f}" for x, y, z in zip(X.ravel(), Y.ravel(), zz.ravel())]
    out.append(f"POINT_DATA {grid.size}")
    for k, v in values.items():
        a = np.asarray(v, float).ravel()
        out.append(f"SCALARS {k.replace(':', '_')} float 1")
        out.append("LOOKUP_TABLE default")
        out += [("nan" if not np.isfinite(t) else f"{t:.4f}") for t in a]
    path.write_text("\n".join(out) + "\n")
    return path


def write_vtk_voxels(path, xe, ye, ze, ids, units: list[str]) -> Path:
    """Rectilinear block model with a ``unit_id`` cell scalar (-1 = above ground)."""
    path = Path(path)
    nx, ny, nz = ids.shape
    out = [
        "# vtk DataFile Version 3.0",
        "AGSGeoModel block model; unit ids: " + ";".join(f"{k}={u}" for k, u in enumerate(units)),
        "ASCII",
        "DATASET RECTILINEAR_GRID",
        f"DIMENSIONS {nx + 1} {ny + 1} {nz + 1}",
        f"X_COORDINATES {nx + 1} double",
        " ".join(f"{v:.3f}" for v in xe),
        f"Y_COORDINATES {ny + 1} double",
        " ".join(f"{v:.3f}" for v in ye),
        f"Z_COORDINATES {nz + 1} double",
        " ".join(f"{v:.3f}" for v in ze),
        f"CELL_DATA {nx * ny * nz}",
        "SCALARS unit_id int 1",
        "LOOKUP_TABLE default",
    ]
    # VTK ordering: x fastest, then y, then z
    flat = ids.transpose(2, 1, 0).ravel()
    out += [" ".join(str(int(v)) for v in flat[i : i + 64]) for i in range(0, len(flat), 64)]
    path.write_text("\n".join(out) + "\n")
    return path


# ============================================================================ OBJ / PLY
def write_obj(path, meshes: list[TriMesh], origin: np.ndarray | None = None) -> tuple[Path, Path]:
    path = Path(path)
    mtl = path.with_suffix(".mtl")
    o = local_origin(meshes) if origin is None else origin
    lines = [
        "# AGSGeoModel export",
        f"# local origin (add to coordinates): {o[0]:.3f} {o[1]:.3f} {o[2]:.3f}",
        f"mtllib {mtl.name}",
    ]
    mtl_lines = ["# AGSGeoModel materials"]
    base = 1
    for m in meshes:
        r, g, b = _rgb(m.colour)
        mat = m.name.replace(" ", "_").replace(":", "_")
        mtl_lines += [f"newmtl {mat}", f"Kd {r:.4f} {g:.4f} {b:.4f}", "Ka 0.1 0.1 0.1", "d 1.0", ""]
        lines.append(f"o {mat}")
        lines += [f"v {x - o[0]:.3f} {y - o[1]:.3f} {z - o[2]:.3f}" for x, y, z in m.vertices]
        lines.append(f"usemtl {mat}")
        lines += [f"f {a + base} {b_ + base} {c + base}" for a, b_, c in m.faces]
        base += len(m.vertices)
    path.write_text("\n".join(lines) + "\n")
    mtl.write_text("\n".join(mtl_lines) + "\n")
    return path, mtl


def write_ply(path, meshes: list[TriMesh], origin: np.ndarray | None = None) -> Path:
    path = Path(path)
    o = local_origin(meshes) if origin is None else origin
    V, F, C = [], [], []
    off = 0
    for m in meshes:
        r, g, b = (int(round(255 * c)) for c in _rgb(m.colour))
        V.append(m.vertices - o)
        F.append(m.faces + off)
        C.append(np.tile([r, g, b], (len(m.vertices), 1)))
        off += len(m.vertices)
    V = np.vstack(V) if V else np.zeros((0, 3))
    F = np.vstack(F) if F else np.zeros((0, 3), dtype=int)
    C = np.vstack(C) if C else np.zeros((0, 3), dtype=int)
    head = [
        "ply",
        "format ascii 1.0",
        f"comment AGSGeoModel; local origin {o[0]:.3f} {o[1]:.3f} {o[2]:.3f}",
        f"element vertex {len(V)}",
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
        f"element face {len(F)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    body = [f"{x:.3f} {y:.3f} {z:.3f} {r} {g} {b}" for (x, y, z), (r, g, b) in zip(V, C)]
    body += [f"3 {a} {b} {c}" for a, b, c in F]
    path.write_text("\n".join(head + body) + "\n")
    return path


# ============================================================================ glTF 2.0 (GLB)
def write_glb(path, meshes: list[TriMesh], origin: np.ndarray | None = None, crs: str | None = None) -> Path:
    """Binary glTF. glTF is Y-up: (east, north, up) -> (x, z, -y) relative to a local origin."""
    path = Path(path)
    o = local_origin(meshes) if origin is None else origin
    bin_chunks = bytearray()
    buffer_views, accessors, gl_meshes, materials, nodes = [], [], [], [], []

    def add_view(data: bytes, target: int) -> int:
        while len(bin_chunks) % 4:
            bin_chunks.append(0)
        buffer_views.append({"buffer": 0, "byteOffset": len(bin_chunks), "byteLength": len(data), "target": target})
        bin_chunks.extend(data)
        return len(buffer_views) - 1

    for m in meshes:
        if m.empty:
            continue
        v = (m.vertices - o).astype(np.float64)
        pos = np.column_stack([v[:, 0], v[:, 2], -v[:, 1]]).astype(np.float32)
        idx = m.faces.astype(np.uint32).ravel()
        pv = add_view(pos.tobytes(), 34962)
        iv = add_view(idx.tobytes(), 34963)
        accessors.append(
            {
                "bufferView": pv,
                "componentType": 5126,
                "count": len(pos),
                "type": "VEC3",
                "min": pos.min(axis=0).tolist(),
                "max": pos.max(axis=0).tolist(),
            }
        )
        pa = len(accessors) - 1
        accessors.append({"bufferView": iv, "componentType": 5125, "count": len(idx), "type": "SCALAR"})
        ia = len(accessors) - 1
        r, g, b = _rgb(m.colour)
        materials.append(
            {
                "name": m.name,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [r, g, b, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.9,
                },
                "doubleSided": True,
            }
        )
        gl_meshes.append(
            {
                "name": m.name,
                "primitives": [{"attributes": {"POSITION": pa}, "indices": ia, "material": len(materials) - 1}],
            }
        )
        nodes.append({"name": m.name, "mesh": len(gl_meshes) - 1, "extras": m.properties})
    while len(bin_chunks) % 4:
        bin_chunks.append(0)
    gltf = {
        "asset": {"version": "2.0", "generator": "AGSGeoModel"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes))), "name": "geological model"}],
        "nodes": nodes,
        "meshes": gl_meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_chunks)}],
        "extras": {"local_origin": o.tolist(), "crs": crs, "axes": "glTF y-up: x=east, y=up, z=-north"},
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_chunks)
    out = (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(js), 0x4E4F534A)
        + js
        + struct.pack("<II", len(bin_chunks), 0x004E4942)
        + bytes(bin_chunks)
    )
    path.write_bytes(out)
    return path


# ============================================================================ DXF R12
_ACI = [
    (1, (255, 0, 0)),
    (2, (255, 255, 0)),
    (3, (0, 255, 0)),
    (4, (0, 255, 255)),
    (5, (0, 0, 255)),
    (6, (255, 0, 255)),
    (7, (255, 255, 255)),
    (8, (128, 128, 128)),
    (9, (192, 192, 192)),
    (30, (255, 127, 0)),
    (40, (255, 191, 0)),
    (50, (255, 255, 127)),
    (32, (165, 82, 0)),
    (52, (165, 165, 82)),
    (142, (82, 124, 165)),
    (102, (127, 255, 127)),
    (112, (82, 165, 82)),
    (12, (165, 0, 0)),
    (22, (165, 41, 0)),
    (254, (240, 240, 240)),
    (250, (51, 51, 51)),
]


def aci(hex_colour: str) -> int:
    r, g, b = (int(255 * c) for c in _rgb(hex_colour))
    return min(_ACI, key=lambda t: (t[1][0] - r) ** 2 + (t[1][1] - g) ** 2 + (t[1][2] - b) ** 2)[0]


def write_dxf(
    path,
    meshes: list[TriMesh] | None = None,
    polylines: list[tuple[str, np.ndarray]] | None = None,
    texts: list[tuple[str, float, float, float, str]] | None = None,
    layer_colours: dict[str, str] | None = None,
) -> Path:
    """ASCII DXF R12: 3DFACE per triangle, POLYLINE (3D) per polyline, TEXT labels; one layer per unit."""
    path = Path(path)
    meshes = meshes or []
    polylines = polylines or []
    layers = {m.name: m.colour for m in meshes}
    layers.update(layer_colours or {})
    for name, _ in polylines:
        layers.setdefault(name, "#000000")
    for lay, *_ in texts or []:
        layers.setdefault(lay, "#000000")

    def clean(n: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_$" else "_" for ch in n)[:31] or "0"

    o: list[str] = []

    def g(*pairs):
        """Append DXF group-code / value pairs."""
        for code, val in pairs:
            o.append(f"{code:>3}")
            o.append(str(val))

    g((0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1009"), (0, "ENDSEC"))
    g((0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LAYER"), (70, len(layers)))
    for name, colour in layers.items():
        g((0, "LAYER"), (2, clean(name)), (70, 0), (62, aci(colour)), (6, "CONTINUOUS"))
    g((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "ENTITIES"))
    for m in meshes:
        lay = clean(m.name)
        V = m.vertices
        for a, b, c in m.faces:
            g((0, "3DFACE"), (8, lay))
            for k, idx in enumerate((a, b, c, c)):
                x, y, z = V[idx]
                g((10 + k, f"{x:.3f}"), (20 + k, f"{y:.3f}"), (30 + k, f"{z:.3f}"))
    for name, line in polylines:
        pts = np.asarray(line, float)
        g((0, "POLYLINE"), (8, clean(name)), (66, 1), (10, "0.0"), (20, "0.0"), (30, "0.0"), (70, 8))
        for p in pts:
            z = p[2] if len(p) > 2 else 0.0
            g((0, "VERTEX"), (8, clean(name)), (10, f"{p[0]:.3f}"), (20, f"{p[1]:.3f}"), (30, f"{z:.3f}"), (70, 32))
        g((0, "SEQEND"), (8, clean(name)))
    for lay, x, y, z, txt in texts or []:
        g((0, "TEXT"), (8, clean(lay)), (10, f"{x:.3f}"), (20, f"{y:.3f}"), (30, f"{z:.3f}"), (40, "1.0"), (1, txt))
    g((0, "ENDSEC"), (0, "EOF"))
    path.write_text("\n".join(o) + "\n")
    return path


# ============================================================================ IFC4 (experimental)
def _ifc_guid() -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
    n = uuid.uuid4().int
    out = []
    for _ in range(22):
        out.append(chars[n % 64])
        n //= 64
    return "".join(reversed(out))


def write_ifc(
    path,
    meshes: list[TriMesh],
    crs_epsg: int | None = None,
    origin: np.ndarray | None = None,
    project_name: str = "AGSGeoModel ground model",
) -> Path:
    """IFC4 file with one IfcGeographicElement (IfcTriangulatedFaceSet) per unit, georeferenced with
    IfcMapConversion / IfcProjectedCRS. Experimental: intended for BIM coordination viewing."""
    path = Path(path)
    o = local_origin(meshes) if origin is None else origin
    lines: list[str] = []
    n = [0]

    def add(s: str) -> str:
        n[0] += 1
        lines.append(f"#{n[0]}={s};")
        return f"#{n[0]}"

    def q(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    owner_person = add("IFCPERSON($,$,'AGSGeoModel',$,$,$,$,$)")
    org = add("IFCORGANIZATION($,'AGSGeoModel',$,$,$)")
    pao = add(f"IFCPERSONANDORGANIZATION({owner_person},{org},$)")
    app = add(f"IFCAPPLICATION({org},'0.1.0','AGSGeoModel','AGSGeoModel')")
    hist = add(f"IFCOWNERHISTORY({pao},{app},$,.ADDED.,$,$,$,0)")
    unit_len = add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
    units = add(f"IFCUNITASSIGNMENT(({unit_len}))")
    origin_pt = add("IFCCARTESIANPOINT((0.,0.,0.))")
    axis = add(f"IFCAXIS2PLACEMENT3D({origin_pt},$,$)")
    ctx = add(f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,{axis},$)")
    body_ctx = add(f"IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Body','Model',*,*,*,*,{ctx},$,.MODEL_VIEW.,$)")
    proj = add(f"IFCPROJECT({q(_ifc_guid())},{hist},{q(project_name)},$,$,$,$,({ctx}),{units})")
    if crs_epsg:
        pcrs = add(f"IFCPROJECTEDCRS('EPSG:{crs_epsg}',$,$,$,$,$,{unit_len})")
        add(f"IFCMAPCONVERSION({ctx},{pcrs},{o[0]:.3f},{o[1]:.3f},{o[2]:.3f},1.,0.,1.)")
    site_place = add(f"IFCLOCALPLACEMENT($,{axis})")
    site = add(f"IFCSITE({q(_ifc_guid())},{hist},'Site',$,$,{site_place},$,$,.ELEMENT.,$,$,$,$,$)")
    add(f"IFCRELAGGREGATES({q(_ifc_guid())},{hist},$,$,{proj},({site}))")
    elems = []
    for m in meshes:
        if m.empty:
            continue
        V = m.vertices - o
        pts = ",".join(f"({x:.3f},{y:.3f},{z:.3f})" for x, y, z in V)
        plist = add(f"IFCCARTESIANPOINTLIST3D(({pts}),$)")
        idx = ",".join(f"({a + 1},{b + 1},{c + 1})" for a, b, c in m.faces)
        tfs = add(f"IFCTRIANGULATEDFACESET({plist},$,.F.,({idx}),$)")
        r, g_, b = _rgb(m.colour)
        col = add(f"IFCCOLOURRGB($,{r:.4f},{g_:.4f},{b:.4f})")
        rend = add(f"IFCSURFACESTYLESHADING({col},0.)")
        sstyle = add(f"IFCSURFACESTYLE({q(m.name)},.BOTH.,({rend}))")
        add(f"IFCSTYLEDITEM({tfs},({sstyle}),$)")
        shape = add(f"IFCSHAPEREPRESENTATION({body_ctx},'Body','Tessellation',({tfs}))")
        pds = add(f"IFCPRODUCTDEFINITIONSHAPE($,$,({shape}))")
        place = add(f"IFCLOCALPLACEMENT({site_place},{axis})")
        el = add(
            f"IFCGEOGRAPHICELEMENT({q(_ifc_guid())},{hist},{q(m.name)},{q('Geological unit ' + m.name)},$,"
            f"{place},{pds},$,.USERDEFINED.)"
        )
        elems.append(el)
    if elems:
        add(f"IFCRELCONTAINEDINSPATIALSTRUCTURE({q(_ifc_guid())},{hist},$,$,({','.join(elems)}),{site})")
    head = [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_DESCRIPTION(('ViewDefinition [ReferenceView]'),'2;1');",
        f"FILE_NAME({q(path.name)},'{_dt.datetime.now().replace(microsecond=0).isoformat()}',('AGSGeoModel'),"
        "('AGSGeoModel'),'AGSGeoModel','AGSGeoModel','');",
        "FILE_SCHEMA(('IFC4'));",
        "ENDSEC;",
        "DATA;",
    ]
    path.write_text("\n".join(head + lines + ["ENDSEC;", "END-ISO-10303-21;"]) + "\n")
    return path
