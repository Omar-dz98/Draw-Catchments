"""Measure the whole connector, independently of saved '90 degrees' metadata."""
from test_ui import *
from draw_catchments.manual import ManualSession,perpendicular_step,straight_normal_angle
from draw_catchments.draft import save_draft,load_draft


class StrictGeometryTests(unittest.TestCase):
    def test_bent_connection_is_not_a_straight_normal(self):
        lines=[(geometry([[100,-100],[100,100]]),3)]
        step=dict(points=np.array([[0,0],[80,20],[100,20]]),point=np.array([100,20]),head=3,contour_index=0,angle_degrees=90)
        with self.assertRaisesRegex(CalculationError,'contains a bend'):straight_normal_angle(step,lines)

    def test_angle_is_measured_not_read_from_metadata(self):
        step=dict(points=np.array([[0,0],[100,20]]),point=np.array([100,20]),head=3,contour_index=0,angle_degrees=90)
        with self.assertRaisesRegex(CalculationError,'not 90 degrees'):
            straight_normal_angle(step,[(geometry([[100,-100],[100,100]]),3)])

    def test_clicked_contour_snaps_to_valid_foot_on_another_segment(self):
        source=geometry([[100,-100],[100,100],[200,150]])
        step=perpendicular_step([20,0],[190,145],[(source,3)],2,15)
        self.assertEqual(len(step['points']),2)
        self.assertTrue(np.allclose(step['point'],[100,0]))
        self.assertAlmostEqual(straight_normal_angle(step,[(source,3)]),90,places=9)

    def test_synthetic_oblique_and_curved_lines_measure_whole_step(self):
        for source,start,click in (([[0,0],[100,100]],[50,0],[40,40]),
                                   ([[100,-100],[100,0],[120,40],[180,90]],[0,-40],[115,30])):
            line=geometry(source);step=perpendicular_step(start,click,[(line,3)],2,20)
            self.assertEqual(len(step['points']),2)
            _,_,after,_=line.closestSegmentWithContext(QgsPointXY(*step['points'][-1]))
            vertices=list(line.vertices());tangent=np.array([vertices[after].x()-vertices[after-1].x(),vertices[after].y()-vertices[after-1].y()])
            whole=step['points'][-1]-step['points'][0]
            self.assertLess(abs(whole@tangent)/(np.linalg.norm(whole)*np.linalg.norm(tangent)),1e-10)

    def test_bent_legacy_draft_is_retained_but_cannot_claim_strict_polygon(self):
        c,w=fixture();session=ManualSession(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300)))
        step=session.candidate([400300,session.points[0][-1][1]],20);session.append(step)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'legacy.draft.json';save_draft(path,session)
            old=json.loads(path.read_text(encoding='utf-8'));stored=old['steps'][0][0]
            start,end=stored['points'];stored['points']=[start,[end[0]-5,end[1]+2],end]
            stored['method']='smooth_normal'
            path.write_text(json.dumps(old),encoding='utf-8')
            restored=load_draft(path)
            self.assertEqual(len(restored.steps[0][0]['points']),3)
            with self.assertRaisesRegex(CalculationError,'Side 1, step 1'):restored.next_step()
            with self.assertRaisesRegex(CalculationError,'Side 1, step 1'):restored.result()
            restored.undo();self.assertEqual(len(restored.points[0]),1)
            self.assertIsNone(restored.constraint_issue());self.assertTrue(path.exists())


class StrictUiTests(unittest.TestCase):
    setUp=UiTests.setUp
    tearDown=UiTests.tearDown

    def test_preview_reports_whole_angle_and_right_angle_mark(self):
        canvas=self.iface.canvas;canvas.resize(900,700);canvas.show()
        canvas.setLayers([self.w,self.c]);canvas.setExtent(QgsRectangle(400000,5799600,401000,5800400));app.processEvents()
        self.d.manual_button.click();tool=self.plugin.extra_tool
        pixel=canvas.getCoordinateTransform().transform(QgsPointXY(400300,5800100))
        event=QgsMapMouseEvent(canvas,QEvent.MouseMove,QPoint(round(pixel.x()),round(pixel.y())),Qt.NoButton)
        tool.canvasMoveEvent(event)
        self.assertIn('90.000000',tool.panel.status.text())
        self.assertEqual(tool.bands[2].numberOfVertices(),2)
        self.assertEqual(tool.angle_band.numberOfVertices(),3)
        click=QgsMapMouseEvent(canvas,QEvent.MouseButtonRelease,event.pos(),Qt.LeftButton)
        tool.canvasReleaseEvent(click)
        self.assertEqual(len(tool.session.steps[0]),1)
        self.assertEqual(len(tool.session.steps[0][0]['points']),2)

    def test_farther_click_includes_every_intermediate_contour(self):
        canvas=self.iface.canvas;canvas.resize(900,700);canvas.show()
        canvas.setLayers([self.w,self.c]);canvas.setExtent(QgsRectangle(400000,5799600,401000,5800400));app.processEvents()
        self.d.manual_button.click();tool=self.plugin.extra_tool
        pixel=canvas.getCoordinateTransform().transform(QgsPointXY(400800,5800100))
        event=QgsMapMouseEvent(canvas,QEvent.MouseButtonRelease,QPoint(round(pixel.x()),round(pixel.y())),Qt.LeftButton)
        tool.canvasReleaseEvent(event)
        self.assertEqual([s['head'] for s in tool.session.steps[0]],[3,4,5,6,7,8])
        self.assertIn('Added 6 contour',tool.panel.status.text())
        for step in tool.session.steps[0]:self.assertAlmostEqual(straight_normal_angle(step,tool.session.data['lines']),90,places=8)


if __name__=='__main__':unittest.main(verbosity=2)
