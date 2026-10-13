# Draw Catchments

## Version 0.1.4: trace upstream and follow the contour cap

The 0.1.4 correction checks turns before accepting a tracing step, preventing
the premature stop seen at Strausberg West. With **Highest connected contour
(follow its shape)**, both boundaries cross successive isohypses upstream and
the cap follows the actual connected contour between their intersections.
The separate **Upstream contour closure** line can be inspected and exported.
The chosen cap and untrimmed stop reasons are reported. This does not identify
a groundwater divide or force both paths to the globally highest map contour.

1. Install `Draw_Catchments-0.1.4.zip` and restart QGIS. The dialog heading must
   show **Draw Catchments 0.1.4**.
2. Choose your groundwater contour line layer and numeric head field.
3. Click **Pick one point on map**, then click the point you want. The dialog
   hides during picking and reopens with **Use: One point** and a target count
   of **1**. Other selected features are excluded. Overlapping features offer
   a choice. Esc or right-click cancels picking.
4. Clicking an empty location uses that exact coordinate as a temporary target;
   no pre-existing well layer is required. It is labelled as a user-picked
   location, not an official well. You can also search individual project
   features in **Point**, or use the Pan tool's map right-click menu.
5. Click **Draw catchment**. Export a successful result before closing QGIS.

The tool can target any location. A groundwater catchment proposal still needs
suitable contour geometry with hydraulic-head values around that location.
It cannot reconstruct groundwater gradients from a point or a WMS image alone.

Calculation CRS is selected automatically from suitable metre-based input data,
or a local UTM/polar CRS for geographic/Web-Mercator inputs. Clear the automatic
CRS checkbox under Calculation settings to choose your own projection.

Automatic width now tries smaller geometric envelopes when the first boundary
fails. It reports the actual width and any reduction. Manual width and adjusted
starts remain exact user choices. Upstream cap selection validates containment,
topology and coverage at shorter contours before rejecting construction.
Curved-contour interpolation uses a bounded two-ring neighborhood for triangles
whose three vertices share a head, retaining observed-head and distance limits.
Consequently new results can extend beyond the caps used in earlier versions.


Created by **Omar Brahimi**.

**Experimental QGIS plugin, version 0.1.4. English interface.** Draws editable,
preliminary groundwater catchment polygons from hydraulic-head contour lines
and selected abstraction wells. Developed and tested with QGIS 3.36.3,
Python 3.12.3, NumPy 1.26.4 and SciPy 1.13.0 on Windows.

## Install and try it in two minutes

1. Open QGIS. Choose **Plugins → Manage and Install Plugins → Install from ZIP**.
2. Select `Draw_Catchments-0.1.4.zip`, click **Install Plugin**, and enable
   **Draw Catchments** under Installed if necessary.
3. Open **Vector → Draw Catchments → Draw Catchments**, or click its orange/blue
   catchment toolbar icon.
4. Click **Load bundled test example**. This adds artificial contours and one
   selected artificial well to your current project. No internet is required.
5. Click **Draw catchment**. Expect an orange polygon and two green upstream
   paths. The polygon contains the well. The hidden original is retained.
6. Click **Edit catchment**, move a vertex with QGIS's vertex tool, then click
   **Export catchment…** and choose a new `.gpkg` filename.

QGIS may display its normal warning that plugins execute Python code. The ZIP
contains readable source, a GPL license, and synthetic GIS data; no executables,
network calls, telemetry, credentials, or automatic dependency installation.

## Try real Brandenburg data

Extract `Draw_Catchments_Source_and_Demo.zip` into a writable folder and open
`Draw_Catchments/outputs/Draw_Catchments_Belzig_II.qgz`. Keep its folder structure
intact because the project uses relative data paths. The four-well result is
visible; the single-well result is a separate hidden group.

For a fresh calculation:

1. Open Draw Catchments. Choose **Belzig II head contours** and `ZLEVEL`.
2. Choose **Belzig II wells** and `well_id`. Select **well II** on the map for the
   single-well test, or choose **All wells** for the four-well test.
3. Use the default real-data settings: analysis radius **5000 m**, sampling
   **30 m**, step **20 m**, maximum support gap **2000 m**, maximum path length
   **10000 m**, CRS **EPSG:25833**. The bundled synthetic example changes these
   settings, so restore them under Calculation settings when switching to real data.
4. Leave automatic width and highest usable contour selected; click **Draw catchment**.

