"""Test the actual ZIP using QGIS's loader and real Qt widgets with an iface adapter."""
import configparser
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from qgis.PyQt.QtCore import QEventLoop, QTimer
from qgis.PyQt.QtGui import QFont, QFontDatabase
from qgis.PyQt.QtWidgets import QAction, QMainWindow, QMessageBox, QApplication
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, Qgis, QgsFeature, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapCanvas
import qgis.utils

ROOT = Path(__file__).resolve().parents[1]
app = QgsApplication([], False)
app.initQgis()
if Path('C:/Windows/Fonts/arial.ttf').is_file():
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
    QApplication.setFont(QFont('Arial',9))
folder = tempfile.mkdtemp(prefix="draw_catchments_zip_")
metadata = configparser.ConfigParser()
metadata.read(ROOT / "plugin/draw_catchments/metadata.txt", encoding="utf-8")
version = metadata['general']['version']
with zipfile.ZipFile(ROOT / "dist" / f"Draw_Catchments-{version}.zip") as archive:
    archive.extractall(folder)
sys.path.insert(0, folder)
qgis.utils.plugin_paths.insert(0, folder)


class Iface:
    def __init__(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:25833"))
        self.active = None
    def mainWindow(self): return self.window
    def mapCanvas(self): return self.canvas
    def activeLayer(self): return self.active
    def setActiveLayer(self, layer): self.active = layer
    def addPluginToVectorMenu(self, *args): pass
    def addToolBarIcon(self, *args): pass
    def removePluginVectorMenu(self, *args): pass
    def removeToolBarIcon(self, *args): pass
    def messageBar(self): return self
    def pushWarning(self, title, message): print("WARNING", message)
    def actionVertexTool(self): return QAction(self.window)


qgis.utils.iface = Iface()
qgis.utils.updateAvailablePlugins()
assert "draw_catchments" in qgis.utils.available_plugins
assert qgis.utils.pluginMetadata("draw_catchments", "name") == "Draw Catchments"
assert qgis.utils.pluginMetadata("draw_catchments", "author") == "Omar Brahimi"
assert qgis.utils.loadPlugin("draw_catchments")
assert str(Path(sys.modules["draw_catchments"].__file__).resolve()).startswith(str(Path(folder).resolve()))
assert qgis.utils.startPlugin("draw_catchments")
plugin = qgis.utils.plugins["draw_catchments"]
plugin.run()
dialog = plugin.dialog
assert dialog.isVisible()
assert all(widget.toolTip() for widget in dialog.help_widgets)
assert hasattr(dialog, 'point_choice')
dialog.load_example()
dialog.generate_button.click()
loop = QEventLoop()
timer = QTimer()
timer.timeout.connect(lambda: loop.quit() if dialog.task is None else None)
timer.start(50)
QTimer.singleShot(60000, loop.quit)
loop.exec()
timer.stop()
assert dialog.task is None
assert "edited" in dialog.layers, dialog.log.toPlainText()
assert 'cap' in dialog.layers
geom = next(dialog.layers["edited"].getFeatures()).geometry()
assert geom.isGeosValid()
target=next(dialog.wells.currentLayer().getFeatures())
plugin.run_for_point(dialog.wells.currentLayer(),target.id())
assert dialog.selection.currentText() == 'One point'
assert len(dialog.inputs()['wells']) == 1
dialog.edit_button.click()
assert dialog.layers["edited"].isEditable()
# Exercise the actual packaged New/Resume buttons, including the real Qt choice.
assert dialog.manual_button.text() == 'Start new drawing for selected point'
assert dialog.resume_manual_button.text() == 'Resume drawing'
dialog.manual_button.click()
assert plugin.extra_tool is not None, dialog.log.toPlainText()
plugin.extra_tool.panel.buttons['Add next contour'].click()
old = plugin.extra_tool.session
assert len(old.steps[0]) == 1
plugin.extra_tool.panel.buttons['Pause / return'].click()
wells = dialog.wells.currentLayer()
point = target.geometry().asPoint()
new_xy = QgsPointXY(point.x()+100, point.y()+40)
feature = QgsFeature(wells.fields())
feature['well_id'] = 'zip-new-point'
feature.setGeometry(QgsGeometry.fromPointXY(new_xy))
assert wells.dataProvider().addFeatures([feature])[0]
wells.updateExtents()
new = next(f for f in wells.getFeatures() if f['well_id'] == 'zip-new-point')
dialog.use_point(wells, new.id())
dialog.resume_manual_button.click()
assert plugin.extra_tool.session is old
plugin.extra_tool.panel.buttons['Pause / return'].click()
assert dialog.grab().save(str(ROOT / 'outputs/new_or_resume_dialog.png'))
choice_clicked = []
def discard_previous():
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QMessageBox) and widget.windowTitle() == 'Start a new drawing':
            for button in widget.buttons():
                if button.text() == 'Discard & start new':
                    choice_clicked.append(True)
                    button.click()
                    return
QTimer.singleShot(20, discard_previous)
dialog.manual_button.click()
assert choice_clicked
fresh = plugin.extra_tool.session
assert fresh is not old and fresh is plugin.manual_session
assert fresh.steps == [[], []]
assert abs(fresh.data['wells'][0][0]-new_xy.x()) < 1e-6
assert abs(fresh.data['wells'][0][1]-new_xy.y()) < 1e-6
for side in (0, 1):
    fresh.side = side
    for _ in range(2):
        fresh.append(fresh.next_step())
plugin.extra_tool.panel.buttons['Finish polygon'].click()
new_geom = next(dialog.layers['edited'].getFeatures()).geometry()
assert new_geom.isGeosValid() and new_geom.contains(new.geometry())
assert not dialog.resume_manual_button.isEnabled()
assert qgis.utils.unloadPlugin("draw_catchments")
result = {"status":"PASS", "qgis":Qgis.QGIS_VERSION, "version":version, "author":"Omar Brahimi", "hover_help_present":True, "individual_point_target_verified":True, "loaded_from_zip":True,
          "loader":"qgis.utils.loadPlugin/startPlugin/unloadPlugin", "interface":"real Qt widgets; explicit iface adapter; offscreen",
          "bundled_example_polygon_valid":True, "editable":True, "area_m2":geom.area(),
          "contour_closure_layer_present":True,
          "separate_new_and_resume_buttons":True,
          "resume_keeps_previous_point":True,
          "new_drawing_uses_new_point":True,
          "new_point_manual_polygon_valid_and_contains_point":True,
          "interactive_desktop_tested":False}
(ROOT / "outputs/zip_smoke_test.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result,indent=2))
