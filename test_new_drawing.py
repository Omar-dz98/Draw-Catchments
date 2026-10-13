"""Starting a different point is distinct from resuming a saved manual session."""
from test_ui import *
from unittest.mock import patch
from draw_catchments.draft import load_draft
from qgis.PyQt.QtWidgets import QMessageBox


class NewDrawingTests(unittest.TestCase):
    setUp=UiTests.setUp
    tearDown=UiTests.tearDown

    def pause_old(self,custom_starts=False):
        first=next(self.w.getFeatures());self.d.use_point(self.w,first.id())
        if custom_starts:self.d.set_start_points(ORIGIN+[[200,-80],[200,80]],CRS)
        self.d.manual_button.click();tool=self.plugin.extra_tool
        self.assertIsNotNone(tool,self.d.log.toPlainText())
        tool.panel.buttons['Add next contour'].click()
        session=tool.session;self.assertEqual(len(session.steps[0]),1)
        tool.panel.buttons['Pause / return'].click()
        return session

    def select_another(self):
        f=QgsFeature(self.w.fields());f['well_id']='another'
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*(ORIGIN+[520,200]))))
        self.w.dataProvider().addFeatures([f]);self.w.updateExtents()
        new=next(f for f in self.w.getFeatures() if f['well_id']=='another')
        self.d.use_point(self.w,new.id());return new

    def test_resume_keeps_original_point_even_after_selection_changes(self):
        old=self.pause_old();self.select_another();before=np.array(old.points[0])
        self.assertTrue(self.d.manual_button.isEnabled());self.assertTrue(self.d.resume_manual_button.isEnabled())
        self.d.resume_manual_button.click()
        self.assertIs(self.plugin.extra_tool.session,old)
        self.assertTrue(np.array_equal(before,old.points[0]))
        self.assertTrue(np.allclose(old.data['wells'],ORIGIN+[[220,0]]))

    def test_new_point_discards_only_after_choice_and_resets_old_arc(self):
        old=self.pause_old(custom_starts=True);new=self.select_another()
        # Map picking already clears this flag; a dropdown choice or a user
        # rechecking the old layer must also be safe when starting fresh.
        self.d.use_starts.setChecked(True)
        old_layer=self.d.starts.currentLayer();self.assertTrue(self.d.use_starts.isChecked())
        with patch.object(self.plugin,'choose_previous_drawing_action',return_value='discard'):
            self.d.manual_button.click()
        fresh=self.plugin.extra_tool.session
        self.assertIsNot(fresh,old);self.assertIs(self.plugin.manual_session,fresh)
        self.assertEqual(fresh.steps,[[],[]]);self.assertFalse(self.d.use_starts.isChecked())
        self.assertTrue(np.allclose(fresh.data['wells'],ORIGIN+[[520,200]]))
        self.assertGreater(np.linalg.norm(fresh.starts.mean(axis=0)-old.starts.mean(axis=0)),200)
        self.assertIsNotNone(QgsProject.instance().mapLayer(old_layer.id()))
        # Complete a real, editable polygon for the NEW point.
        for side in (0,1):
            fresh.side=side
            for _ in range(2):fresh.append(fresh.next_step())
        self.plugin.extra_tool.panel.buttons['Finish polygon'].click()
        polygon=next(self.d.layers['edited'].getFeatures()).geometry()
        self.assertTrue(polygon.isGeosValid());self.assertTrue(polygon.contains(new.geometry()))
        self.assertFalse(self.d.resume_manual_button.isEnabled())

    def test_new_point_save_keeps_reloadable_previous_work(self):
        old=self.pause_old();self.select_another()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.draft.json'
            with patch.object(self.plugin,'choose_previous_drawing_action',return_value='save'),patch(
                'draw_catchments.plugin.QFileDialog.getSaveFileName',return_value=(str(path),'')):
                self.d.manual_button.click()
            restored=load_draft(path)
            self.assertTrue(np.array_equal(restored.points[0],old.points[0]))
            self.assertTrue(np.array_equal(restored.data['wells'],old.data['wells']))
            self.assertIsNot(self.plugin.manual_session,old)
            self.assertTrue(np.allclose(self.plugin.manual_session.data['wells'],ORIGIN+[[520,200]]))

    def test_cancel_new_or_save_dialog_preserves_old_drawing(self):
        old=self.pause_old(custom_starts=True);self.select_another();before=np.array(old.points[0])
        self.d.use_starts.setChecked(True)
        for decision in ('cancel','save'):
            with patch.object(self.plugin,'choose_previous_drawing_action',return_value=decision),patch(
                'draw_catchments.plugin.QFileDialog.getSaveFileName',return_value=('','')):
                self.d.manual_button.click()
            self.assertIs(self.plugin.manual_session,old);self.assertIsNone(self.plugin.extra_tool)
            self.assertTrue(self.d.use_starts.isChecked());self.assertTrue(np.array_equal(old.points[0],before))
        self.d.resume_manual_button.click();self.assertIs(self.plugin.extra_tool.session,old)

    def test_failed_save_or_invalid_new_input_never_destroys_old_work(self):
        old=self.pause_old();self.select_another()
        with patch.object(self.plugin,'choose_previous_drawing_action',return_value='save'),patch.object(
            self.plugin,'save_previous_drawing',side_effect=OSError('Disk write failed')):
            self.d.manual_button.click()
        self.assertIs(self.plugin.manual_session,old);self.assertIn('Disk write failed',self.d.log.toPlainText())
        with patch.object(self.d,'inputs',side_effect=CalculationError('Invalid new input')),patch.object(
            self.plugin,'choose_previous_drawing_action') as choose:
            self.d.manual_button.click();choose.assert_not_called()
        self.assertIs(self.plugin.manual_session,old)

    def test_intentionally_new_arc_layer_is_preserved(self):
        old=self.pause_old(custom_starts=True);self.select_another()
        chosen=ORIGIN+[[500,130],[500,270]];self.d.set_start_points(chosen,CRS)
        with patch.object(self.plugin,'choose_previous_drawing_action',return_value='discard'):
            self.d.manual_button.click()
        self.assertTrue(self.d.use_starts.isChecked())
        self.assertTrue(np.array_equal(self.plugin.manual_session.starts,chosen))

    def test_active_map_tool_cannot_restore_old_session_over_new(self):
        self.pause_old();self.d.resume_manual_button.click();old=self.plugin.extra_tool.session
        self.select_another()
        with patch.object(self.plugin,'choose_previous_drawing_action',return_value='discard'):
            self.plugin.start_perpendicular()
        self.assertIsNot(self.plugin.manual_session,old)
        self.assertIs(self.plugin.manual_session,self.plugin.extra_tool.session)
        self.plugin.finish_extra_tool();self.d.resume_manual_button.click()
        self.assertTrue(np.allclose(self.plugin.extra_tool.session.data['wells'],ORIGIN+[[520,200]]))

    def test_actual_choice_dialog_offers_save_discard_and_cancel(self):
        old=self.pause_old();self.select_another();labels=[]
        def cancel_visible_dialog():
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget,QMessageBox) and widget.windowTitle()=='Start a new drawing':
                    labels.extend(b.text() for b in widget.buttons())
                    widget.button(QMessageBox.Cancel).click();return
        QTimer.singleShot(20,cancel_visible_dialog)
        self.d.manual_button.click()
        self.assertIn('Save draft & start new',labels);self.assertIn('Discard & start new',labels)
        self.assertIs(self.plugin.manual_session,old)


if __name__=='__main__':unittest.main(verbosity=2)