The expected real examples are roughly **0.910 km²** for well II and **1.450 km²**
for all four wells. These are geometric examples, not accepted catchment areas.
The current cap at **101 m NHN** is artificial, not an identified groundwater divide.
Some individual wells, notably well IA with these settings, are rejected because
the proposed boundary crosses unsupported interpolation areas. This is an
intentional limitation; obtain better local contours or adjust the starts/limit.

## Use your own contour map

Load a line layer and a point layer into QGIS. Select your wells. Choose the
contour layer, its numeric hydraulic-head field, the well layer and its identifier.
Use **Selected wells** or **All wells**. Choose a suitable projected calculation
CRS in metres. EPSG:25833 is the Brandenburg default, not a global default.
Both input layers are transformed from their correctly assigned source CRSs.
No transformation of vertical datums is performed: all heads must already share
one unit (metres), datum, date/period and appropriate aquifer.

At least three distinct head levels with meaningful two-dimensional coverage are
required. Input features inside the analysis window must have finite numeric
heads and valid geometry. A map image/WMS alone is not an analytical input.

For the shortest workflow, click **Draw catchment** directly. For width review,
click **Suggest width / create start points**, then choose **Manual width** and
change the proposed value before calculating. The proposal is a geometric
assumption: it does not estimate hydraulic capture width from abstraction rates.

Selected wells form one shared group. Calculate separate selections for groups
with different flow directions. The plugin rejects a group if its local
directions differ too strongly or its paths cannot enclose every well.

## Adjust geometry

- **Width:** choose Manual width and recalculate. Increasing width is not always
  appropriate: inspect the available contour coverage.
- **Starting points:** create them with the suggestion button; select that point
  layer, toggle normal QGIS editing, move its two points, and enable
  **Use adjusted start-point layer**. Recalculate. Exactly two single points are required.
- **Stopping limit:** choose the highest usable contour, the current map extent,
  or a line/polygon boundary layer. If boundary features are selected, only those
  features are used. A polygon boundary acts as a study-area perimeter; starts
  and wells must lie inside it. A line boundary should cross both upstream paths.
- **Polygon:** click Edit catchment and use standard QGIS tools. The original and
  calculation paths are read-only copies. Turn on the hidden original to compare.
  Repeated calculations create new result groups; older groups remain available.

Results are **temporary layers until exported**. Saving only a QGIS project does
not make memory-layer geometries permanent. Export before closing QGIS.

## Export

GeoPackage is recommended: it contains `catchment`, `original`, `paths`, and
`starts` layers. Shapefile and GeoJSON export the current polygon. Export never
silently overwrites an existing output; choose a new filename.

Every format gets a companion `<filename>.<extension>.drawcatchments.json` with
the full settings, run ID, well identifiers and coordinates, UTC calculation date,
CRS WKT, width assumptions, termination reasons and edit status when available.
Keep the JSON file with the GIS file. Shapefile attribute lengths are limited;
the sidecar preserves the complete values. GeoJSON follows RFC 7946 and is
transformed to WGS84; its original calculation CRS remains in the sidecar.
GeoPackage and Shapefile retain the calculation CRS.

Exports reject empty/invalid polygons and edits which exclude a selected well.
GeoPackage preserves original and corrected geometries separately. To continue
editing later, load its `catchment` layer in QGIS and use normal QGIS editing.

## Method and limitations

The plugin samples contours, constructs a SciPy Clough-Tocher C1 head surface on
a Delaunay triangulation, and traces the normalized **positive** head gradient
upstream using adaptive midpoint integration. It reduces steps at large turns,
requires increasing head, and excludes extrapolation, triangles spanning the
maximum support gap, and flat triangles without a supported adjacent head range. Cubic
overshoot beyond a small tolerance is rejected.

Local contour interval divided by gradient magnitude estimates contour spacing.
The default width is the selected group's cross-flow span plus half that spacing.
The start bar is placed slightly downstream of every selected well. The coefficient
is an adjustable geometric heuristic. Curvature affects the interpolated gradient
and resulting paths; no independent calibrated curvature-to-width law is claimed.

The lateral paths, an upstream arc along the chosen actual contour and a
straight downstream start bar form the polygon. Custom/map limits retain their
straight endpoint connection. GEOS checks validity, self-intersections and well containment.
Unsupported boundary samples and interior gaps are checked. If needed, default
termination backs down to the highest connected contour that forms a supported polygon. Failed construction
is rejected, not repaired by replacing it with a convex hull or arbitrary buffer.

