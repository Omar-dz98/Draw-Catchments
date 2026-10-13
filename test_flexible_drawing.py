"""Real Qt map events, straight approximate arrivals and adaptive analysis edges."""
from test_ui import *
from dataclasses import replace
from draw_catchments.manual import ManualSession,perpendicular_step,straight_arrival_angle
from draw_catchments.draft import save_draft,load_draft
from draw_catchments.manual_geometry import clear_connector


class FlexibleGeometryTests(unittest.TestCase):
    def test_analytic_nearest_angle_when_normal_outside_segment(self):
        lines=[(geometry([[100,20],[100,100]]),3)]
        with self.assertRaisesRegex(CalculationError,'No straight 90-degree'):
            perpendicular_step([0,0],[100,30],lines,2,20)
        step=perpendicular_step([0,0],[100,30],lines,2,20,angle_mode='best')
        expected=np.degrees(np.arctan2(100,20.001))
        self.assertEqual(step['points'].shape,(2,2))
        self.assertAlmostEqual(straight_arrival_angle(step,lines),expected,places=7)
        self.assertLess(step['angle_degrees'],90)

    def test_best_mode_still_prefers_exact_and_never_skips_contours(self):
        lines=[(geometry([[100,-100],[100,100]]),3),(geometry([[200,-100],[200,100]]),4)]
        step=perpendicular_step([0,0],[100,70],lines,2,20,angle_mode='best')
        self.assertAlmostEqual(step['angle_degrees'],90)
        with self.assertRaises(CalculationError):perpendicular_step([0,0],[200,0],lines,2,20,angle_mode='best')

    def test_blocked_normal_uses_straight_clear_candidate(self):
        lines=[(geometry([[100,-100],[100,100]]),3)]
        obstacle=geometry([[50,-10],[50,10]])
        step=perpendicular_step([0,0],[100,30],lines,2,20,obstacles=[obstacle],angle_mode='best')
        self.assertLess(step['angle_degrees'],90)
        self.assertTrue(clear_connector(step['points'],lines,2,0,[obstacle]))
        self.assertEqual(len(step['points']),2)

    def test_user_starts_exactly_preserved_in_result_and_draft(self):
        c,w=fixture();s=ManualSession(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300,downstream_shape='rounded')))
        starts=ORIGIN+[[200,-60],[200,90]];s.set_starts(starts)
        for side in (0,1):s.side=side;s.append(s.next_step())
        result=s.result()
        self.assertTrue(result['polygon'].contains(next(w.getFeatures()).geometry()))
        self.assertTrue(np.array_equal(result['starts'],starts))
        self.assertTrue(np.allclose(s.base()[[0,-1]],starts[::-1]))
        before=[np.array(p) for p in s.points]
        with self.assertRaisesRegex(CalculationError,'Undo both'):s.set_starts(starts+[0,1])
        for a,b in zip(before,s.points):self.assertTrue(np.array_equal(a,b))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'chosen.draft.json';save_draft(path,s);restored=load_draft(path)
            self.assertTrue(np.array_equal(restored.starts,starts))
            self.assertTrue(restored.result()['polygon'].equals(result['polygon']))

    def test_analysis_edge_expands_and_explicit_boundary_stays_fixed(self):
        c,w=fixture()
        settings=Settings(radius=250,sample=20,step=10,max_gap=0,auto_extent=True,interpolation='harmonic')
        data=prepare(c,'head',w,False,settings)
        first=calculate(dict(data,source_lines=None))
        result=calculate(data)
        self.assertGreater(result['metadata']['upstream_closure']['head_m'],first['metadata']['upstream_closure']['head_m'])
        self.assertTrue(result['metadata']['analysis_expansions'])
        self.assertTrue(np.array_equal(result['starts'],first['starts']))
        boundary=geometry(ORIGIN+[[450,-500],[450,500]])
        data=prepare(c,'head',w,False,replace(settings,stop='boundary',radius=1200),stop_geometry=boundary)
        result=calculate(data)
        self.assertEqual(result['metadata']['analysis_expansions'],[])
        for path in result['paths']:self.assertAlmostEqual(path.asPolyline()[-1].x(),ORIGIN[0]+450,places=6)

    def test_requested_boundary_not_reached_never_returns_shortened_polygon(self):
        c,w=fixture();boundary=geometry(ORIGIN+[[450,-500],[450,500]])
        data=prepare(c,'head',w,False,Settings(radius=250,sample=20,step=10,max_gap=0,interpolation='harmonic',stop='boundary'),stop_geometry=boundary)
        with self.assertRaisesRegex(CalculationError,'Both paths must reach'):calculate(data)
        self.assertIsNone(calculate(data,False)['polygon'])


