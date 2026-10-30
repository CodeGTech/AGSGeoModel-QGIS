"""AGS4 / AGS3 reading, writing and format detection."""

import numpy as np
import pytest
from helpers import minimal_ags, table, tran

from agsgeomodel.core.errors import AGSParseError
from agsgeomodel.importers.ags import read_ags, write_ags4
from agsgeomodel.importers.ags.reader import detect_format, parse_float

LOCS = [("BH1", "CP", 545000.0, 180000.0, 5.0, 20.0), ("BH2", "CP", 545100.0, 180050.0, 6.0, 25.0)]
STRATA = [
    ("BH1", 0.0, 1.2, "MADE GROUND: brick and concrete", "MG"),
    ("BH1", 1.2, 20.0, 'Stiff grey fissured CLAY with "selenite"', "LC", "LC"),
    ("BH2", 0.0, 25.0, "Firm to stiff brown CLAY", "LC"),
]


def test_roundtrip_and_quoting():
    text = minimal_ags(LOCS, STRATA)
    assert text.count("\r\n") > 10 and '""selenite""' in text
    ags = read_ags(text.encode())
    assert ags.format == "AGS4" and ags.version == "4.1.1" and ags.line_endings == "CRLF"
    geol = ags["GEOL"]
    assert len(geol) == 3 and geol.column("GEOL_DESC")[1] == 'Stiff grey fissured CLAY with "selenite"'
    assert geol.unit_of("GEOL_TOP") == "m" and geol.type_of("GEOL_TOP") == "2DP"
    np.testing.assert_allclose(geol.float_column("GEOL_BASE"), [1.2, 20.0, 25.0])
    again = read_ags(write_ags4(ags.tables.values()).encode())
    assert again["GEOL"].rows == geol.rows and not [i for i in again.issues if i.severity == "error"]


def test_parse_float():
    assert parse_float("1.50") == 1.5 and parse_float(" -2 ") == -2.0
    for bad in ("", "NR", "<0.5", ">50", None, "abc"):
        assert np.isnan(parse_float(bad))


def test_detect_format():
    assert detect_format('"GROUP","PROJ"') == "AGS4"
    assert detect_format('"**PROJ"\n"*PROJ_ID"') == "AGS3"
    assert detect_format("hello") == "unknown"
    with pytest.raises(AGSParseError):
        read_ags(b"not,an,ags,file\n")


def test_structural_problems_are_reported_not_raised():
    text = (
        '"GROUP","LOCA"\r\n"HEADING","LOCA_ID","LOCA_GL"\r\n"UNIT","","m"\r\n"TYPE","ID","2DP"\r\n'
        '"DATA","BH1","5.00","EXTRA"\r\nDATA,BH2,6.00\r\n"FOO","x"\r\n'
    )
    ags = read_ags(text.encode())
    rules = {i.rule for i in ags.issues}
    assert {"AGS4-4", "AGS4-5", "AGS4-3"} <= rules
    assert ags["LOCA"].column("LOCA_ID") == ["BH1", "BH2"]  # rows kept, padded / truncated


def test_duplicate_headings_are_renamed():
    text = (
        '"GROUP","LOCA"\r\n"HEADING","LOCA_ID","LOCA_GL","LOCA_GL"\r\n"UNIT","","m","m"\r\n'
        '"TYPE","ID","2DP","2DP"\r\n"DATA","BH1","5.00","6.00"\r\n'
    )
    ags = read_ags(text.encode())
    assert "AGS4-7" in {i.rule for i in ags.issues}
    assert ags["LOCA"].headings == ["LOCA_ID", "LOCA_GL", "LOCA_GL_2"]


def test_non_ascii_and_cp1252():
    text = minimal_ags(LOCS[:1], [("BH1", 0.0, 2.0, "Grey CLAY – soft", "ALV")])
    ags = read_ags(text.encode("cp1252"))
    assert ags.encoding == "cp1252" and "AGS4-1" in {i.rule for i in ags.issues}
    assert "–" in ags["GEOL"].column("GEOL_DESC")[0]


AGS3 = """"**PROJ"
"*PROJ_ID","*PROJ_NAME"
"P1","Test"
"**HOLE"
"*HOLE_ID","*HOLE_TYPE","*HOLE_NATE","*HOLE_NATN","*HOLE_GL","*HOLE_FDEP"
"<UNITS>","","m","m","m","m"
"BH1","CP","545000.00","180000.00","5.00","10.00"
"**GEOL"
"*HOLE_ID","*GEOL_TOP","*GEOL_BASE","*GEOL_DESC","*GEOL_GEOL"
"<UNITS>","m","m","",""
"BH1","0.00","3.00","Soft grey CLAY","ALV"
"BH1","3.00","10.00","Dense GRAVEL","RTD"
"""


def test_ags3_is_converted_to_ags4_names():
    ags = read_ags(AGS3.replace("\n", "\r\n").encode())
    assert ags.format.startswith("AGS3")
    assert "LOCA" in ags and "GEOL" in ags
    assert ags["LOCA"].column("LOCA_ID") == ["BH1"]
    assert ags["GEOL"].unit_of("GEOL_TOP") == "m"
    np.testing.assert_allclose(ags["GEOL"].float_column("GEOL_BASE"), [3.0, 10.0])


def test_write_ags4_quotes_and_crlf(tmp_path):
    p = tmp_path / "x.ags"
    write_ags4([tran(), table("PROJ", ["PROJ_ID", "PROJ_NAME"], [["1", 'A "quoted" name']])], p)
    raw = p.read_bytes()
    assert raw.endswith(b"\r\n") and b'"A ""quoted"" name"' in raw
