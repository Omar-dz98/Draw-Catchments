# Test-data provenance

Retrieved 12 October 2026. No production-well coordinates or official catchment
boundaries were invented. Artificial inputs are explicitly named `synthetic_*`.

| Dataset | Date and CRS | Source and processing | Limitation |
|---|---|---|---|
| Groundwater contours, `GWD_2015F` | Spring 2015; revision 12/2022; documentation generated 25 July 2024; EPSG:25833 | [LfU/LGB official ZIP](https://data.geobasis-bb.de/geofachdaten/Wasser/Grundwasser/gwd_2015.zip); `ZLEVEL` is head in m NHN; `ZEITPKT=2015F`. Subsets clipped to a 6 km buffer around each well-group extent, retaining original attributes. | Regional overview, approximately 1:50,000–1:300,000. Not a local calibrated pumping model. Historic snapshot, not current heads. |
| Belzig II production wells | Ordinance 18 August 2025; EPSG:25833 | [Amtsblatt Potsdam-Mittelmark 5/2025](https://www.potsdam-mittelmark.de/files/images/aktuelles-veranstaltungen/bekanntmachungen/amtsblatt/2025/amtsblatt-pm-2025-5.pdf), page 7, Annex 2 coordinate table. Numeric transcription of wells IA, II, III, IV. | No abstraction rates or screened aquifer intervals used. The head dataset predates the ordinance by ten years. |
| Eichwalde production wells | Ordinance 2 August 2001; source EPSG:31468 | [BRAVORS official table](https://bravors.brandenburg.de/de/verordnungen-258286), Annex 1. Nine numeric coordinate pairs transcribed, transformed to EPSG:25833 with QGIS/PROJ. Source easting/northing retained. | Historic positions. Its unresolved depression is a rejection example with this regional contour dataset. Not used for a successful catchment demonstration. |
| Water-protection zones | 31 July 2026; documentation 29 September 2026; EPSG:25833 | [Official WSG ZIP](https://data.geobasis-bb.de/geofachdaten/Wasser/Gewaesserbewirtschaftung/wsg.zip); selected `KLARNAME` values Belzig II and Eichwalde. | Legal protection zones, **not groundwater capture zones**. Downloaded documentation states non-commercial usage. Retained locally, excluded from the shareable source/demo archive and plugin ZIP. |
| Synthetic contours and points | Generated test fixtures; EPSG:25833 coordinate labels | `tests/test_engine.py`: planar, curved and sinusoidally irregular analytical head fields. | Artificial coordinates and heads. Never identified as real wells or official data. |

## Attribution and rights

Contour data: **Daten des Landesamts für Umwelt (LfU Brandenburg), Frühjahr
2015, Bearbeitungsstand 12/2022**, [Datenlizenz Deutschland – Namensnennung –
Version 2.0](https://www.govdata.de/dl-de/by-2-0). The supplied contour documentation
explicitly specifies this license. Derived clips and demonstration proposals are
identified as modifications; the proposals are not LfU outputs.

The downloaded WSG documentation instead states unrestricted **non-commercial**
use with attribution. Because of this discrepancy with general portal open-data
statements, the public package conservatively excludes those files. They are
optional local context and are not referenced by the portable demo project.
Check the dataset-specific terms before any wider WSG redistribution.

The ordinance coordinate tables are official published factual sources. Original
documents/URLs are retained for checking transcription, dates and CRS. The
40/83 Bessel GK4 mapping to EPSG:31468 is corroborated by the
[Brandenburg GDI reference-system guide](https://geoportal.brandenburg.de/fileadmin/publicdata/gdi-brandenburg/veranstaltungen/2024-07-11_GDI-Forum_2024/15_Metadaten_ProMIS_LGB.pdf).

## Search decisions

Storkow was investigated first. The available official waterworks information,
protection-area geometry and 2016 public-hearing notice did not provide a
verified analytical production-well coordinate dataset. The user subsequently
confirmed any suitable location was acceptable.

Eichwalde supplied documented coordinates, but its regional minimum-head contour
does not resolve the local depression sufficiently. The algorithm rejects it.
Belzig II offers explicit ETRS89/UTM33 coordinates and usable regional gradients.
The final demonstration uses well II and the group of four wells, with all
assumptions retained in exported JSON.

Direct vector downloads were available, so WMS imagery was not used as analytical
data and WFS extraction was unnecessary. No verified waterworks-specific
hydrogeological capture boundary was obtained. No quantitative agreement with
water-protection zones is presented as hydrogeological validation.

## Public regression fixtures

The repository contains the prepared contour clips and well tables needed by
the automated tests. The two station subsets have adjacent SOURCE.txt files
with their provenance. Original downloads and water-protection-zone datasets
are not redistributed here. Synthetic fixtures are created by the test code;
the plugin also includes a small synthetic GeoPackage. Dataset licenses are
separate from the GPL license of the plugin source.
