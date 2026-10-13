"""Finish, preserve, reload and export real QGIS manual geometries."""
from test_ui import *
from unittest.mock import patch
from draw_catchments.draft import save_draft,load_draft


class ManualWorkflowTests(unittest.TestCase):
    setUp=UiTests.setUp
    tearDown=UiTests.tearDown

    def draw_sides(self,both=True):
        self.d.manual_button.click()
        self.assertIsNotNone(self.plugin.extra_tool,self.d.log.toPlainText())
        tool=self.plugin.extra_tool
        for side in ((0,1) if both else (0,)):
            tool.session.side=side
            for x in (300,400,500,600,700,800):
                step=tool.session.candidate([400000+x,tool.session.points[side][-1][1]],20)
                tool.session.append(step)
        tool.update()
        return tool

    def test_finish_button_closes_near_station(self):
        tool=self.draw_sides();self.assertTrue(tool.panel.isVisible())
        self.assertGreater(tool.bands[3].numberOfVertices(),1)
        tool.panel.buttons['Finish polygon'].click()
        self.assertIsNone(self.plugin.extra_tool)
        self.assertIsNone(self.plugin.manual_session)
        polygon=next(self.d.layers['edited'].getFeatures()).geometry()
        self.assertTrue(polygon.isGeosValid())
        self.assertTrue(polygon.contains(next(self.w.getFeatures()).geometry()))

    def test_failed_finish_keeps_drawing_and_explains(self):
        tool=self.draw_sides(both=False);before=tool.session.points[0].copy()
        tool.panel.buttons['Finish polygon'].click()
        self.assertIs(self.plugin.extra_tool,tool)
        self.assertIn('Draw at least one',tool.panel.status.text())
        self.assertTrue(np.array_equal(before,tool.session.points[0]))

    def test_pause_and_resume_preserve_vertices(self):
        tool=self.draw_sides();session=tool.session
        tool.panel.buttons['Pause / return'].click()
        self.assertIsNone(self.plugin.extra_tool)
        self.assertIs(self.plugin.manual_session,session)
        self.assertEqual(self.d.manual_button.text(),'Start new drawing for selected point')
        self.assertTrue(self.d.resume_manual_button.isEnabled())
        self.d.resume_manual_button.click()
        self.assertIs(self.plugin.extra_tool.session,session)
        self.assertTrue(self.plugin.extra_tool.panel.isVisible())

    def test_finish_and_export_button_creates_reimportable_polygon(self):
        tool=self.draw_sides()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path=Path(folder)/'finished.gpkg'
            with patch('draw_catchments.plugin.QFileDialog.getSaveFileName',return_value=(str(path),'GeoPackage (*.gpkg)')):
                tool.panel.buttons['Finish & export…'].click()
            layer=QgsVectorLayer(str(path)+'|layername=catchment','saved','ogr')
            self.assertTrue(layer.isValid(),self.d.log.toPlainText())
            self.assertTrue(next(layer.getFeatures()).geometry().contains(next(self.w.getFeatures()).geometry()))
            del layer

    def test_main_export_finishes_paused_drawing(self):
        tool=self.draw_sides();tool.panel.buttons['Pause / return'].click()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'paused.geojson'
            with patch('draw_catchments.plugin.QFileDialog.getSaveFileName',return_value=(str(path),'GeoJSON (*.geojson)')):
                self.d.export_button.click()
            self.assertTrue(path.exists(),self.d.log.toPlainText())
            self.assertIsNone(self.plugin.manual_session)

    def test_draft_save_restart_load_continue_finish(self):
        tool=self.draw_sides(both=False)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'work.draft.json'
            with patch('draw_catchments.maptools.QFileDialog.getSaveFileName',return_value=(str(path),'Drawing draft (*.draft.json)')):
                tool.panel.buttons['Save drawing draft…'].click()
            self.assertTrue(path.exists())
            self.plugin.unload()
            self.plugin=classFactory(self.iface);self.plugin.initGui();self.plugin.run();self.d=self.plugin.dialog
            with patch('draw_catchments.plugin.QFileDialog.getOpenFileName',return_value=(str(path),'Drawing draft (*.draft.json)')):
                self.d.open_draft_button.click()
            tool=self.plugin.extra_tool;self.assertIsNotNone(tool,self.d.log.toPlainText())
            self.assertEqual(len(tool.session.steps[0]),6)
            tool.panel.buttons['Undo last step'].click();self.assertEqual(len(tool.session.steps[0]),5)
            session=tool.session
            session.append(session.candidate([400800,session.points[0][-1][1]],20))
            tool.panel.buttons['Switch side'].click()
            for x in (300,400,500,600,700,800):
                session.append(session.candidate([400000+x,session.points[1][-1][1]],20))
            tool.panel.buttons['Finish polygon'].click()
            self.assertIn('edited',self.d.layers,self.d.log.toPlainText())

    def test_draft_load_with_empty_project(self):
        tool=self.draw_sides()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'work.draft.json';save_draft(path,tool.session)
            self.plugin.unload();QgsProject.instance().removeAllMapLayers()
            self.iface.active=None
            self.plugin=classFactory(self.iface);self.plugin.initGui();self.plugin.run();self.d=self.plugin.dialog
            with patch('draw_catchments.plugin.QFileDialog.getOpenFileName',return_value=(str(path),'')):
                self.d.open_draft_button.click()
            self.assertIsNotNone(self.plugin.extra_tool,self.d.log.toPlainText())
            self.assertTrue(any(l.name()=='Draft source contours' for l in QgsProject.instance().mapLayers().values()))
            self.plugin.extra_tool.panel.buttons['Finish polygon'].click()
            self.assertIn('edited',self.d.layers,self.d.log.toPlainText())

    def test_invalid_draft_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'invalid.json';path.write_text('{"format":"unrelated"}')
            with self.assertRaisesRegex(CalculationError,'Cannot load drawing draft'):load_draft(path)


if __name__=='__main__':unittest.main(verbosity=2)
