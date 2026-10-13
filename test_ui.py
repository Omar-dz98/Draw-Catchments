"""Qt widget and background-task integration tests using actual PyQGIS.

The iface adapter below is explicit: these are Qt/PyQGIS tests, not proof of
interactive QGIS desktop success. desktop_smoke.py separately tests real iface.
"""
from test_engine import *
from qgis.PyQt.QtCore import QEvent, QEventLoop, QPoint, QTimer, Qt
from qgis.PyQt.QtGui import QHelpEvent
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QAction, QApplication, QMenu, QToolButton, QToolTip
from qgis.gui import QgsMapMouseEvent
from draw_catchments.points import point_key
from draw_catchments import classFactory


class IfaceAdapter:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(CRS)
        self.active = None
        self.messages = []
        self.actions = []
        self.vertex = QAction("Vertex tool", self.window)

    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def activeLayer(self): return self.active
    def setActiveLayer(self, layer): self.active = layer
    def addPluginToVectorMenu(self, name, action): self.actions.append(action)
    def addToolBarIcon(self, action): pass
    def removePluginVectorMenu(self, name, action): self.actions.remove(action)
    def removeToolBarIcon(self, action): pass
    def messageBar(self): return self
    def pushWarning(self, title, message): self.messages.append(message)
    def actionVertexTool(self): return self.vertex


