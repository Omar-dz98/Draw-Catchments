"""Known gradients, target arrival and exact manual perpendicular constraints."""
import unittest, tempfile, json
import test_engine as base
import numpy as np
from qgis.core import *
from draw_catchments.core.flow import Settings,CalculationError,trace
from draw_catchments.service import prepare,calculate,build_surface,result_layers,export_result,geometry
from draw_catchments.manual import ManualSession,perpendicular_step


class UpstreamModesTests(unittest.TestCase):
    def real_high_data(self,target=None):
        path=base.ROOT/'test_data/tiefensee_high_regression.gpkg'
        c=QgsVectorLayer(str(path)+'|layername=contours','LfU spring 2015','ogr')
        w=QgsVectorLayer(str(path)+'|layername=station','Tiefensee','ogr')
        target=target or QgsGeometry.fromRect(QgsRectangle(428300,5841800,430500,5844000))
        return prepare(c,'ZLEVEL',w,False,Settings(stop='target',max_gap=0,auto_extent=True,interpolation='harmonic'),upstream_target=target)

    def test_real_tiefensee_target_reaches_86m(self):
        data=self.real_high_data();result=calculate(data)
        self.assertEqual(result['metadata']['upstream_closure']['head_m'],86)
        self.assertTrue(result['polygon'].isGeosValid())
        for path,values in zip(result['paths'],result['path_data']):
            self.assertGreater(path.length(),12000)
            self.assertTrue(np.all(np.diff(values['heads'])>0))
            reached={head for line,head in data['lines'] if path.intersects(line)}
            self.assertTrue(set(range(75,86)).issubset(reached))
            self.assertTrue(data['upstream_target'].contains(QgsGeometry.fromPointXY(path.asPolyline()[-1])))

    def test_real_target_from_pixel_coordinates(self):
        target=QgsGeometry.fromRect(QgsRectangle(428304.18943533697,5841795.992714025,430496.35701275046,5844001.366120218))
        result=calculate(self.real_high_data(target))
        self.assertEqual(result['metadata']['upstream_closure']['head_m'],86)

    def test_contour_cap_submicrometre_endpoint_rounding(self):
        from draw_catchments.closure import contour_caps
        paths=[dict(points=np.array([[0.,y],[10.+delta,y]]),heads=np.array([0.,1.]),length_m=10.) for y,delta in [(0.,-1e-9),(5.,1e-9)]]
        caps=list(contour_caps(paths,[(geometry([[10,-10],[10,10]]),1)],1,.1))
        self.assertTrue(caps)
        self.assertLess(abs(caps[0][0][0]['points'][-1][0]-10),1e-6)

    def test_real_manual_whole_straight_normals_or_explicit_rejection(self):
        from dataclasses import replace
        data=self.real_high_data();data['settings']=replace(data['settings'],manual_angle_mode='strict');session=ManualSession(data)
        accepted=0;rejected=0
        for side in (0,1):
            session.side=side
            for level in range(75,87):
                point=QgsGeometry.fromPointXY(QgsPointXY(*session.points[side][-1]))
                line=min((line for line,head in data['lines'] if head==level),key=lambda line:line.distance(point))
                click=line.nearestPoint(point).asPoint()
                before=np.array(session.points[side])
                try:step=session.candidate([click.x(),click.y()],10)
                except CalculationError as exc:
                    self.assertIn('No straight 90-degree',str(exc))
                    self.assertTrue(np.array_equal(before,session.points[side]))
                    rejected+=1;break
                session.append(step);accepted+=1
                from draw_catchments.manual import straight_normal_angle
                self.assertEqual(len(step['points']),2)
                straight_normal_angle(step,data['lines'])
                self.assertLess(abs(step['angle_degrees']-90),1e-7)
                # Independently verify the arrival vector against original geometry.
                source=data['lines'][step['contour_index']][0]
                distance,foot,after,left=source.closestSegmentWithContext(QgsPointXY(*step['point']))
                coords=list(source.vertices());tangent=np.array([coords[after].x()-coords[after-1].x(),coords[after].y()-coords[after-1].y()])
                arrival=step['points'][-1]-step['points'][-2]
                self.assertLess(abs(arrival@tangent)/(np.linalg.norm(arrival)*np.linalg.norm(tangent)),1e-7)
        self.assertGreater(accepted,0)
        if not rejected:
            result=session.result()
            self.assertTrue(result['polygon'].isGeosValid())
            self.assertEqual(result['metadata']['upstream_closure']['head_m'],86)

    def test_widely_spaced_contours_expand_without_gap_cutoff(self):
        for scale in (10,100):
            c,w=base.fixture()
            for layer in (c,w):
                changes={}
                for feature in layer.getFeatures():
                    geom=feature.geometry()
                    if layer==c:
                        coords=np.array([[p.x(),p.y()] for p in geom.vertices()])
                        geom=geometry(base.ORIGIN+(coords-base.ORIGIN)*scale)
                    else:
                        pt=geom.asPoint();xy=base.ORIGIN+(np.array([pt.x(),pt.y()])-base.ORIGIN)*scale
                        geom=QgsGeometry.fromPointXY(QgsPointXY(*xy))
                    changes[feature.id()]=geom
                layer.dataProvider().changeGeometryValues(changes);layer.updateExtents()
            data=prepare(c,'head',w,False,Settings(radius=50,sample=20*scale,step=10*scale,max_gap=0,auto_extent=True,interpolation='harmonic'))
            self.assertGreater(data['settings'].radius,50)
            result=calculate(data)
            self.assertTrue(result['polygon'].isGeosValid())
            self.assertTrue(result['polygon'].contains(next(w.getFeatures()).geometry()))
            self.assertEqual(result['metadata']['settings']['max_gap'],0)

    def test_outside_actual_coverage_does_not_extrapolate(self):
        c,w=base.fixture(points=[[-500,0]])
        with self.assertRaisesRegex(CalculationError,'do not surround'):
            prepare(c,'head',w,False,Settings(radius=100,max_gap=0,auto_extent=True,interpolation='harmonic'))

    def test_harmonic_plane_gradient_and_group(self):
        for points in ([[220,0]],[[220,-50],[250,80]]):
            c,w=base.fixture(points=points)
            data=prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300,max_length=700,interpolation='harmonic'))
            surface=build_surface(data)
            for xy in ([250,0],[480,-90],[725,110]):
                h,g=surface.at(base.ORIGIN+xy)
                self.assertLess(abs(h-xy[0]/100),.01)
                self.assertLess(abs(g[1]),1e-5)
                self.assertLess(abs(g[0]-.01),1e-5)
            result=calculate(data)
            self.assertTrue(result['polygon'].isGeosValid())
            self.assertTrue(all(result['polygon'].contains(f.geometry()) for f in w.getFeatures()))
            self.assertTrue(all(np.all(np.diff(p['heads'])>0) for p in result['path_data']))

    def test_harmonic_curved_and_irregular(self):
        for kind in ('curved','irregular'):
            c,w=base.fixture(kind)
            result=calculate(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300,max_length=700,interpolation='harmonic')))
            self.assertTrue(result['polygon'].isGeosValid())
            self.assertTrue(all(np.all(np.diff(p['heads'])>0) for p in result['path_data']))
            for path in result['paths']:
                self.assertTrue(path.isSimple())

    def test_target_expands_analysis_and_reaches_area(self):
        c,w=base.fixture()
        target=QgsGeometry.fromRect(QgsRectangle(400800,5799900,400990,5800100))
        data=prepare(c,'head',w,False,Settings(radius=100,sample=20,step=10,max_gap=300,max_length=200,stop='target'),upstream_target=target)
        self.assertGreater(data['settings'].max_length,700)
        self.assertEqual(data['settings'].interpolation,'harmonic')
        result=calculate(data)
        self.assertTrue(all(target.intersects(QgsGeometry.fromPointXY(QgsPointXY(*p['points'][-1]))) for p in result['path_data']))
        self.assertGreaterEqual(result['metadata']['upstream_closure']['head_m'],8)
        layers=result_layers(result);self.assertIn('target',layers)
        with tempfile.TemporaryDirectory() as folder:
            path=base.Path(folder)/'result.gpkg';export_result(path,layers['edited'],layers)
            again=QgsVectorLayer(str(path)+'|layername=target','target','ogr')
            self.assertTrue(again.isValid())
            self.assertEqual(again.featureCount(),1)
            del again

    def test_unreachable_target_returns_no_short_polygon(self):
        c,w=base.fixture()
        target=QgsGeometry.fromRect(QgsRectangle(400750,5800400,400900,5800550))
        data=prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300,stop='target'),upstream_target=target)
        paths=calculate(data,polygon=False)
        self.assertIsNone(paths['polygon'])
        self.assertTrue(any('not reached' in note for note in paths['metadata']['warnings']))
        with self.assertRaisesRegex(CalculationError,'target area'):
            calculate(data)

    def test_target_required(self):
        c,w=base.fixture()
        with self.assertRaisesRegex(CalculationError,'target area'):
            prepare(c,'head',w,False,Settings(stop='target'))

    def test_perpendicular_projected_endpoint_is_exact(self):
        line=geometry([[0,0],[100,100]])
        # Use a non-endpoint foot for an oblique contour.
        step=perpendicular_step([50,0],[40,40],[(line,10)],9,10)
        self.assertTrue(np.allclose(step['point'],[25,25]))
        self.assertAlmostEqual(step['angle_degrees'],90,places=10)

    def test_vertex_clamping_is_rejected(self):
        with self.assertRaises(CalculationError):
            perpendicular_step([200,0],[100,100],[(geometry([[0,0],[100,100]]),10)],9,10)

    def test_missing_normal_is_rejected_without_bent_fallback(self):
        with self.assertRaisesRegex(CalculationError,'No straight 90-degree'):
            perpendicular_step([200,0],[100,100],[(geometry([[0,0],[100,100]]),10)],9,10)

    def test_skipped_higher_contour_rejected(self):
        lines=[(geometry([[100,-100],[100,100]]),1),(geometry([[200,-100],[200,100]]),2)]
        with self.assertRaises(CalculationError):perpendicular_step([0,0],[200,0],lines,0,10)

    def test_manual_two_sides_polygon_export(self):
        c,w=base.fixture()
        session=ManualSession(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300,interpolation='harmonic')))
        for side in (0,1):
            session.side=side
            for x in (300,400,500,600,700,800):
                step=session.candidate([400000+x,session.points[side][-1][1]],20)
                session.append(step)
                self.assertAlmostEqual(step['angle_degrees'],90,places=9)
        result=session.result()
        self.assertTrue(result['polygon'].isGeosValid())
        self.assertTrue(result['polygon'].contains(next(w.getFeatures()).geometry()))
        self.assertEqual(result['metadata']['upstream_closure']['head_m'],8)
        self.assertEqual(len(result['metadata']['manual_steps'][0]),6)
        session.undo();self.assertEqual(len(session.points[1]),6)
        with self.assertRaisesRegex(CalculationError,'same head'):session.result()

if __name__=='__main__':unittest.main(verbosity=2)
