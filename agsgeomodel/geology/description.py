"""BS 5930-aware parser for soil and rock descriptions (GEOL_DESC).

The parser turns free text into a bag of weighted, human-readable features such as
``pt:clay`` (principal soil type), ``sec:sandy``, ``cons:stiff``, ``col:blue_grey``,
``incl:selenite``, ``struct:fissured``, ``chalk:graded`` or ``geo:LC`` (a named unit). Weights
reflect qualifiers in the text ("rare" 0.3, "occasional" 0.5, "frequent" 0.9, "slightly" 0.5,
"possibly" halves geological names). Negated inclusions ("no flint", "without shells") are
dropped. Features feed the explainable formation classifier; nothing here decides a formation.

It follows BS 5930:2015+A1:2020 conventions (principal constituent in capitals, secondary and
minor constituents as adjectives/"with ...") but degrades gracefully on lower-case legacy logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

PRINCIPALS = {
    "made ground": "made_ground",
    "topsoil": "topsoil",
    "clay": "clay",
    "silt": "silt",
    "sand": "sand",
    "gravel": "gravel",
    "cobbles": "cobbles",
    "boulders": "boulders",
    "peat": "peat",
    "chalk": "chalk",
    "mudstone": "mudstone",
    "siltstone": "siltstone",
    "sandstone": "sandstone",
    "limestone": "limestone",
    "claystone": "claystone",
    "flint": "flint",
    "diamicton": "diamicton",
    "conglomerate": "conglomerate",
}
_PRINCIPAL_RX = re.compile(
    r"\b(MADE GROUND|TOPSOIL|CLAY|SILT|SAND|GRAVEL|COBBLES|BOULDERS|PEAT|CHALK|MUDSTONE|"
    r"SILTSTONE|SANDSTONE|LIMESTONE|CLAYSTONE|FLINT|DIAMICTON|CONGLOMERATE)\b"
)
_PRINCIPAL_LOWER_RX = re.compile(
    r"\b(made ground|topsoil|clay|silt|sand|gravel|cobbles|boulders|peat|chalk|mudstone|"
    r"siltstone|sandstone|limestone|claystone|diamicton)\b"
)
_SECONDARY = [
    "sandy",
    "silty",
    "clayey",
    "gravelly",
    "cobbly",
    "bouldery",
    "organic",
    "peaty",
    "chalky",
    "shelly",
    "micaceous",
    "calcareous",
    "glauconitic",
    "carbonaceous",
    "flinty",
    "lignitic",
    "pyritic",
]
_SEC_RX = re.compile(r"\b(slightly|very|extremely)?\s*(" + "|".join(_SECONDARY) + r")\b")
_CONSISTENCY = [
    ("very soft", "very_soft"),
    ("very stiff", "very_stiff"),
    ("soft", "soft"),
    ("firm", "firm"),
    ("stiff", "stiff"),
    ("hard", "hard"),
]
_CONS_RX = re.compile(r"\b(very soft|very stiff|soft|firm|stiff|hard)\b")
_DENS_RX = re.compile(r"\b(very loose|medium dense|very dense|loose|dense)\b")
_STRENGTH_RX = re.compile(
    r"\b(extremely weak|very weak|moderately weak|weak|medium strong|moderately strong|"
    r"very strong|extremely strong|strong)\b"
)
_COLOURS = [
    (r"blue[\s-]*grey|bluish[\s-]*grey|grey[\s-]*blue", "blue_grey"),
    (r"greenish[\s-]*grey|green[\s-]*grey|grey[\s-]*green|olive[\s-]*grey|greenish", "greenish_grey"),
    (r"orang(?:e|ish)[\s-]*brown|brownish[\s-]*orange|orange", "orange_brown"),
    (r"yellow(?:ish)?[\s-]*brown|yellow", "yellow_brown"),
    (r"red(?:dish)?[\s-]*brown|brownish[\s-]*red", "red_brown"),
    (r"light[\s-]*brown|pale[\s-]*brown|buff", "light_brown"),
    (r"dark[\s-]*brown", "dark_brown"),
    (r"dark[\s-]*grey|black(?:ish)?[\s-]*grey", "dark_grey"),
    (r"grey[\s-]*brown|brown[\s-]*grey|greyish[\s-]*brown|brownish[\s-]*grey", "grey_brown"),
    (r"light[\s-]*grey|pale[\s-]*grey|off[\s-]*white", "light_grey"),
    (r"white", "white"),
    (r"cream", "cream"),
    (r"black", "black"),
    (r"purple|purplish|mauve", "purple"),
    (r"red|reddish", "red"),
    (r"green", "green"),
    (r"grey|gray", "grey"),
    (r"brown", "brown"),
    (r"mottled|mottling", "mottled"),
]
_COLOUR_RX = [(re.compile(r"\b(?:" + p + r")\b"), tok) for p, tok in _COLOURS]
_COLOUR_IMPLIES = {
    "grey_brown": ("grey", "brown"),
    "light_grey": ("grey",),
    "blue_grey": ("grey",),
    "dark_grey": ("grey",),
    "greenish_grey": ("grey",),
    "light_brown": ("brown",),
    "dark_brown": ("brown",),
    "orange_brown": ("brown",),
    "yellow_brown": ("brown",),
    "red_brown": ("brown",),
}
_INCLUSIONS = [
    (r"volcanic ash|tuff", "volcanic_ash"),
    (r"flints?|flinty", "flint"),
    (r"shells?|shelly|shell fragments", "shell"),
    (r"glauconit(?:e|ic)", "glauconite"),
    (r"lignit(?:e|ic)", "lignite"),
    (r"selenite|gypsum", "selenite"),
    (r"pyrit(?:e|ic)", "pyrite"),
    (r"claystone", "claystone"),
    (r"cementstone|septaria|septarian", "cementstone"),
    (r"mica|micaceous", "mica"),
    (r"rootlets?|roots", "rootlets"),
    (r"wood|plant remains|plant debris", "wood"),
    (r"brick", "brick"),
    (r"concrete", "concrete"),
    (r"(?<!volcanic )ash", "ash"),
    (r"clinker", "clinker"),
    (r"glass", "glass"),
    (r"metal|wire|steel|iron", "metal"),
    (r"plastic", "plastic"),
    (r"tarmac|asphalt|bitumen|bituminous", "tarmac"),
    (r"slag", "slag"),
    (r"ceramic|pottery|tile|crockery", "ceramic"),
    (r"timber", "timber"),
    (r"cobbles?|cobbly", "cobbles"),
    (r"boulders?|bouldery", "boulders"),
    (r"pebbles?", "pebbles"),
    (r"quartz", "quartz"),
    (r"coal", "coal"),
    (r"peat", "peat"),
    (r"organic", "organic"),
    (r"chalk", "chalk"),
]
_INCL_RX = [(re.compile(r"\b(?:" + p + r")\b"), tok) for p, tok in _INCLUSIONS]
_ANTHROPOGENIC = {"brick", "concrete", "ash", "clinker", "glass", "metal", "plastic", "tarmac", "slag", "ceramic"}
_FREQ = {
    "rare": 0.3,
    "occasional": 0.5,
    "some": 0.6,
    "scattered": 0.5,
    "frequent": 0.9,
    "many": 0.9,
    "numerous": 0.9,
    "abundant": 1.0,
    "much": 0.9,
    "traces of": 0.3,
    "trace": 0.3,
    "with": 0.7,
}
_FREQ_RX = re.compile(r"\b(rare|occasional|some|scattered|frequent|many|numerous|abundant|much|traces of|trace|with)\b")
_NEG_RX = re.compile(r"\b(no|without|free of|absence of|not)\s+(?:\w+\s+){0,2}$")
_STRUCTURES = [
    (r"very closely fissured|closely fissured", "closely_fissured"),
    (r"fissured|fissures", "fissured"),
    (r"thinly laminated|laminated|laminations?", "laminated"),
    (r"thinly bedded|bedded|bedding", "bedded"),
    (r"structureless", "structureless"),
    (r"putty", "putty"),
    (r"slickensided|slickensides", "slickensided"),
    (r"polished", "polished"),
    (r"sheared|shear surfaces?", "sheared"),
    (r"desiccat\w*", "desiccated"),
    (r"bioturbat\w*|burrowed", "bioturbated"),
    (r"fibrous", "fibrous"),
    (r"amorphous", "amorphous"),
    (r"fractured|fractures|jointed|joints", "fractured"),
    (r"voids?|cavit(?:y|ies)", "void"),
]
_STRUCT_RX = [(re.compile(r"\b(?:" + p + r")\b"), tok) for p, tok in _STRUCTURES]
_SHAPES = [
    (r"well rounded", "well_rounded"),
    (r"sub[\s-]?angular", "subangular"),
    (r"sub[\s-]?rounded", "subrounded"),
    (r"angular", "angular"),
    (r"rounded", "rounded"),
]
_SHAPE_RX = [(re.compile(r"\b(?:" + p + r")\b"), tok) for p, tok in _SHAPES]
_GRADE_RX = re.compile(r"\bgrade\s*([abc]|d[mc]?)\b")
_GRAIN_RX = re.compile(r"\b(fine|medium|coarse)\b(?=[^.]{0,30}\b(?:sand|gravel)\b)")
_GEO_TERMS = [
    (r"tidal", "tidal"),
    (r"estuarine", "estuarine"),
    (r"glacio[\s-]?fluvial|outwash", "glaciofluvial"),
    (r"glacial", "glacial"),
    (r"loess", "loess"),
    (r"bullhead", "bullhead"),
    (r"terrace", "terrace_gravel"),
]
_GEO_RX = [(re.compile(r"\b(?:" + p + r")\b"), tok) for p, tok in _GEO_TERMS]
_NO_RECOVERY_RX = re.compile(
    r"\b(no recovery|not recovered|core loss|lost core|no core|not logged|nr|no sample|"
    r"no description|unknown)\b"
)
_TENTATIVE_RX = re.compile(r"\b(possibly|possible|probably|probable|assumed|inferred|presumed)\b|\?")
_MG_RX = re.compile(r"\bmade ground\b|\bfill\b|\bhardcore\b|\breworked\b|\brubble\b")
_NAME_STOPLIST = {
    "chalk",
    "peat",
    "fill",
    "white chalk",
    "putty chalk",
    "structureless chalk",
    "diamicton",
    "turf",
    "outwash",
    "loess",
    "colluvium",
    "hillwash",
    "mottled clay",
    "upper chalk",
    "middle chalk",
    "lower chalk",
    "terrace gravel",
    "river gravel",
    "amorphous peat",
    "fibrous peat",
    "alluvial clay",
}


@dataclass
class ParsedDescription:
    text: str
    features: dict[str, float] = field(default_factory=dict)
    principals: list[str] = field(default_factory=list)
    named_units: list[tuple[str, str, bool]] = field(default_factory=list)  # (code, alias, tentative)
    no_information: bool = False

    @property
    def material(self) -> str:
        if not self.principals:
            return "unknown"
        if "made_ground" in self.principals:
            return "made_ground"
        if len(self.principals) > 1 and {"sand", "gravel"} <= set(self.principals):
            return "sand_gravel"
        return self.principals[0]

    def top_features(self, n: int = 10) -> list[tuple[str, float]]:
        return sorted(self.features.items(), key=lambda kv: -kv[1])[:n]


def _add(feats: dict[str, float], key: str, w: float) -> None:
    feats[key] = max(feats.get(key, 0.0), round(float(w), 3))


def _freq_before(text: str, start: int) -> float:
    window = text[max(0, start - 45) : start]
    ms = list(_FREQ_RX.finditer(window))
    return _FREQ[ms[-1].group(1)] if ms else 0.7


def _negated(text: str, start: int) -> bool:
    return bool(_NEG_RX.search(text[max(0, start - 30) : start]))


@lru_cache(maxsize=65536)
def _parse_cached(text: str, kb_version: str) -> ParsedDescription:  # kb_version keys the cache
    from .knowledge import default_kb

    return _parse(text, default_kb())


def parse_description(text: str, kb=None) -> ParsedDescription:
    """Parse a description. Results for the bundled knowledge base are cached (logs repeat a lot)."""
    if kb is None:
        from .knowledge import default_kb

        kb = default_kb()
        return _parse_cached(text or "", kb.version)
    return _parse(text or "", kb)


def _parse(text: str, kb) -> ParsedDescription:
    raw = " ".join(text.split())
    low = raw.lower()
    pd = ParsedDescription(raw)
    f = pd.features
    if not low.strip() or (len(low) < 40 and _NO_RECOVERY_RX.search(low)):
        pd.no_information = True
        _add(f, "rec:none", 1.0)
        return pd
    # ---- principal constituent(s)
    upper_hits = [m.group(1).lower() for m in _PRINCIPAL_RX.finditer(raw)]
    if upper_hits:
        first = upper_hits[0]
        pd.principals.append(PRINCIPALS[first])
        rest = [PRINCIPALS[h] for h in upper_hits[1:3] if PRINCIPALS[h] not in pd.principals]
        # "SAND and GRAVEL" / "CLAY and SILT": composite principal
        if rest and re.search(
            r"\b" + re.escape(upper_hits[0].upper()) + r"\s+(and|&)\s+" + re.escape(upper_hits[1].upper()) + r"\b", raw
        ):
            pd.principals.append(rest[0])
    else:
        sentence = re.split(r"[.;]", low, maxsplit=1)[0]
        head = re.split(r"\bwith\b|,", sentence, maxsplit=1)[0]
        cands = list(_PRINCIPAL_LOWER_RX.finditer(head)) or list(_PRINCIPAL_LOWER_RX.finditer(sentence))
        if cands:
            last = cands[-1].group(1)
            pd.principals.append(PRINCIPALS[last])
            if len(cands) > 1 and re.search(r"\b" + cands[-2].group(1) + r"\s+(and|&)\s+" + last + r"\b", head):
                pd.principals.insert(0, PRINCIPALS[cands[-2].group(1)])
    if _MG_RX.search(low) or "made_ground" in pd.principals:
        if "made ground" in low or "made_ground" in pd.principals:
            _add(f, "pt:made_ground", 1.0)
            if "made_ground" not in pd.principals:
                pd.principals.insert(0, "made_ground")
        for m in _MG_RX.finditer(low):
            tok = m.group(0)
            if tok == "fill":
                _add(f, "mg:fill", 0.8)
            elif tok in ("hardcore", "rubble"):
                _add(f, "mg:hardcore", 0.9)
            elif tok == "reworked":
                _add(f, "mg:reworked", 0.7)
    w_principal = 1.0 / max(1, len(pd.principals))
    for p in pd.principals:
        if p != "made_ground":
            _add(f, f"pt:{p}", 1.0 if len(pd.principals) == 1 else max(0.6, w_principal))
    if "topsoil" in low:
        _add(f, "pt:topsoil", 1.0)
    # ---- secondary constituents
    for m in _SEC_RX.finditer(low):
        mod, adj = m.group(1), m.group(2)
        _add(f, f"sec:{adj}", 0.5 if mod == "slightly" else 1.0 if mod in ("very", "extremely") else 0.8)
    # ---- consistency / density / strength (ranges such as "firm to stiff" give both terms)
    cons = [m.group(1) for m in _CONS_RX.finditer(low)]
    for c in dict.fromkeys(cons):
        # "hard" in "hardcore" is excluded by \b; "hard" for rock strength is rare in soils logs
        _add(f, "cons:" + c.replace(" ", "_"), 0.6 if len(set(cons)) > 1 else 1.0)
    dens = [m.group(1) for m in _DENS_RX.finditer(low)]
    for d in dict.fromkeys(dens):
        _add(f, "dens:" + d.replace(" ", "_"), 0.6 if len(set(dens)) > 1 else 1.0)
    for m in _STRENGTH_RX.finditer(low):
        _add(f, "str:" + m.group(1).replace(" ", "_"), 0.8)
    # ---- colours
    taken: list[tuple[int, int]] = []
    for rx, tok in _COLOUR_RX:
        for m in rx.finditer(low):
            if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                continue
            taken.append(m.span())
            _add(f, f"col:{tok}", 0.8)
            for implied in _COLOUR_IMPLIES.get(tok, ()):
                _add(f, f"col:{implied}", 0.4)
    # ---- inclusions (with frequency and negation handling)
    principal_words = set(pd.principals)
    taken = []
    any_mg = 0.0
    for rx, tok in _INCL_RX:
        for m in rx.finditer(low):
            if any(not (m.end() <= a or m.start() >= b) for a, b in taken):
                continue
            taken.append(m.span())
            if tok in principal_words:
                # the principal constituent itself is not an inclusion ("weak white CHALK ... chalk")
                continue
            if _negated(low, m.start()):
                continue
            w = _freq_before(low, m.start())
            _add(f, f"incl:{tok}", w)
            if tok in _ANTHROPOGENIC:
                any_mg = max(any_mg, w)
    if any_mg or "pt:made_ground" in f:
        _add(f, "mg:any", max(any_mg, 1.0 if "pt:made_ground" in f else 0.0))
    # ---- structures, shapes, grain size, chalk grade
    for rx, tok in _STRUCT_RX:
        if rx.search(low):
            _add(f, f"struct:{tok}", 0.8)
    for rx, tok in _SHAPE_RX:
        if rx.search(low):
            _add(f, f"shape:{tok}", 0.7)
    for m in _GRAIN_RX.finditer(low):
        _add(f, f"grain:{m.group(1)}", 0.6)
    g = _GRADE_RX.search(low)
    if g and ("chalk" in low):
        _add(f, "chalk:graded", 1.0)
        _add(f, f"chalk:grade_{g.group(1)}", 1.0)
    # ---- geological terms and named units
    tentative_spans = [m.span() for m in _TENTATIVE_RX.finditer(low)]
    for rx, tok in _GEO_RX:
        if rx.search(low):
            _add(f, f"geo:{tok}", 0.8)
    for code, alias in kb.find_in_text(low):
        if alias in _NAME_STOPLIST:
            continue
        pos = low.find(alias)
        tentative = any(abs(pos - s) < 25 for s, _ in tentative_spans) if pos >= 0 else False
        bracketed = bool(re.search(r"[\(\[][^\)\]]*" + re.escape(alias) + r"[^\)\[]*[\)\]]", low))
        pd.named_units.append((code, alias, tentative))
        w = (1.0 if bracketed else 0.8) * (0.5 if tentative else 1.0)
        _add(f, f"geo:{code}", w)
    return pd
