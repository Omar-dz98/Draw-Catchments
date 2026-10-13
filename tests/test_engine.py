"""Run with the Python environment shipped with QGIS; no mock GIS geometry."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin"))
from qgis.core import *
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.gui import QgsMapCanvas
from draw_catchments.core.flow import Settings, HeadSurface, CalculationError, trace
from draw_catchments.service import prepare, calculate, result_layers, export_result, geometry, build_surface, local_calculation_crs

app = QgsApplication.instance() or QgsApplication([], False)
app.initQgis()
CRS = QgsCoordinateReferenceSystem("EPSG:25833")
ORIGIN = np.array([400000., 5800000.])


def fixture(kind="plane", points=None):
    contours = QgsVectorLayer("LineString?crs=EPSG:25833", "Synthetic " + kind, "memory")
    contours.dataProvider().addAttributes([QgsField("head", QVariant.Double)])
    contours.updateFields()
    for h in np.arange(0., 11.):
        y = np.linspace(-600, 600, 61)
        if kind == "plane":
            x = np.full(len(y), h * 100)
        elif kind == "curved":
            x = h * 100 - 0.0005 * y ** 2
        else:
            x = h * 100 - 70 * np.sin(y / 220)
        f = QgsFeature(contours.fields())
        f.setAttributes([float(h)])
        f.setGeometry(geometry(np.column_stack([x, y]) + ORIGIN))
        contours.dataProvider().addFeatures([f])
    contours.updateExtents()
    wells = QgsVectorLayer("Point?crs=EPSG:25833", "Synthetic wells", "memory")
    wells.dataProvider().addAttributes([QgsField("well_id", QVariant.String)])
    wells.updateFields()
    for i, p in enumerate(points or [[220, 0]]):
        f = QgsFeature(wells.fields())
        f.setAttributes([str(i)])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*(np.array(p) + ORIGIN))))
        wells.dataProvider().addFeatures([f])
    wells.updateExtents()
    return contours, wells


def run_case(kind="plane", points=None, settings=None):
    c, w = fixture(kind, points)
    settings = settings or Settings(sample=20, step=10, radius=2000, max_gap=300, max_length=700)
    data = prepare(c, "head", w, False, settings, CRS, "well_id")
    return calculate(data), data


class EngineTests(unittest.TestCase):
    def test_individual_locations_across_three_known_surfaces(self):
        for kind in ('plane','curved','irregular'):
            for xy in ([150,-200],[350,0],[600,200]):
                with self.subTest(kind=kind,xy=xy):
                    r,data=run_case(kind,points=[xy])
                    self.assertTrue(r['polygon'].isGeosValid())
                    self.assertTrue(r['polygon'].contains(QgsGeometry.fromPointXY(QgsPointXY(*data['wells'][0]))))
                    self.assertEqual(len(r['metadata']['wells']),1)
                    self.assertTrue(all(np.all(np.diff(path['heads'])>0) for path in r['path_data']))

    def test_geographic_inputs_choose_local_crs_on_two_continents(self):
        for epsg,origin in [('EPSG:32618',np.array([580000.,4500000.])),('EPSG:32755',np.array([320000.,5810000.]))]:
            with self.subTest(epsg=epsg):
                c,w=fixture()
                local=QgsCoordinateReferenceSystem(epsg)
                geographic=QgsCoordinateReferenceSystem('EPSG:4326')
                transform=QgsCoordinateTransform(local,geographic,QgsProject.instance())
                for layer in (c,w):
                    changes={}
                    for feature in layer.getFeatures():
                        g=feature.geometry();g.translate(*(origin-ORIGIN));g.transform(transform)
                        changes[feature.id()]=g
                    layer.dataProvider().changeGeometryValues(changes);layer.setCrs(geographic);layer.updateExtents()
                # Web-Mercator map clicks must also choose a local metric CRS,
                # not mistake web-map units for undistorted ground distances.
                if epsg=='EPSG:32755':
                    web=QgsCoordinateReferenceSystem('EPSG:3857')
                    tr=QgsCoordinateTransform(geographic,web,QgsProject.instance())
                    changes={}
                    for feature in w.getFeatures():
                        g=feature.geometry();g.transform(tr);changes[feature.id()]=g
                    w.dataProvider().changeGeometryValues(changes);w.setCrs(web);w.updateExtents()
                chosen=local_calculation_crs(c,w)
                self.assertEqual(chosen.authid(),epsg)
                r=calculate(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300,max_length=700),chosen))
                self.assertTrue(r['polygon'].isGeosValid())
                # Highest crossed contour is x=900: 690 m path by 50 m width.
                self.assertLess(abs(r['polygon'].area()-34500),2)

    def test_automatic_width_can_fit_near_edge_without_extrapolation(self):
        r,data=run_case(points=[[220,580]])
        self.assertTrue(r['polygon'].contains(QgsGeometry.fromPointXY(QgsPointXY(*data['wells'][0]))))
        self.assertLess(r['metadata']['initialization']['automatic_width_factor'],1)
        self.assertTrue(any('width reduced' in note for note in r['metadata']['warnings']))
        self.assertLessEqual(r['polygon'].boundingBox().yMaximum(),5800600)
        # Explicit manual settings are never silently narrowed.
        with self.assertRaises(CalculationError):
            run_case(points=[[220,580]],settings=Settings(sample=20,step=10,max_gap=300,width=50))

    def test_plane_direction_and_monotonicity(self):
        r, _ = run_case()
        self.assertTrue(r["polygon"].isGeosValid())
        for p in r["path_data"]:
            self.assertLess(np.ptp(p["points"][:, 1]), 0.01)
            self.assertTrue(np.all(np.diff(p["heads"]) > 0))
            self.assertAlmostEqual(p["length_m"], 690, places=3)

    def test_strausberg_continues_across_higher_contours(self):
        path=ROOT/'test_data/strausberg_regression.gpkg'
        c=QgsVectorLayer(str(path)+'|layername=contours','LfU contours','ogr')
        w=QgsVectorLayer(str(path)+'|layername=station','Strausberg West','ogr')
        data=prepare(c,'ZLEVEL',w,False,Settings(),id_field='MKZ')
        result=calculate(data)
        self.assertTrue(result['polygon'].isGeosValid())
        self.assertTrue(result['polygon'].contains(next(w.getFeatures()).geometry()))
        self.assertGreater(result['polygon'].area(),2e6)
        self.assertEqual(result['metadata']['upstream_closure']['head_m'],64)
        self.assertTrue(all(path['length_m']>2000 for path in result['path_data']))
        self.assertGreater(result['metadata']['untrimmed_paths'][1]['end_head_m'],67)
        contour=QgsGeometry.unaryUnion([g for g,h in data['lines'] if h==64])
        cap=result['upstream_boundary']
        self.assertGreater(len(list(cap.vertices())),3)
        self.assertTrue(cap.difference(contour.buffer(.001,4)).isEmpty())
        for path in result['paths']:
            reached={head for line,head in data['lines'] if path.intersects(line)}
            self.assertTrue(set(range(59,64)).issubset(reached),reached)
            end=QgsGeometry.fromPointXY(path.asPolyline()[-1])
            self.assertLess(contour.distance(end),1e-5)
            for iso,head in data['lines']:
                crossings=path.intersection(iso)
                for point in crossings.vertices():
                    g=QgsGeometry.fromPointXY(QgsPointXY(point))
                    def tangent(line,distance):
                        a=line.interpolate(max(0,distance-2)).asPoint()
                        b=line.interpolate(min(line.length(),distance+2)).asPoint()
                        v=np.array([b.x()-a.x(),b.y()-a.y()]);return v/np.linalg.norm(v)
                    cosine=abs(np.dot(tangent(path,path.lineLocatePoint(g)),tangent(iso,iso.lineLocatePoint(g))))
                    self.assertLess(cosine,np.cos(np.radians(80)),f'{head}: crossing deviates over 10 degrees from perpendicular')

    def test_curved_cap_preserves_contour_shape(self):
        result,data=run_case('curved',points=[[220,100]])
        level=result['metadata']['upstream_closure']['head_m']
        source=QgsGeometry.unaryUnion([line for line,head in data['lines'] if head==level])
        cap=result['upstream_boundary']
        self.assertGreater(len(list(cap.vertices())),2)
        self.assertTrue(cap.difference(source.buffer(1e-4,4)).isEmpty())
        for path in result['path_data']:
            end=QgsGeometry.fromPointXY(QgsPointXY(*path['points'][-1]))
            self.assertLess(source.distance(end),1e-5)

    def test_group_containment(self):
        r, data = run_case(points=[[220, -70], [250, 85], [300, 30]])
        for x, y in data["wells"]:
            self.assertTrue(r["polygon"].contains(QgsGeometry.fromPointXY(QgsPointXY(x, y))))

    def test_curved_analytical_solution(self):
        r, _ = run_case("curved", points=[[220, 100]])
        # h = .01*(x + .0005*y^2), so dy/dx=.001*y.
        for p in r["path_data"]:
            local = p["points"] - ORIGIN
            expected_y = local[0, 1] * np.exp(0.001 * (local[:, 0] - local[0, 0]))
            self.assertLess(np.max(np.abs(local[:, 1] - expected_y)), 4.0)

    def test_irregular(self):
        r, _ = run_case("irregular", points=[[220, 50]])
        self.assertTrue(r["polygon"].isGeosValid())
        self.assertGreater(len(r["path_data"][0]["points"]), 20)

    def test_edge_rejected(self):
        with self.assertRaises(CalculationError):
            run_case(points=[[220, 599]])

    def test_empty_selection(self):
        c, w = fixture()
        with self.assertRaisesRegex(CalculationError, "No wells selected"):
            prepare(c, "head", w, True, Settings(), CRS)

    def test_explicit_point_ignores_layer_selection(self):
        c, w = fixture(points=[[220,0],[60000,0]])
        features = list(w.getFeatures())
        w.selectAll()
        data = prepare(c, 'head', w, False, Settings(sample=20, step=10, max_gap=300, max_length=700),
                       CRS, 'well_id', point_target=(features[0].id(),0))
        self.assertEqual(len(data['wells']),1)
        self.assertEqual(w.selectedFeatureCount(),2)
        r=calculate(data)
        self.assertTrue(r['polygon'].isGeosValid())
        self.assertEqual(len(r['metadata']['wells']),1)
        self.assertEqual(r['metadata']['wells'][0]['fid'],features[0].id())

    def test_missing_explicit_point_never_falls_back_to_all(self):
        c,w=fixture()
        with self.assertRaisesRegex(CalculationError,'no longer exists'):
            prepare(c,'head',w,False,Settings(),CRS,point_target=(999999,0))

    def test_multipart_target_uses_only_one_component(self):
        c,_=fixture()
        w=QgsVectorLayer('MultiPoint?crs=EPSG:25833','Multipart points','memory')
        f=QgsFeature()
        f.setGeometry(QgsGeometry.fromMultiPointXY([QgsPointXY(400220,5800000),QgsPointXY(450000,5800000)]))
        w.dataProvider().addFeatures([f])
        f=next(w.getFeatures())
        data=prepare(c,'head',w,False,Settings(),CRS,point_target=(f.id(),0))
        self.assertEqual(data['wells'].tolist(),[[400220.0,5800000.0]])

    def test_sample_limit_reports_usable_spacing(self):
        c, w = fixture()
        data = prepare(c, "head", w, False, Settings(sample=0.1), CRS)
        with self.assertRaisesRegex(CalculationError, "132,011.*Contour sample spacing.*0.3 m"):
            build_surface(data)
        data["settings"].sample = 0.3
        surface = build_surface(data)
        self.assertLessEqual(len(surface.xy), 60000)

    def test_sample_limit_explains_distant_well_groups(self):
        c, w = fixture(points=[[220,0],[60000,0]])
        data = prepare(c, "head", w, False, Settings(sample=0.1), CRS)
        with self.assertRaisesRegex(CalculationError, "2 wells.*59.8 km.*Selected wells"):
            build_surface(data)

    def test_crossing_contours_rejected(self):
        c, w = fixture()
        f = QgsFeature(c.fields())
        f.setAttributes([5.5])
        f.setGeometry(geometry(ORIGIN + np.array([[0, 0], [1000, 0]])))
        c.dataProvider().addFeatures([f])
        with self.assertRaisesRegex(CalculationError, "intersect"):
            calculate(prepare(c, "head", w, False, Settings(), CRS))

    def test_custom_stop_exact(self):
        c, w = fixture()
        data = prepare(c, "head", w, False, Settings(sample=20, step=17, max_gap=300, stop="boundary"), CRS,
                       stop_geometry=geometry(ORIGIN + np.array([[700, -600], [700, 600]])))
        r = calculate(data)
        for p in r["path_data"]:
            self.assertAlmostEqual(p["points"][-1, 0] - ORIGIN[0], 700, places=5)
            self.assertEqual(p["stop_reason"], "Custom boundary reached")

    def test_highest_stop(self):
        r, _ = run_case(settings=Settings(sample=20, step=10, radius=2000, max_gap=300))
        self.assertTrue(all("Highest" in p["stop_reason"] for p in r["path_data"]))

    def test_mixed_crs(self):
        c, w = fixture()
        ll = QgsCoordinateReferenceSystem("EPSG:4326")
        trans = QgsCoordinateTransform(CRS, ll, QgsProject.instance())
        changes = {}
        for f in w.getFeatures():
            g = f.geometry()
            g.transform(trans)
            changes[f.id()] = g
        w.dataProvider().changeGeometryValues(changes)
        w.setCrs(ll)
        r = calculate(prepare(c, "head", w, False, Settings(sample=20, step=10, max_gap=300, max_length=700), CRS))
        baseline, _ = run_case()
        self.assertLess(abs(r["polygon"].area() - baseline["polygon"].area()), 0.1)

    def test_invalid_crs_rejected(self):
        c, w = fixture()
        with self.assertRaises(CalculationError):
            prepare(c, "head", w, False, Settings(), QgsCoordinateReferenceSystem("EPSG:4326"))

    def test_gap_rejected(self):
        with self.assertRaises(CalculationError):
            run_case(settings=Settings(max_gap=20, sample=20, step=10))

    def test_adjusted_starts(self):
        c, w = fixture()
        data = prepare(c, "head", w, False, Settings(max_gap=300, sample=20, step=10, max_length=700), CRS,
                       starts=ORIGIN + np.array([[180, -100], [180, 100]]))
        r = calculate(data)
        self.assertEqual(r["metadata"]["initialization"]["initialization"], "user start points")

    def test_exports_roundtrip(self):
        r, _ = run_case()
        layers = result_layers(r)
        for ext in ("gpkg", "shp", "geojson"):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ("proposal." + ext)
                export_result(path, layers["edited"], layers)
                reopened = QgsVectorLayer(str(path) + ("|layername=catchment" if ext == "gpkg" else ""), "roundtrip", "ogr")
                self.assertTrue(reopened.isValid())
                self.assertEqual(reopened.featureCount(), 1)
                iterator = reopened.getFeatures()
                feature = next(iterator)
                iterator.close()
                g = feature.geometry()
                if reopened.crs() != CRS:
                    g.transform(QgsCoordinateTransform(reopened.crs(), CRS, QgsProject.instance()))
                self.assertTrue(g.isGeosValid())
                self.assertLess(abs(g.area() - r["polygon"].area()), 5.0)
                self.assertTrue(path.with_suffix(path.suffix + ".drawcatchments.json").exists())
                with self.assertRaises(CalculationError):
                    export_result(path, layers["edited"])
                reopened.setDataSource("", "closed", "ogr")
                del reopened

    def test_invalid_edited_export_rejected(self):
        r, _ = run_case()
        layers = result_layers(r)
        f = next(layers["edited"].getFeatures())
        layers["edited"].dataProvider().changeGeometryValues({f.id(): QgsGeometry.fromWkt("POLYGON((0 0,1 1,0 1,1 0,0 0))")})
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(CalculationError):
            export_result(Path(tmp) / "invalid.gpkg", layers["edited"])

    def test_edited_polygon_must_retain_well(self):
        r, _ = run_case()
        layers = result_layers(r)
        f = next(layers["edited"].getFeatures())
        g = f.geometry()
        g.translate(10000, 0)
        layers["edited"].dataProvider().changeGeometryValues({f.id(): g})
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(CalculationError, "excludes"):
            export_result(Path(tmp) / "excludes.gpkg", layers["edited"])

    def test_cancel_before_interpolation(self):
        c, w = fixture()
        with self.assertRaisesRegex(CalculationError, "canceled"):
            calculate(prepare(c, "head", w, False, Settings(), CRS), canceled=lambda: True)

    def test_group_too_narrow_rejected(self):
        with self.assertRaisesRegex(CalculationError, "span"):
            run_case(points=[[220,-100],[220,100]], settings=Settings(width=100))

    def test_flat_contours_rejected(self):
        c, w = fixture()
        c.dataProvider().changeAttributeValues({f.id(): {0: 10.0} for f in c.getFeatures()})
        with self.assertRaisesRegex(CalculationError, "three distinct"):
            calculate(prepare(c, "head", w, False, Settings(), CRS))

    def test_nan_head_rejected(self):
        c, w = fixture()
        f = next(c.getFeatures())
        c.dataProvider().changeAttributeValues({f.id(): {0: float("nan")}})
        with self.assertRaisesRegex(CalculationError, "finite"):
            calculate(prepare(c, "head", w, False, Settings(), CRS))

    def test_duplicate_conflicting_sample(self):
        with self.assertRaisesRegex(CalculationError, "Conflicting"):
            HeadSurface([[0,0],[0,0],[1,1]], [1,2,3], 300, 20)

    def test_eichwalde_unresolved_low_rejected(self):
        c = QgsVectorLayer(str(ROOT / "test_data/eichwalde_contours_2015.gpkg"), "Real contours", "ogr")
        w = QgsVectorLayer(str(ROOT / "test_data/eichwalde_wells.gpkg"), "Real wells", "ogr")
        with self.assertRaisesRegex(CalculationError, "coverage"):
            calculate(prepare(c, "ZLEVEL", w, False, Settings(), CRS, "well_id"))

    def test_real_single_and_group(self):
        c = QgsVectorLayer(str(ROOT / "test_data/belzig_ii_contours_2015.gpkg"), "Real contours", "ogr")
        w = QgsVectorLayer(str(ROOT / "test_data/belzig_ii_wells.gpkg"), "Real wells", "ogr")
        for selected in (True, False):
            with self.subTest(selected=selected):
                w.selectByExpression("\"well_id\" = 'II'")
                data = prepare(c, "ZLEVEL", w, selected, Settings(), CRS, "well_id")
                r = calculate(data)
                self.assertTrue(r["polygon"].isGeosValid())
                print("REAL", selected, r["polygon"].area(), r["metadata"]["initialization"], r["metadata"]["paths"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
