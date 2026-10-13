"""Straight lateral boundaries, atomic multi-contour clicks and rounded bases."""
from test_ui import *
from dataclasses import replace
from draw_catchments.manual import ManualSession,straight_normal_angle
from draw_catchments.downstream import downstream_arc
from draw_catchments.draft import save_draft,load_draft


class StraightArcGeometryTests(unittest.TestCase):
    def test_direction_changes_at_contour_without_rounding(self):
        c,w=fixture();s=ManualSession(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300)))
        s.starts=ORIGIN+[[0,0],[0,80]];s.points=[[p.copy()] for p in s.starts];s.heads=[[1],[1]]
        s.data['lines']=[(geometry(ORIGIN+np.array([[100,-200],[100,200]])),2),
                         (geometry(ORIGIN+np.array([[180,-100],[240,100]])),3)]
        for index,click in ((0,ORIGIN+[100,0]),(1,ORIGIN+[210,0])):
            step=s.step_to(index,click);s.append(step)
            self.assertEqual(len(step['points']),2)
            self.assertAlmostEqual(straight_normal_angle(step,s.data['lines']),90,places=7)
        self.assertEqual(len(s.points[0]),3)
        a,b=np.diff(s.points[0],axis=0)
        self.assertGreater(abs(a[0]*b[1]-a[1]*b[0]),10)

    def test_old_smooth_step_cannot_be_appended_even_with_normal_tip(self):
        c,w=fixture();s=ManualSession(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300)))
        step=s.candidate([400300,s.starts[0][1]],20);start,end=step['points']
        step['points']=np.array([start,start+[20,10],end-[5,0],end]);step['method']='smooth_normal'
        with self.assertRaisesRegex(CalculationError,'contains a bend'):s.append(step)
        self.assertEqual(len(s.points[0]),1)

    def test_proposal_is_atomic_and_records_intermediate_steps(self):
        c,w=fixture();s=ManualSession(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300)))
        batch=s.propose([400800,s.starts[0][1]],20)
        self.assertEqual(len(s.points[0]),1)
        self.assertEqual([v['head'] for v in batch],[3,4,5,6,7,8])
        with self.assertRaises(CalculationError):s.propose([400800,5900000],1)
        self.assertEqual(len(s.steps[0]),0)
        for step in batch:s.append(step)
        self.assertEqual(s.heads[0][-1],8)

    def test_round_base_orientation_depth_rotation_and_group(self):
        for angle in (0,.7,2.4):
            rot=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
            starts=np.array([[0,-100],[0,100]])@rot.T+ORIGIN
            wells=np.array([[20,-30],[70,30]])@rot.T+ORIGIN
            settings=Settings(downstream_shape='rounded',arc_depth=80)
            arc=downstream_arc(starts,wells,settings)
            self.assertTrue(np.allclose(arc[[0,-1]],starts[::-1]))
            local=(arc-ORIGIN)@rot
            self.assertAlmostEqual(local[:,0].min(),-80,places=7)
            reversed_arc=downstream_arc(starts[::-1],wells,settings)
            self.assertTrue(np.allclose(arc,reversed_arc[::-1]))

    def test_automatic_rounded_polygon_contains_group_and_preserves_base_export(self):
        c,w=fixture(points=[[220,-30],[250,30]])
        result=calculate(prepare(c,'head',w,False,Settings(sample=20,step=10,max_gap=300,downstream_shape='rounded')))
        self.assertTrue(result['polygon'].isGeosValid())
        self.assertGreater(len(result['downstream_boundary'].asPolyline()),40)
        for f in w.getFeatures():self.assertTrue(result['polygon'].contains(f.geometry()))
        layers=result_layers(result)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path=Path(folder)/'arc.gpkg';export_result(path,layers['edited'],layers)
            layer=QgsVectorLayer(str(path)+'|layername=base','base','ogr')
            self.assertTrue(layer.isValid());self.assertEqual(layer.featureCount(),1)
            self.assertTrue(next(layer.getFeatures()).geometry().equals(result['downstream_boundary']))
            del layer

    def test_invalid_arc_setting_rejected(self):
        for s in (Settings(arc_depth=-1),Settings(arc_depth=float('nan')),Settings(downstream_shape='x')):
            with self.assertRaises(CalculationError):s.validate()


class StraightArcUiTests(unittest.TestCase):
    setUp=UiTests.setUp
    tearDown=UiTests.tearDown

    def test_add_next_visible_preview_round_control_draft_and_finish(self):
        self.d.manual_button.click();tool=self.plugin.extra_tool
        self.assertGreater(tool.bands[2].numberOfVertices(),1)
        self.assertEqual(tool.bands[3].numberOfVertices(),65)
        tool.panel.depth.setValue(120)
        self.assertEqual(tool.session.data['settings'].arc_depth,120)
        tool.panel.shape.setCurrentIndex(1);self.assertEqual(tool.bands[3].numberOfVertices(),2)
        tool.panel.shape.setCurrentIndex(0)
        for side in (0,1):
            tool.session.side=side
            tool.panel.buttons['Add next contour'].click()
            self.assertEqual(tool.session.heads[side][-1],3)
            self.assertIn('Added the 3 m',tool.panel.status.text())
        original=[np.array(v) for v in tool.session.points]
        tool.panel.depth.setValue(150);tool.panel.shape.setCurrentIndex(1);tool.panel.shape.setCurrentIndex(0);tool.panel.depth.setValue(120)
        for before,after in zip(original,tool.session.points):self.assertTrue(np.array_equal(before,after))
        self.assertIn('Straight segments',tool.panel.status.text())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'round.draft.json';save_draft(path,tool.session);restored=load_draft(path)
            self.assertEqual(restored.data['settings'].arc_depth,120)
            self.assertTrue(np.allclose(restored.base(),tool.session.base()))
            self.assertTrue(restored.result()['polygon'].isGeosValid())
        tool.panel.buttons['Finish polygon'].click()
        self.assertIn('base',self.d.layers,self.d.log.toPlainText())
        self.assertTrue(next(self.d.layers['edited'].getFeatures()).geometry().contains(next(self.w.getFeatures()).geometry()))


if __name__=='__main__':unittest.main(verbosity=2)
