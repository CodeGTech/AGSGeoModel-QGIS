"""Registry of BGS services used by the connector.

Endpoints marked ``verified`` were checked against the live services (October 2026). BGS labels its
OGC API service BETA ("endpoints and attributes could change"), so the registry is data, not code:
users can override any entry in the plugin settings without a new release.
"""

from __future__ import annotations

OGC_API = "https://ogcapi.bgs.ac.uk"
AGS_API = "https://agsapi.bgs.ac.uk/v7"
LEXICON_HTML = "https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub={code}"
LEXICON_LD = "https://data.bgs.ac.uk/id/Lexicon/NamedRockUnit/{code}"
ATTRIBUTION = "Contains British Geological Survey materials © UKRI {year}"

COLLECTIONS = {
    # key: (collection id, kind, notes)
    "bedrock_625k": ("bgsgeology625kbedrock", "polygon", "BGS Geology 625k bedrock (generalised); OGL"),
    "superficial_625k": ("bgsgeology625ksuperficial", "polygon", "BGS Geology 625k superficial deposits"),
    "faults_625k": ("bgsgeology625kfaults", "line", "BGS Geology 625k faults"),
    "dykes_625k": ("bgsgeology625kdykes", "line", "BGS Geology 625k dykes"),
    "boreholes": ("onshoreboreholeindex", "point", "Single Onshore Borehole Index (scanned logs, no data)"),
    "ags_boreholes": ("agsboreholeindex", "point", "AGS data held by BGS (downloadable via the AGS API)"),
    "landslides": ("landslideindex", "point", "National Landslide Database index"),
    "maps_250k": ("scanned-maps-250k", "polygon", "Scanned 1:250k geological map sheets (citations)"),
    "maps_500k": ("scanned-maps-500k", "polygon", "Scanned 1:500k geological map sheets"),
}

WMS_LAYERS = {
    "BGS 50k bedrock": (
        "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer",
        "BGS.50k.Bedrock",
    ),
    "BGS 50k superficial deposits": (
        "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer",
        "BGS.50k.Superficial.deposits",
    ),
    "BGS 50k artificial ground": (
        "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer",
        "BGS.50k.Artificial.ground",
    ),
    "BGS 50k mass movement": (
        "https://map.bgs.ac.uk/arcgis/services/BGS_Detailed_Geology/MapServer/WMSServer",
        "BGS.50k.Mass.movement",
    ),
}

# Not verified at release: supplied by the user in settings (no endpoint is invented here).
UNVERIFIED = {
    "hydrogeology": {
        "url": None,
        "note": "BGS hydrogeology / aquifer designation services: configure a WMS or "
        "OGC API URL in Settings > BGS services. Environment Agency aquifer "
        "designations are the authoritative source in England.",
    },
}

VERIFIED = {
    "ogc_api": OGC_API,
    "ags_api": AGS_API,
    "lexicon": LEXICON_HTML,
    "wms_50k": WMS_LAYERS["BGS 50k bedrock"][0],
}