class FlexibleUiTests(unittest.TestCase):
    setUp=UiTests.setUp
    tearDown=UiTests.tearDown

    def map_event(self,xy,kind=QEvent.MouseButtonRelease):
        canvas=self.iface.canvas;pixel=canvas.getCoordinateTransform().transform(QgsPointXY(*xy))
        return QgsMapMouseEvent(canvas,kind,QPoint(round(pixel.x()),round(pixel.y())),Qt.LeftButton if kind==QEvent.MouseButtonRelease else Qt.NoButton)

    def setup_canvas(self):
        canvas=self.iface.canvas;canvas.resize(900,700);canvas.show();canvas.setLayers([self.w,self.c])
        canvas.setExtent(QgsRectangle(400000,5799600,401000,5800400));app.processEvents()

    def test_main_map_clicks_set_two_arc_endpoints(self):
        self.setup_canvas();self.d.place_starts_button.click();tool=self.plugin.extra_tool
        events=[self.map_event(ORIGIN+p) for p in ([200,-60],[200,90])]
        expected=[]
        for event in events:
            p=event.mapPoint();expected.append([p.x(),p.y()]);tool.canvasReleaseEvent(event)
        self.assertIsNone(self.plugin.extra_tool)
        self.assertTrue(self.d.use_starts.isChecked())
        self.assertEqual(self.d.starts.currentLayer().featureCount(),2)
        self.d.manual_button.click();session=self.plugin.extra_tool.session
        self.assertTrue(np.allclose(session.starts,expected,rtol=0,atol=1e-6))
        for side in (0,1):
            session.side=side
            while session.heads[side][-1]<3:session.append(session.next_step())
        self.plugin.extra_tool.panel.buttons['Finish polygon'].click()
        self.assertIn('edited',self.d.layers,self.d.log.toPlainText())

    def test_floating_panel_endpoint_placement_cancel_and_commit(self):
        self.setup_canvas();self.d.manual_button.click();tool=self.plugin.extra_tool
        old=tool.session.starts.copy();tool.panel.buttons['Place arc endpoints on map'].click()
        self.assertFalse(tool.panel.buttons['Add next contour'].isEnabled())
        self.assertFalse(tool.panel.buttons['Finish polygon'].isEnabled())
        self.assertFalse(tool.panel.depth.isEnabled())
        tool.canvasReleaseEvent(self.map_event(ORIGIN+[200,-70]))
        from qgis.PyQt.QtGui import QKeyEvent
        tool.keyPressEvent(QKeyEvent(QEvent.KeyPress,Qt.Key_Escape,Qt.NoModifier))
        self.assertTrue(np.array_equal(tool.session.starts,old))
        self.assertTrue(tool.panel.buttons['Finish polygon'].isEnabled())
        tool.panel.buttons['Place arc endpoints on map'].click();expected=[]
        for xy in (ORIGIN+[200,-70],ORIGIN+[200,70]):
            event=self.map_event(xy);expected.append([event.mapPoint().x(),event.mapPoint().y()]);tool.canvasReleaseEvent(event)
        self.assertTrue(np.allclose(tool.session.starts,expected))
        self.assertIsNone(tool.placing_starts)
        self.assertEqual(tool.bands[3].numberOfVertices(),65)

    def test_approximate_preview_never_shows_right_angle_marker(self):
        self.setup_canvas();self.d.manual_button.click();tool=self.plugin.extra_tool;s=tool.session
        start=s.points[0][-1]
        s.data['lines']=[(geometry(start+[[100,20],[100,100]]),3)]
        tool.update()
        self.assertIn('78.',tool.panel.status.text())
        self.assertEqual(tool.angle_band.numberOfVertices(),0)
        self.assertEqual(tool.bands[2].numberOfVertices(),2)
        tool.panel.angle_mode.setCurrentIndex(1)
        self.assertFalse(tool.panel.buttons['Add next contour'].isEnabled())
        tool.panel.angle_mode.setCurrentIndex(0);tool.panel.buttons['Add next contour'].click()
        self.assertEqual(len(s.steps[0]),1)
        self.assertLess(s.steps[0][0]['angle_degrees'],90)


if __name__=='__main__':unittest.main(verbosity=2)