Assumptions: isotropy; a suitable continuous head surface; no pumping-rate,
recharge, anisotropy, aquifer-layer, travel-time or uncertainty model. The tool
does not infer a verified groundwater divide. Sparse contours, pumping depressions,
saddles and strongly divergent groups may produce warnings or no result.
Sampling and interpolation tolerances remain numerical approximations.

The Brandenburg data are regional, spring-2015 contours revised in 2022. The
Belzig well coordinates come from a 2025 ordinance. Their combination tests the
software; it does not establish the hydraulic situation in 2025 or today.
Water-protection zones are contextual legal boundaries, not capture-zone truth.

## Dependencies and compatibility

Requires QGIS 3.36–3.x with NumPy and SciPy available in **QGIS's own Python**.
They are present in the tested Windows installation. The plugin checks them and
shows an explanatory message if missing. Use the QGIS/OSGeo4W installer to add
the matching scientific Python packages; on distro-based Linux use the matching
QGIS/Python package manager. Do not install packages into an unrelated system Python.
Other QGIS versions, Linux/macOS and QGIS 4 have not been tested. QGIS 4 is excluded
in metadata. This is an experimental public-test release, not a validated modelling tool.

## Source, testing and publication

Runtime source: `plugin/draw_catchments/`; the numerical engine is in its `core/`
subdirectory so the plugin is self-contained. `tests/` contains numerical,
PyQGIS/Qt integration, data preparation, demonstration and package verification scripts.
See `docs/VALIDATION.md`, `docs/DATA_SOURCES.md` and `docs/PUBLISHING.md`.

Code and synthetic examples: **GPL-3.0-or-later**, see `LICENSE`. Official data
retain their own terms and attribution. The installable plugin ZIP can be shared
for manual installation. An official QGIS repository submission still requires
the publisher's real author/contact and public repository/homepage/issue URLs;
these were not fabricated. No online upload was performed.

## Full LfU project: select one local well group

The additional `Draw_Catchments_LfU_Brandenburg.qgz` project contains 13 wells
at two distant sites. Open the abstraction-wells attribute table and select:

```text
"site" = 'Belzig II' AND "well_id" = 'II'
```

Use **Selected wells**, spring 2015 contours, head field `ZLEVEL`, and identifier
`well_id`. Default settings produce a valid polygon. For the four-well example,
select only `"site" = 'Belzig II'`. Do not use All wells on this combined layer.
All 13 wells span about 78 km and require more than 140,000 contour samples;
version 0.1.1 explains the local selection needed before interpolation begins.
It also recognizes selected wells on reopening and prefers `well_id` where present.
For a dense single-site contour dataset, the sample-limit message provides a
minimum recommended **Contour sample spacing**; inspect the effect of any
coarser sampling on your paths. The numerical algorithm is unchanged.

## Hover help and selection feedback (0.1.2)

Hover over an input field, its label, or an action button for an explanation of
what to choose, units, examples, defaults and effects. Labels also provide help
when their related field is disabled. The count below Use shows how many well
features will be included and updates when the QGIS selection changes.
Start-point and stopping-boundary fields activate only in their relevant modes.
Settings scroll on smaller screens while calculation and export buttons remain
accessible. On first opening, visible contours are preferred over hidden
comparison layers when a recognized head field is present.

This plugin accepts user-supplied groundwater contour lines and well point
layers. No LfU-specific loading button was added. The official LfU monitoring
stations are available in the separate Draw_Catchments_LfU_Brandenburg.qgz
project under LfU | spring 2015 | default and the optional autumn group.

## Individual point workflow

Install Draw_Catchments-0.1.4.zip and restart QGIS. Check that the dialog
heading displays version 0.1.4.

1. With the QGIS Pan tool active, right-click an existing point on the map.
2. Choose **Draw Catchments for this point...**. If several points overlap,
   use the **Draw Catchments for one point** submenu to choose the exact feature.
3. Select your groundwater contour layer and head field, then **Draw catchment**.

Alternatively, open the plugin and choose a point from the **Point** dropdown.
It lists individual points across all loaded project point layers. Type a name
or layer name to search. Selecting an item automatically activates **One point**
mode and sets its source layer. Entries include layer, identifier and feature ID;
multipart features have separate point-component entries. No map selection is
required. Other selected features are ignored, and existing QGIS selections are
preserved. A deleted or unmatched point causes an error, never a fallback to
the full layer. Selected wells and All wells remain explicit group options.

Right-click access requires a normal map context menu (for example with Pan).
During digitizing, right-click may finish the edit instead; switch to Pan first.
The loaded contours still need to cover the chosen point and its upstream area.
