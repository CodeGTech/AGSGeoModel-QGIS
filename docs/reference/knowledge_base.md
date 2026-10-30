# Knowledge base

Generated from `agsgeomodel/geology/knowledge/data` by `scripts/gen_reference_docs.py`.

Knowledge base version `2026.10.0`: 22 units keyed to BGS Lexicon codes.

| Code | Name | Class | Material | Age | Hydrogeology | Lexicon |
|---|---|---|---|---|---|---|
| `ALV` | Alluvium | superficial | clay_silt_organic | Quaternary (mainly Holocene) | aquitard | [ALV](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=ALV) |
| `BRBA` | Bracklesham Group and Barton Group (Undifferentiated) | bedrock | mixed | Eocene | variable | [BRBA](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=BRBA) |
| `BRK` | Brickearth | superficial | silt | Quaternary (mainly Devensian) | aquitard | [BRK](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=BRK) |
| `CK` | Chalk Group | bedrock | chalk | Cenomanian to Maastrichtian (Late Cretaceous) | aquifer | [CK](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=CK) |
| `GSG` | Glacial Sand and Gravel | superficial | sand_gravel | Quaternary | aquifer | [GSG](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=GSG) |
| `HEAD` | Head | superficial | mixed | Quaternary | variable | [HEAD](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=HEAD) |
| `HWH` | Harwich Formation | bedrock | sand | Ypresian (Eocene) | variable | [HWH](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=HWH) |
| `KPGR` | Kempton Park Gravel Member | superficial | sand_gravel | Devensian | aquifer | [KPGR](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=KPGR) |
| `LC` | London Clay Formation | bedrock | clay | Ypresian (Eocene) | aquiclude | [LC](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=LC) |
| `LMBE` | Lambeth Group | bedrock | mixed | Thanetian to Ypresian | variable | [LMBE](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=LMBE) |
| `LOFT` | Lowestoft Formation | superficial | diamicton | Anglian | aquitard | [LOFT](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=LOFT) |
| `MGR` | Made Ground | artificial | made_ground | Anthropocene (historical) | variable | [MGR](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=MGR) |
| `PEAT` | Peat | superficial | peat | Quaternary | aquitard | [PEAT](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=PEAT) |
| `RB` | Reading Formation | bedrock | clay | Thanetian to Ypresian | aquitard | [RB](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=RB) |
| `RTDU` | River Terrace Deposits (Undifferentiated) | superficial | sand_gravel | Quaternary (Pleistocene) | aquifer | [RTDU](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=RTDU) |
| `TAB` | Thanet Formation | bedrock | fine_sand | Thanetian (Late Paleocene) | aquifer | [TAB](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=TAB) |
| `THAM` | Thames Group | bedrock | clay | Ypresian (Eocene) | aquiclude | [THAM](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=THAM) |
| `TILL` | Till | superficial | diamicton | Quaternary | aquitard | [TILL](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=TILL) |
| `TOPSOIL` | Topsoil | artificial | topsoil | Holocene | variable | - |
| `UPR` | Upnor Formation | bedrock | sand | Thanetian | aquifer | [UPR](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=UPR) |
| `WHCK` | White Chalk Subgroup | bedrock | chalk | Cenomanian to Maastrichtian | aquifer | [WHCK](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=WHCK) |
| `WL` | Woolwich Formation | bedrock | clay | Thanetian to Ypresian | variable | [WL](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=WL) |

## Province column `east_anglia_glacial`

**London Basin margin with Anglian glacial cover (north Essex / south Suffolk).** Palaeogene-over-Chalk bedrock beneath Anglian Lowestoft Formation till and glaciofluvial sand and gravel, with Holocene alluvium and terrace deposits in valleys. Till and outwash interfinger, so the glacial package is a facies association (units may repeat) rather than a strict layer cake. Priors are indicative only.

| Sequence | Kind | Order | Units (top to base) |
|---|---|---|---|
| `surface` | artificial | facies | TOPSOIL > MGR |
| `postglacial` | superficial | facies | ALV > PEAT > HEAD > RTDU |
| `glacial` | superficial | facies | LOFT > GSG |
| `bedrock` | bedrock | strict | LC > HWH > LMBE > TAB > CK |

Aquifer systems:

* `shallow`: aquifer units RTDU, GSG; confined by ALV, PEAT, LOFT
* `deep`: aquifer units TAB, CK, HWH, LMBE; confined by LC

Maximum apparent dip of conformable bedrock contacts 3.0 deg; rockhead 20.0 deg.

## Province column `london_basin`

**London Basin (central and eastern, including the Thames estuary).** Default column for Palaeogene-over-Chalk ground in the central and eastern London Basin with Quaternary fluvial and estuarine cover. Presence probabilities and thicknesses are regional priors to be replaced by site-specific values from the BGS sheet explanation / memoir and nearby records.

| Sequence | Kind | Order | Units (top to base) |
|---|---|---|---|
| `surface` | artificial | facies | TOPSOIL > MGR |
| `superficial` | superficial | facies | ALV > PEAT > HEAD > BRK > RTDU |
| `bedrock` | bedrock | strict | LC > HWH > LMBE > TAB > CK |

Aquifer systems:

* `shallow`: aquifer units RTDU; confined by ALV, PEAT
* `deep`: aquifer units TAB, CK, HWH, LMBE; confined by LC

Maximum apparent dip of conformable bedrock contacts 3.0 deg; rockhead 15.0 deg.
