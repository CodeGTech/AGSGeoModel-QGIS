"""Typed configuration objects.

All settings that influence an interpretation or a model are explicit dataclasses with documented
defaults. They are serialised into every ``model_runs`` record so any output can be regenerated.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


def _from_dict(cls: type[T], data: dict[str, Any] | None) -> T:
    if data is None:
        return cls()
    kwargs = {}
    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        if f.name not in data:
            continue
        val = data[f.name]
        ftype = f.type if isinstance(f.type, type) else None
        default = f.default_factory() if f.default_factory is not dataclasses.MISSING else None  # type: ignore[misc]
        if dataclasses.is_dataclass(default) and isinstance(val, dict):
            val = _from_dict(type(default), val)
        elif ftype is not None and dataclasses.is_dataclass(ftype) and isinstance(val, dict):
            val = _from_dict(ftype, val)
        kwargs[f.name] = val
    return cls(**kwargs)  # type: ignore[call-arg]


class _Serialisable:
    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)  # type: ignore[arg-type]

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, **kw)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None):
        return _from_dict(cls, data)

    def updated(self, **changes):
        return dataclasses.replace(self, **changes)  # type: ignore[type-var]


@dataclass
class InterpolationConfig(_Serialisable):
    method: str = "kriging"  # kriging | rbf | idw | tin
    variogram_model: str = "auto"  # auto | spherical | exponential | gaussian | linear
    n_neighbours: int = 16
    drift: str = "constant"  # constant (ordinary) | linear (universal kriging)
    rbf_kernel: str = "thin_plate"  # thin_plate | linear | cubic | gaussian | multiquadric
    idw_power: float = 2.0
    max_inequality_iterations: int = 8


@dataclass
class UncertaintyConfig(_Serialisable):
    observed_radius_m: float = 2.0  # within this of a primary observation -> Observed
    interpolated_max_sigma_m: float = 1.0  # sigma <= this with primary support -> Interpolated
    inferred_max_sigma_m: float = 3.0  # sigma <= this -> Inferred; above -> Speculative
    confidence_scale_m: float = 3.0  # confidence = exp(-sigma / scale)
    green_min_confidence: float = 0.70
    amber_min_confidence: float = 0.40


@dataclass
class ModelConfig(_Serialisable):
    cell_size_m: float | None = None  # None -> automatic (about 150 cells across)
    buffer_m: float = 250.0  # model extent = boreholes + buffer
    interpolation: InterpolationConfig = field(default_factory=InterpolationConfig)
    bedrock_method: str = "isochore"  # isochore (conformable stacking) | independent
    reference_surface: str | None = None  # e.g. "base:LC"; None -> most observed surface
    respect_faults: bool = True
    use_map_constraints: bool = True
    map_tolerance_m: float = 50.0  # positional tolerance of map linework (scale dependent)
    primary_sigma_m: float = 0.15  # AGS logged contact (levelling + depth logging)
    secondary_sigma_m: float = 1.0  # digitised BGS / legacy records
    map_sigma_m: float = 2.0  # contacts inferred from geological map linework
    min_mapped_thickness_m: float = 0.5  # unit shown on map is at least this thick there
    monte_carlo_realisations: int = 0  # 0 = deterministic only
    seed: int = 20261028
    uncertainty: UncertaintyConfig = field(default_factory=UncertaintyConfig)
    max_apparent_dip_deg: float = 3.0  # conformable bedrock contacts (province override)
    rockhead_max_apparent_dip_deg: float = 15.0


@dataclass
class ReasonerConfig(_Serialisable):
    stated_code_log_lr: float = 4.0  # GEOL_BGS lexicon code adopted by the logger
    stated_geol_log_lr: float = 3.0  # GEOL_GEOL code mapped through aliases
    stated_text_log_lr: float = 3.0  # formation named in GEOL_FORM / description text
    tentative_factor: float = 0.5  # "possibly ...", "?" halves the stated weight
    temperature: float = 1.0  # calibration of description evidence
    stay_base: float = 0.85  # base probability that a unit continues downwards
    reverse_order_weight: float = 0.25  # superficial (facies) sequences: atypical stacking
    omission_presence_threshold: float = 0.5
    review_below_probability: float = 0.6  # posterior below this -> flagged for review
    unknown_split_m: float = 1.0  # no-recovery intervals are split into these slices
    allow_repetition: list[str] = field(default_factory=list)  # LOCA_IDs with structural repeats
    repetition_probability: float = 1e-4
    neighbour_elevation_sigma_m: float = 3.0  # second-pass elevation prior from neighbours


@dataclass
class HydroConfig(_Serialisable):
    strike_weight: float = 0.3  # water strikes are not equilibrated
    monitoring_weight: float = 1.0
    perched_tolerance_m: float = 0.5
    streamline_seeds: int = 25


@dataclass
class HDDConfig(_Serialisable):
    station_spacing_m: float = 1.0
    product_od_m: float = 0.25
    ream_factor: float = 1.5  # final reamed hole = factor x product OD
    mixed_face_tolerance_m: float = 1.0
    min_cover_m: float = 3.0
    low_cover_ratio: float = 10.0  # cover / reamed diameter below this -> frac-out flag


@dataclass
class ProjectSettings(_Serialisable):
    name: str = "AGSGeoModel project"
    crs: str = "EPSG:27700"
    vertical_datum: str = "ODN"
    province: str = "london_basin"
    buffer_options_m: list[float] = field(default_factory=lambda: [100.0, 250.0, 500.0, 1000.0, 5000.0])
    model: ModelConfig = field(default_factory=ModelConfig)
    reasoner: ReasonerConfig = field(default_factory=ReasonerConfig)
    hydro: HydroConfig = field(default_factory=HydroConfig)
    hdd: HDDConfig = field(default_factory=HDDConfig)