class UiTests(unittest.TestCase):
    def setUp(self):
        self.iface = IfaceAdapter()
        self.c, self.w = fixture()
        QgsProject.instance().addMapLayers([self.c, self.w])
        self.w.selectAll()
        self.iface.active = self.w
        self.plugin = classFactory(self.iface)
        self.plugin.initGui()
        self.plugin.run()
        self.d = self.plugin.dialog
        self.d.contours.setLayer(self.c)
        self.d.head.setField("head")
        self.d.wells.setLayer(self.w)
        self.d.sample.setValue(20)
        self.d.step.setValue(10)
        self.d.gap.setValue(300)
        self.d.length.setValue(700)

    def tearDown(self):
        self.plugin.unload()
        QgsProject.instance().removeAllMapLayers()
        self.iface.canvas.deleteLater()
        self.iface.window.deleteLater()
        app.processEvents()

    def wait_task(self):
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if self.d.task is None else None)
        timer.start(50)
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        timer.stop()
        self.assertIsNone(self.d.task, "Background task did not complete")

    def test_plugin_lifecycle_and_dialog(self):
        self.assertEqual(self.d.windowTitle(), "Draw Catchments")
        self.assertTrue(self.d.isVisible())
        self.assertEqual(self.d.wells.currentLayer(), self.w)
        self.assertEqual(self.d.head.currentField(), "head")
        self.assertEqual(self.d.ids.currentField(), "well_id")
        self.assertEqual(len(self.iface.actions), 1)

    def test_reopening_recognizes_selected_wells(self):
        self.d.selection.setCurrentIndex(1)
        self.d.auto_select()
        self.assertEqual(self.d.selection.currentIndex(), 0)

    def test_hover_help_appears_on_field(self):
        self.assertTrue(all(widget.toolTip() for widget in self.d.help_widgets))
        position = QPoint(10, 10)
        event = QHelpEvent(QEvent.ToolTip, position, self.d.head.mapToGlobal(position))
        QApplication.sendEvent(self.d.head, event)
        self.assertIn("groundwater HEAD", QToolTip.text())
        QToolTip.hideText()

    def test_well_count_updates_with_selection(self):
        self.assertIn("1 selected of 1", self.d.well_summary.text())
        self.w.removeSelection()
        self.assertIn("0 selected of 1", self.d.well_summary.text())
        self.d.selection.setCurrentIndex(1)
        self.assertIn("all 1", self.d.well_summary.text())

    def test_point_dropdown_includes_other_project_layers(self):
        _, other=fixture(points=[[220,100],[220,200]])
        other.setName('Other point layer')
        QgsProject.instance().addMapLayer(other)
        self.d.point_choice.refresh()
        self.assertEqual(self.d.point_choice.count(),4) # placeholder + three points
        target=next(other.getFeatures())
        self.d.point_choice.setCurrentIndex(self.d.point_choice.findData(point_key(other,target.id(),0)))
        self.assertEqual(self.d.selection.currentIndex(),2)
        self.assertEqual(self.d.wells.currentLayer(),other)
        data=self.d.inputs()
        self.assertEqual(len(data['wells']),1)
        self.assertEqual(data['identifiers'][0]['fid'],target.id())

    def test_right_click_action_targets_only_clicked_feature(self):
        f=QgsFeature(self.w.fields())
        f.setAttributes(['distant'])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(460000,5800000)))
        self.w.dataProvider().addFeatures([f])
        self.w.selectAll()
        canvas=self.iface.canvas
        canvas.resize(800,600)
        canvas.setLayers([self.w,self.c])
        canvas.setExtent(QgsRectangle(400000,5799700,400800,5800300))
        canvas.refresh()
        app.processEvents()
        pixel=canvas.getCoordinateTransform().transform(QgsPointXY(400220,5800000))
        event=QgsMapMouseEvent(canvas,QEvent.MouseButtonRelease,QPoint(round(pixel.x()),round(pixel.y())),Qt.RightButton)
        menu=QMenu()
        canvas.contextMenuAboutToShow.emit(menu,event)
        actions=[a for a in menu.actions() if a.text().startswith('Draw Catchments')]
        self.assertEqual(len(actions),1)
        actions[0].trigger()
        self.assertEqual(self.d.selection.currentIndex(),2)
        self.assertEqual(self.w.selectedFeatureCount(),2)
        # Even changing QGIS selection afterwards must not change the target.
        self.w.removeSelection()
        self.d.generate_button.click()
        self.wait_task()
        self.assertIn('edited',self.d.layers,self.d.log.toPlainText())
        meta=json.loads(self.d.layers['edited'].customProperty('draw_catchments/metadata'))
        self.assertEqual(len(meta['wells']),1)
        self.assertEqual(meta['wells'][0]['id'],'0')

    def test_point_mode_rejects_unmatched_typed_text(self):
        f=next(self.w.getFeatures())
        self.d.use_point(self.w,f.id())
        self.d.point_choice.setEditText('nonexistent point')
        with self.assertRaisesRegex(CalculationError,'Choose one point'):
            self.d.inputs()

    def test_search_completion_selects_exact_single_point(self):
        other=QgsVectorLayer('Point?crs=EPSG:25833','Search target layer','memory')
        other.dataProvider().addAttributes([QgsField('name',QVariant.String)])
        other.updateFields()
        for name, x in [('Unique target well',400320),('Distant well',460000)]:
            f=QgsFeature(other.fields());f['name']=name
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x,5800000)))
            other.dataProvider().addFeatures([f])
        QgsProject.instance().addMapLayer(other);other.selectAll()
        self.d.point_choice.refresh()
        edit=self.d.point_choice.lineEdit()
        edit.setFocus();edit.selectAll()
        QTest.keyClicks(edit,'Unique target')
        app.processEvents()
        popup=self.d.point_choice.completer().popup()
        self.assertEqual(popup.model().rowCount(),1)
        index=popup.model().index(0,0)
        QTest.mouseClick(popup.viewport(),Qt.LeftButton,pos=popup.visualRect(index).center())
        app.processEvents()
        self.assertEqual(self.d.selection.currentIndex(),2)
        self.assertEqual(self.d.wells.currentLayer(),other)
        data=self.d.inputs()
        self.assertEqual(data['wells'].tolist(),[[400320.,5800000.]])
        self.d.generate_button.click();self.wait_task()
        self.assertIn('edited',self.d.layers,self.d.log.toPlainText())

    def test_pick_button_and_actual_map_click_uses_one_feature(self):
        f=QgsFeature(self.w.fields());f['well_id']='other'
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(460000,5800000)))
        self.w.dataProvider().addFeatures([f]);self.w.selectAll()
        canvas=self.iface.canvas;canvas.resize(800,600)
        canvas.setLayers([self.w,self.c])
        canvas.setExtent(QgsRectangle(400000,5799700,400800,5800300))
        canvas.show();app.processEvents()
        self.d.pick_button.click()
        self.assertFalse(self.d.isVisible())
        p=canvas.getCoordinateTransform().transform(QgsPointXY(400220,5800000))
        QTest.mouseClick(canvas.viewport(),Qt.LeftButton,pos=QPoint(round(p.x()),round(p.y())))
        app.processEvents()
        self.assertTrue(self.d.isVisible())
        self.assertEqual(self.d.inputs()['wells'].tolist(),[[400220.,5800000.]])
        self.assertEqual(self.w.selectedFeatureCount(),2)
        self.d.generate_button.click();self.wait_task()
        self.assertIn('edited',self.d.layers,self.d.log.toPlainText())

    def test_free_map_location_without_any_well_layer(self):
        QgsProject.instance().removeMapLayer(self.w.id())
        self.plugin.run_at_location(QgsPointXY(400320,5800050),CRS)
        self.assertEqual(self.d.selection.currentIndex(),2)
        self.assertEqual(self.d.inputs()['wells'].tolist(),[[400320.,5800050.]])
        self.d.generate_button.click();self.wait_task()
        self.assertIn('edited',self.d.layers,self.d.log.toPlainText())
        meta=json.loads(self.d.layers['edited'].customProperty('draw_catchments/metadata'))
        self.assertEqual(len(meta['wells']),1)
        self.assertEqual(meta['wells'][0]['xy'],[400320.,5800050.])
        self.plugin.run_at_location(QgsPointXY(400420,5800050),CRS)
        self.assertEqual(self.plugin.picked_layer.featureCount(),1)
        self.assertEqual(self.d.inputs()['wells'].tolist(),[[400420.,5800050.]])
        QgsProject.instance().removeMapLayer(self.plugin.picked_layer.id())
        self.plugin.run_at_location(QgsPointXY(400420,5800150),CRS)
        self.assertEqual(self.d.inputs()['wells'].tolist(),[[400420.,5800150.]])

    def test_unmatched_search_never_calculates_all_layer_points(self):
        self.d.selection.setCurrentIndex(1)
        edit=self.d.point_choice.lineEdit();edit.setFocus();edit.selectAll()
        QTest.keyClicks(edit,'Unmatched target')
        app.processEvents()
        self.assertEqual(self.d.selection.currentIndex(),2)
        with self.assertRaisesRegex(CalculationError,'Choose one point'):
            self.d.inputs()

    def test_click_empty_map_creates_location_and_polygon(self):
        canvas=self.iface.canvas;canvas.resize(800,600)
        canvas.setLayers([self.w,self.c]);canvas.setExtent(QgsRectangle(400000,5799700,400800,5800300))
        canvas.show();app.processEvents();self.d.pick_button.click()
        pixel=canvas.getCoordinateTransform().transform(QgsPointXY(400480,5800100))
        QTest.mouseClick(canvas.viewport(),Qt.LeftButton,pos=QPoint(round(pixel.x()),round(pixel.y())))
        app.processEvents()
        target=self.d.inputs()['wells']
        self.assertEqual(len(target),1)
        self.assertLess(np.linalg.norm(target[0]-[400480,5800100]),2)
        self.d.generate_button.click();self.wait_task()
        self.assertIn('edited',self.d.layers,self.d.log.toPlainText())

    def test_pick_cancel_restores_previous_tool(self):
        from qgis.gui import QgsMapToolPan
        previous=QgsMapToolPan(self.iface.canvas)
        self.iface.canvas.setMapTool(previous)
        self.d.pick_button.click()
        self.plugin.finish_picking()
        self.assertEqual(self.iface.canvas.mapTool(),previous)
        self.assertTrue(self.d.isVisible())

    def test_right_click_handles_other_layer_crs(self):
        other=QgsVectorLayer('Point?crs=EPSG:4326','Geographic point layer','memory')
        transform=QgsCoordinateTransform(CRS,other.crs(),QgsProject.instance())
        f=QgsFeature();f.setGeometry(QgsGeometry.fromPointXY(transform.transform(QgsPointXY(400420,5800000))))
        other.dataProvider().addFeatures([f]);other.updateExtents()
        QgsProject.instance().addMapLayer(other)
        canvas=self.iface.canvas;canvas.resize(800,600);canvas.setLayers([other,self.w,self.c])
        canvas.setExtent(QgsRectangle(400000,5799700,400800,5800300));app.processEvents()
        p=canvas.getCoordinateTransform().transform(QgsPointXY(400420,5800000))
        menu=QMenu();event=QgsMapMouseEvent(canvas,QEvent.MouseButtonRelease,QPoint(round(p.x()),round(p.y())),Qt.RightButton)
        canvas.contextMenuAboutToShow.emit(menu,event)
        action=next(a for a in menu.actions() if a.text().startswith('Draw Catchments'))
        action.trigger()
        self.assertEqual(self.d.wells.currentLayer(),other)
        data=self.d.inputs()
        self.assertEqual(len(data['wells']),1)
        self.assertLess(np.linalg.norm(data['wells'][0]-[400420,5800000]),0.01)

    def test_overlapping_points_offer_individual_choices(self):
        _,other=fixture()
        other.setName('Overlapping layer');QgsProject.instance().addMapLayer(other)
        canvas=self.iface.canvas;canvas.resize(800,600);canvas.setLayers([other,self.w,self.c])
        canvas.setExtent(QgsRectangle(400000,5799700,400800,5800300));app.processEvents()
        p=canvas.getCoordinateTransform().transform(QgsPointXY(400220,5800000))
        menu=QMenu();event=QgsMapMouseEvent(canvas,QEvent.MouseButtonRelease,QPoint(round(p.x()),round(p.y())),Qt.RightButton)
        canvas.contextMenuAboutToShow.emit(menu,event)
        submenu=next(a.menu() for a in menu.actions() if a.text().startswith('Draw Catchments'))
        self.assertEqual(len(submenu.actions()),2)
        next(a for a in submenu.actions() if 'Overlapping layer' in a.text()).trigger()
        self.assertEqual(self.d.wells.currentLayer(),other)
        self.assertEqual(len(self.d.inputs()['wells']),1)

    def test_optional_fields_enable_for_their_modes(self):
        self.assertFalse(self.d.starts.isEnabled())
        self.assertFalse(self.d.boundary.isEnabled())
        self.d.use_starts.setChecked(True)
        self.d.stop_mode.setCurrentIndex(2)
        self.assertTrue(self.d.starts.isEnabled())
        self.assertTrue(self.d.boundary.isEnabled())

    def test_small_screen_fields_are_scrollable(self):
        self.d.resize(560, 500)
        toggle = next(w for w in self.d.findChildren(QToolButton)
                      if w.text().startswith('Calculation settings'))
        toggle.setChecked(True)
        app.processEvents()
        self.assertGreater(self.d.controls_scroll.verticalScrollBar().maximum(), 0)
        self.assertTrue(self.d.generate_button.isVisible())

    def test_initial_contours_prefer_visible_layer(self):
        hidden, _ = fixture()
        QgsProject.instance().addMapLayer(hidden)
        QgsProject.instance().layerTreeRoot().findLayer(hidden).setItemVisibilityChecked(False)
        self.d.contours.setLayer(hidden)
        self.d.auto_select(initial=True)
        self.assertEqual(self.d.contours.currentLayer(), self.c)

    def test_output_lines_are_excluded_at_startup_and_when_added(self):
        from draw_catchments.service import memory_layer
        cap = memory_layer('LineString', '75 m contour closure | artificial limit', CRS)
        cap.setCustomProperty('draw_catchments/role', 'cap')
        QgsProject.instance().addMapLayer(cap)
        self.assertIn(cap, self.d.contours.exceptedLayerList())
        self.assertEqual(self.d.contours.currentLayer(), self.c)
        self.assertEqual(self.d.head.currentField(), 'head')
        self.plugin.unload()
        self.plugin = classFactory(self.iface)
        self.plugin.initGui(); self.plugin.run(); self.d = self.plugin.dialog
        self.assertEqual(self.d.contours.currentLayer(), self.c)
        self.assertEqual(self.d.head.currentField(), 'head')
        self.assertIn(cap, self.d.contours.exceptedLayerList())
        self.d.contours.setLayer(cap)
        self.assertNotEqual(self.d.contours.currentLayer(), cap)

    def test_reimported_outputs_excluded_without_custom_properties(self):
        from draw_catchments.service import memory_layer
        cap = memory_layer('LineString', 'Renamed exported result', CRS)
        QgsProject.instance().addMapLayer(cap)
        self.assertIn(cap, self.d.contours.exceptedLayerList())
        with self.assertRaisesRegex(CalculationError, 'result layer, not a groundwater contour plan'):
            prepare(cap, 'width_m', self.w, False, Settings())
        QgsProject.instance().removeMapLayer(cap.id())
        self.assertEqual(self.d.contours.currentLayer(), self.c)

    def test_source_change_suggests_head_instead_of_fid(self):
        c = QgsVectorLayer('LineString?crs=EPSG:25833', 'Different date contours', 'memory')
        c.dataProvider().addAttributes([QgsField('fid', QVariant.Int), QgsField('ZLEVEL', QVariant.Double)])
        c.updateFields(); c.setFieldAlias(1, 'Hydraulic head (m NHN)')
        QgsProject.instance().addMapLayer(c)
        self.d.contours.setLayer(c)
        self.assertEqual(self.d.head.currentField(), 'ZLEVEL')
        self.d.contours.setLayer(self.c)
        self.assertEqual(self.d.head.currentField(), 'head')

    def test_custom_head_field_requires_choice_and_is_remembered(self):
        # No naming whitelist: any actual numeric head attribute is supported.
        c = QgsVectorLayer('LineString?crs=EPSG:25833', 'My local plan', 'memory')
        c.dataProvider().addAttributes([QgsField('fid', QVariant.Int), QgsField('measured_level_2026', QVariant.Double)])
        c.updateFields(); QgsProject.instance().addMapLayer(c)
        self.d.contours.setLayer(c)
        self.assertEqual(self.d.head.currentField(), '')
        with self.assertRaisesRegex(CalculationError, 'Select the hydraulic-head attribute'):
            self.d.inputs()
        self.d.head.setField('measured_level_2026')
        self.d.auto_select()
        self.assertEqual(self.d.head.currentField(), 'measured_level_2026')
        self.d.contours.setLayer(self.c); self.d.contours.setLayer(c)
        self.assertEqual(self.d.head.currentField(), 'measured_level_2026')

    def test_alternative_head_choice_survives_reopening(self):
        self.c.dataProvider().addAttributes([QgsField('alternative_head', QVariant.Double)])
        self.c.updateFields(); self.d.head.setField('alternative_head')
        self.d.hide(); self.plugin.run()
        self.assertEqual(self.d.head.currentField(), 'alternative_head')

    def test_result_error_precedes_coverage_error(self):
        self.c.setCustomProperty('draw_catchments/role', 'cap')
        with self.assertRaisesRegex(CalculationError, 'original groundwater contours'):
            prepare(self.c, 'head', self.w, False, Settings(auto_extent=True))

    def test_removing_current_contour_updates_field(self):
        extra, _ = fixture()
        QgsProject.instance().addMapLayer(extra)
        self.d.contours.setLayer(extra)
        QgsProject.instance().removeMapLayer(extra.id())
        self.d.auto_select()
        self.assertEqual(self.d.contours.currentLayer(), self.c)
        self.assertEqual(self.d.head.currentField(), 'head')

    def test_button_task_polygon_edit(self):
        self.d.generate_button.click()
        self.wait_task()
        self.assertIn("edited", self.d.layers, self.d.log.toPlainText())
        layer = self.d.layers["edited"]
        self.assertTrue(next(layer.getFeatures()).geometry().isGeosValid())
        self.d.edit_button.click()
        self.assertTrue(layer.isEditable())
        self.assertTrue(self.d.layers["original"].readOnly())

    def test_suggest_adjust_recalculate(self):
        self.d.suggest_button.click()
        layer = self.d.starts.currentLayer()
        self.assertEqual(layer.featureCount(), 2)
        changes = {}
        for f in layer.getFeatures():
            g = f.geometry()
            g.translate(-10, 0)
            changes[f.id()] = g
        layer.dataProvider().changeGeometryValues(changes)
        self.d.use_starts.setChecked(True)
        self.d.generate_button.click()
        self.wait_task()
        self.assertIn("edited", self.d.layers, self.d.log.toPlainText())

    def test_map_boundary(self):
        self.iface.canvas.setExtent(QgsRectangle(400100,5799600,400650,5800400))
        self.d.stop_mode.setCurrentIndex(1)
        self.d.generate_button.click()
        self.wait_task()
        self.assertIn("edited", self.d.layers, self.d.log.toPlainText())

    def test_bundled_example(self):
        self.d.load_example()
        self.assertEqual(self.d.head.currentField(), "head")
        self.assertEqual(self.d.wells.currentLayer().selectedFeatureCount(), 1)
        self.d.generate_button.click()
        self.wait_task()
        self.assertIn("edited", self.d.layers, self.d.log.toPlainText())

    def test_target_rectangle_map_clicks_and_cancel(self):
        canvas=self.iface.canvas;canvas.resize(900,700);canvas.show()
        canvas.setLayers([self.w,self.c]);canvas.setExtent(QgsRectangle(400000,5799600,401000,5800400));app.processEvents()
        self.d.target_button.click()
        self.assertIsNotNone(self.plugin.extra_tool)
        for point in (QgsPointXY(400750,5799900),QgsPointXY(400970,5800100)):
            pixel=canvas.getCoordinateTransform().transform(point)
            QTest.mouseClick(canvas.viewport(),Qt.LeftButton,pos=QPoint(round(pixel.x()),round(pixel.y())))
        app.processEvents()
        self.assertIsNone(self.plugin.extra_tool)
        self.assertEqual(self.d.stop_mode.currentIndex(),3)
        self.assertIsNotNone(self.d.inputs()['upstream_target'])
        self.assertTrue(self.d.isVisible())
        previous=self.d.target_geometry.asWkt()
        self.d.target_button.click();QTest.keyClick(canvas,Qt.Key_Escape);app.processEvents()
        self.assertEqual(self.d.target_geometry.asWkt(),previous)

    def test_manual_perpendicular_map_workflow(self):
        canvas=self.iface.canvas;canvas.resize(900,700);canvas.show()
        canvas.setLayers([self.w,self.c]);canvas.setExtent(QgsRectangle(400000,5799600,401000,5800400));app.processEvents()
        self.d.radius.setValue(50)
        self.d.manual_button.click()
        self.assertIsNotNone(self.plugin.extra_tool,self.d.log.toPlainText())
        session=self.plugin.extra_tool.session
        for side in (0,1):
            for x in (300,400,500,600,700,800):
                pixel=canvas.getCoordinateTransform().transform(QgsPointXY(400000+x,session.points[side][-1][1]))
                QTest.mouseClick(canvas.viewport(),Qt.LeftButton,pos=QPoint(round(pixel.x()),round(pixel.y())))
            QTest.mouseClick(canvas.viewport(),Qt.RightButton,pos=QPoint(100,100))
            app.processEvents()
        self.assertIsNone(self.plugin.extra_tool,self.d.log.toPlainText())
        self.assertIn('edited',self.d.layers,self.d.log.toPlainText())
        self.assertTrue(next(self.d.layers['edited'].getFeatures()).geometry().isGeosValid())
        meta=json.loads(self.d.layers['edited'].customProperty('draw_catchments/metadata'))
        self.assertTrue(all(abs(step['angle_degrees']-90)<1e-8 for side in meta['manual_steps'] for step in side))


if __name__ == "__main__":
    unittest.main(verbosity=2)
