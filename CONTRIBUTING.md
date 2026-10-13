# Development and testing

Created and maintained by Omar Brahimi. Contributions use GPL-3.0-or-later.
Keep fixes small and include a reproducible case. State QGIS version, operating
system, input CRSs, chosen head field and the exact error. Do not present software
tests as proof of hydrogeological capture-zone accuracy.

Use Python configured with PyQGIS, NumPy and SciPy. On Windows, use the Python
environment shipped with your QGIS installation. Set `QT_QPA_PLATFORM=offscreen`
for headless tests; the scripts initialize real QGIS geometry and Qt widgets.

From `tests`, run this single command:

```text
python -m unittest test_engine.EngineTests test_upstream_modes.UpstreamModesTests test_ui.UiTests test_manual_workflow.ManualWorkflowTests test_strict_manual.StrictGeometryTests test_strict_manual.StrictUiTests test_straight_arc.StraightArcGeometryTests test_straight_arc.StraightArcUiTests test_flexible_drawing.FlexibleGeometryTests test_flexible_drawing.FlexibleUiTests test_new_drawing.NewDrawingTests -v
```

From the repository root, build and test the actual ZIP:

```text
python tools/build_plugin.py
python tests/test_package.py
```

The builder requires publisher metadata by default; `--local` permits an
incomplete email/hosting entry for local development only. Its checks cover
syntax, metadata and archive contents, not a QGIS runtime test. Build output
goes to `dist`; test output goes to `outputs`. Neither belongs in source control.
Use `python tools/set_contact.py your-address@example.org` to set your own
maintainer address before building. Never leave that example address in metadata.

Before publishing changes, also test the map interaction in QGIS, save/reopen a
draft, switch to another point, finish both manual sides, and reimport an exported
GeoPackage. Increase the plugin version for each future published update.
