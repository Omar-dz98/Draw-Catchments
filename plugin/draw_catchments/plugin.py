"""English QGIS UI. Layer reads and GUI updates remain on the main thread.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from pathlib import Path
import traceback

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QGuiApplication, QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QMenu, QScrollArea, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsFeature, QgsField, QgsFieldProxyModel, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
    QgsMapLayerProxyModel, QgsMarkerSymbol, QgsMessageLog, QgsProject, QgsTask,
    QgsPointXY, QgsVectorLayer, QgsWkbTypes,
)
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox, QgsMapToolIdentify, QgsProjectionSelectionWidget
from .points import ProjectPointCombo, SinglePointMapTool, point_key, points_at_click
from .contour_inputs import ContourLayerCombo, is_result_layer, suggested_head_field


class CalculationTask(QgsTask):
    def __init__(self, data, polygon, callback):
        super().__init__("Draw Catchments", QgsTask.CanCancel)
        self.data, self.polygon, self.callback = data, polygon, callback
        self.result, self.error = None, None

    def run(self):
        try:
            from .service import calculate
            self.result = calculate(self.data, self.polygon, self.isCanceled)
            return not self.isCanceled()
        except Exception as exc:
            self.error = str(exc)
            QgsMessageLog.logMessage(traceback.format_exc(), "Draw Catchments", Qgis.Warning)
            return False

    def finished(self, success):
        if self.callback:
            self.callback(self.result if success else None, self.error or "Calculation canceled.")


class CatchmentDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner.iface.mainWindow())
        self.owner, self.iface = owner, owner.iface
        self.layers, self.task = {}, None
        self.setWindowTitle("Draw Catchments")
        self.setMinimumWidth(530)
        layout = QVBoxLayout(self)
        title = QLabel("<b>Draw Catchments</b> · groundwater boundary assistant<br><small>Created by Omar Brahimi</small>")
        layout.addWidget(title)
        intro = QLabel("Create a preliminary geometric proposal from hydraulic-head contours.\nWidth is an assumption; results require hydrogeological review.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.pick_button = QPushButton("Pick one point on map")
        self.pick_button.clicked.connect(self.owner.start_picking)
        layout.addWidget(self.pick_button)
        example_button = QPushButton("Load bundled test example")
        self.example_button = example_button
        example_button.clicked.connect(self.load_example)
        layout.addWidget(example_button)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.NoFrame)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_scroll.setWidget(controls)
        layout.addWidget(self.controls_scroll, 1)
        form = QFormLayout()
        controls_layout.addLayout(form)
        self.contours = ContourLayerCombo()
        self.head = QgsFieldComboBox()
        self.head.setFilters(QgsFieldProxyModel.Numeric)
        self.head.setAllowEmptyFieldName(True)
        self._head_fields = {}
        self.contours.layerChanged.connect(self.select_contour_head)
        self.head.fieldChanged.connect(self.remember_head_field)
        self.select_contour_head(self.contours.currentLayer())
        self.wells = self.layer_combo(QgsMapLayerProxyModel.PointLayer)
        self.ids = QgsFieldComboBox()
        self.wells.layerChanged.connect(self.ids.setLayer)
        self.wells.layerChanged.connect(self.select_well_identifier)
        self.ids.setLayer(self.wells.currentLayer())
        self.selection = QComboBox()
        self.selection.addItems(["Selected wells", "All wells", "One point"])
        self.selection.setCurrentIndex(2)
        self.point_choice = ProjectPointCombo()
        self.point_choice.refresh()
        self.point_choice.currentIndexChanged.connect(self.point_chosen)
        self.point_choice.editTextChanged.connect(self.point_chosen)
        self.point_choice.activated.connect(self.point_chosen)
        self.point_choice.lineEdit().textEdited.connect(lambda text: self.selection.setCurrentIndex(2))
        self.selection.setToolTip("One calculation creates one shared catchment. Select wells from one local waterworks; calculate distant sites separately.")
        self.width_mode = QComboBox()
        self.width_mode.addItems(["Automatic geometric proposal", "Manual width"])
        self.width = self.spin(0.1, 1000000, 200, " m")
        self.width.setEnabled(False)
        self.width_mode.currentIndexChanged.connect(lambda i: self.width.setEnabled(i == 1))
        self.suggest_button = QPushButton("Suggest width / create start points")
        self.suggest_button.clicked.connect(self.suggest)
        self.place_starts_button=QPushButton('Place arc endpoints on map')
        self.place_starts_button.clicked.connect(self.owner.start_endpoint_picking)
        self.place_starts_button.setToolTip('Click the two endpoints of the arc around the well yourself. They also become the starts of the two uphill sides. Their exact positions are kept. Orange previews the closure; depth is adjustable below.')
        self.downstream_shape=QComboBox();self.downstream_shape.addItems(['Rounded arc around point','Straight closure'])
        self.arc_depth=self.spin(0,1000000,0,' m');self.arc_depth.setSpecialValueText('Automatic: half the width')
        self.downstream_shape.currentIndexChanged.connect(lambda i:self.arc_depth.setEnabled(i==0))
        self.downstream_shape.setToolTip('Close the downstream end with an arc outside the selected point(s), or a straight base. This is an adjustable geometric assumption, not a hydraulic capture boundary. The orange dashed preview is shown during manual drawing.')
        self.arc_depth.setToolTip('Distance the downstream arc extends beyond the line between the two starts. Zero uses half the starting width. You can adjust this in the manual drawing panel and see the preview before finishing.')
        self.use_starts = QCheckBox("Use adjusted start-point layer (exactly 2 points)")
        self.starts = self.layer_combo(QgsMapLayerProxyModel.PointLayer, True)
        self.stop_mode = QComboBox()
        self.stop_mode.addItems(["Highest connected contour (follow its shape)", "Current map extent (artificial)", "Custom boundary layer", "Marked upstream target area"])
        self.target_geometry, self.target_crs, self.target_layer = None, None, None
        self.target_button = QPushButton('Mark upstream target area on map')
        self.target_button.clicked.connect(self.owner.start_target_picking)
        self.target_summary = QLabel('Optional: mark the groundwater high the boundaries should reach.')
        self.target_summary.setWordWrap(True)
        self.stop_mode.currentIndexChanged.connect(self.update_target_visibility)
        self.boundary = self.layer_combo(QgsMapLayerProxyModel.LineLayer | QgsMapLayerProxyModel.PolygonLayer, True)
        self.starts.setEnabled(False)
        self.use_starts.toggled.connect(self.starts.setEnabled)
        self.boundary.setEnabled(False)
        self.stop_mode.currentIndexChanged.connect(lambda i: self.boundary.setEnabled(i == 2))
        self.well_summary = QLabel()
        self.well_summary.setWordWrap(True)
        for label, widget in [("Contour layer", self.contours), ("Head field (m)", self.head),
                              ("Well / point layer", self.wells), ("Point identifier", self.ids),
                              ("Use", self.selection), ("Point", self.point_choice), ("", self.well_summary), ("Starting width", self.width_mode),
                              ("Width", self.width), ("", self.suggest_button), ('',self.place_starts_button),
                              ('Downstream closure',self.downstream_shape),('Arc depth',self.arc_depth),
                              ("", self.use_starts), ("Start-point layer", self.starts),
                              ("Upstream limit", self.stop_mode), ("Boundary layer", self.boundary),
                              ("Upstream target", self.target_button), ('',self.target_summary)]:
            form.addRow(label, widget)
        advanced_toggle = QToolButton()
        advanced_toggle.setText("Calculation settings")
        advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        advanced_toggle.setArrowType(Qt.RightArrow)
        advanced_toggle.setCheckable(True)
        controls_layout.addWidget(advanced_toggle)
        advanced = QGroupBox("Calculation settings")
        adv = QFormLayout(advanced)
        self.crs = QgsProjectionSelectionWidget()
        self.crs.setCrs(QgsCoordinateReferenceSystem("EPSG:25833"))
        self.auto_crs = QCheckBox("Choose calculation CRS automatically")
        self.auto_crs.setChecked(True)
        self.crs.setEnabled(False)
        self.auto_crs.toggled.connect(lambda checked: self.crs.setEnabled(not checked))
        self.radius = self.spin(10, 100000, 5000, " m")
        self.sample = self.spin(0.1, 5000, 30, " m")
        self.step = self.spin(0.1, 1000, 20, " m")
        self.gap = self.spin(1, 20000, 4000, " m")
        self.limit_gap = QCheckBox('Apply a maximum contour-spacing limit')
        self.limit_gap.setChecked(False);self.gap.setEnabled(False)
        self.limit_gap.toggled.connect(self.gap.setEnabled)
        self.auto_extent = QCheckBox('Expand analysis automatically for surrounding contours')
        self.auto_extent.setChecked(True)
        self.limit_gap.setToolTip('Off by default: contour spacing alone does not reject a point. Enable only to impose a chosen interpolation-gap threshold. Input contours must still cover the point.')
        self.auto_extent.setToolTip('Expand the analysis when upstream paths approach its edge and the source contours continue beyond it. Preserve the starting points and extend the path budget. Also expand to surround the selected wells. A source-data limit is still a limit, not a verified divide.')
        self.length = self.spin(1, 100000, 10000, " m")
        self.interpolation = QComboBox()
        self.interpolation.addItems(['Contour-constrained harmonic (recommended)','Smooth cubic (legacy)'])
        self.style_inputs = QCheckBox("Apply blue contour / dark well styling")
        for label, widget in [("", self.auto_crs), ("Calculation CRS (metres)", self.crs), ("Analysis radius", self.radius),
                              ("Contour sample spacing", self.sample), ("Tracing step", self.step),
                              ('',self.auto_extent), ('',self.limit_gap), ("Maximum support gap", self.gap), ("Maximum path length", self.length), ('Interpolation',self.interpolation),
                              ("", self.style_inputs)]:
            adv.addRow(label, widget)
        controls_layout.addWidget(advanced)
        controls_layout.addStretch()
        advanced.setVisible(False)
        advanced_toggle.toggled.connect(advanced.setVisible)
        advanced_toggle.toggled.connect(lambda checked: advanced_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow))
        buttons = QHBoxLayout()
        self.paths_button = QPushButton("Calculate flow paths")
        self.generate_button = QPushButton("Draw catchment")
        self.generate_button.setStyleSheet("font-weight: bold; padding: 6px;")
        self.paths_button.clicked.connect(lambda: self.compute(False))
        self.generate_button.clicked.connect(lambda: self.compute(True))
        buttons.addWidget(self.paths_button)
        buttons.addWidget(self.generate_button)
        layout.addLayout(buttons)
        self.manual_button = QPushButton('Start new drawing for selected point')
        self.manual_button.clicked.connect(self.owner.start_perpendicular)
        layout.addWidget(self.manual_button)
        self.resume_manual_button=QPushButton('Resume drawing')
        self.resume_manual_button.clicked.connect(self.owner.resume_manual)
        self.resume_manual_button.setEnabled(False)
        self.resume_manual_button.setToolTip('Continue the unfinished drawing with its original point(s), arc endpoints and paths. The newly selected point is used only by Start new drawing.')
        layout.addWidget(self.resume_manual_button)
        draft_row=QHBoxLayout();layout.addLayout(draft_row)
        self.open_draft_button=QPushButton('Open drawing draft…')
        self.open_draft_button.clicked.connect(self.owner.open_manual_draft);draft_row.addWidget(self.open_draft_button)
        self.discard_draft_button=QPushButton('Discard paused drawing')
        self.discard_draft_button.clicked.connect(self.owner.discard_manual);draft_row.addWidget(self.discard_draft_button)
        self.discard_draft_button.setEnabled(False)
        self.open_draft_button.setToolTip('Resume unfinished manual work from a .draft.json file, including its saved contours, settings and chosen points.')
        self.discard_draft_button.setToolTip('Explicitly discard the paused in-memory drawing so you can start a new one. Saved draft files are kept.')
        self.manual_button.setToolTip('Start a fresh manual drawing for the point(s) currently selected above. If another drawing is unfinished, save its draft or discard it first; Cancel keeps it. Arc endpoints belonging to the old drawing are reset. Resume drawing continues the old point instead.')
        self.target_button.setToolTip('Click two opposite corners around the desired groundwater high. The analysis area and path-length budget expand to include it. Both calculated endpoints must reach this area; the target does not force paths against the gradient. Right-click or Esc cancels.')
        self.interpolation.setToolTip('Harmonic interpolation follows contour constraints without creating interior highs between them. It is a geometric interpolation, not a pumping or recharge model. Cubic retains the previous method for comparison.')
        edits = QHBoxLayout()
        self.edit_button = QPushButton("Edit catchment")
        self.edit_button.clicked.connect(self.edit)
        self.export_button = QPushButton("Export catchment…")
        self.export_button.clicked.connect(self.export)
        self.cancel_button = QPushButton("Cancel calculation")
        self.cancel_button.clicked.connect(lambda: self.task.cancel() if self.task else None)
        self.cancel_button.setEnabled(False)
        for button in (self.edit_button, self.export_button, self.cancel_button):
            edits.addWidget(button)
        layout.addLayout(edits)
        self.log = QTextBrowser()
        self.log.setMaximumHeight(125)
        self.log.setPlainText("Select wells on the QGIS map, choose contours and their head field, then Draw catchment.\nOutputs are temporary until exported. GeoPackage preserves originals and flow paths.")
        layout.addWidget(self.log)
        self.add_hover_help(form, adv, example_button, advanced_toggle)
        self._summary_layer = None
        self.wells.layerChanged.connect(self.watch_well_selection)
        self.selection.currentIndexChanged.connect(self.update_well_summary)
        self.watch_well_selection(self.wells.currentLayer())
        self.auto_select(initial=True)
        screen = self.screen() or QGuiApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen else 800
        self.resize(560, min(730, max(350, available_height - 60)))

    def add_hover_help(self, form, advanced, example_button, advanced_toggle):
        tips = [
            (self.pick_button, "Hide this dialog and click one point on the map. Existing point features are identified from visible layers; overlapping points offer a choice. Clicking elsewhere uses that exact map location as one temporary target. Right-click or Esc cancels. Then choose contours and click Draw catchment."),
            (self.auto_crs, "Use a projected metre CRS from your contours or point layer. For geographic input choose the local UTM zone at the target (polar CRS near the poles). Clear this to set your own calculation CRS. No location in Brandenburg is hard-coded."),
            (self.point_choice, "Choose any individual point from the point layers loaded in this QGIS project, including monitoring stations. Type part of its name or layer name to search. Each entry includes its source layer and feature ID. One point mode uses exactly this point, regardless of layer selections. Multipart features list their point components separately. Right-clicking a point on the map also fills this field."),
            (self.contours, "Choose the ORIGINAL georeferenced groundwater contour LINE layer, for example Groundwater contours | spring 2015. Generated flow paths and contour closures are excluded. Each source line must carry a numeric hydraulic-head value. A WMS image alone cannot be used; use contours for one date and the relevant aquifer."),
            (self.head, "Choose the numeric field containing groundwater HEAD in metres, for example ZLEVEL, head or elevation. fid is a feature identifier, not a groundwater level. Recognized head fields are suggested; otherwise choose your field explicitly. Use groundwater elevation, not depth below ground. All values must share one vertical datum. The displayed name may be a QGIS alias."),
            (self.wells, "Choose a POINT layer for Selected wells or All wells mode. Choosing an individual point from the Point dropdown sets this layer automatically. A one-point run uses only that feature/component. For a well-group run, select wells from one local waterworks."),
            (self.ids, "Choose the field used to identify wells in output and export settings, for example well_id or well_name. This field does not change the flow calculation. well_id is selected automatically when available."),
            (self.selection, "One point uses the individual point chosen below, regardless of layer selections. Selected wells uses highlighted features in the chosen well layer. All wells uses every feature in that layer and creates one shared polygon. Use separate runs for distant waterworks. The count below shows what will be used."),
            (self.well_summary, "This count updates when you change the well layer, its selection, or the Use option. Select features using the QGIS map selection tool or the attribute table. Selected wells requires at least one selected feature."),
            (self.width_mode, "Automatic geometric proposal uses local contour spacing and point distribution. If construction fails, progressively smaller supported envelopes are tried; the final width and any reduction are reported. Manual width uses the exact Width value below without automatic narrowing. Both are geometric assumptions, not pumping-based capture widths."),
            (self.width, "Total distance in metres between the two initial lateral boundary starts. Used only with Manual width. For a group it must accommodate all selected wells across the local flow direction. In automatic mode this field displays the suggested or last calculated width."),
            (self.suggest_button, "Calculate a geometric starting-width suggestion and add two adjustable starting points to QGIS. This does not generate a catchment. To move them, edit the new point layer and enable Use adjusted start-point layer before recalculating."),
            (self.use_starts, "Enable this after moving the two boundary-start points with normal QGIS editing tools. The selected Start-point layer then replaces automatic/manual starting positions. Exactly two distinct, single-point features are required."),
            (self.starts, "Choose the point layer containing the two adjusted boundary starts. It is used only when the checkbox above is enabled. Create a suitable layer with Suggest width / create start points."),
            (self.stop_mode, "Highest connected contour traces both sides upstream, then closes along the actual highest connected contour reached by both paths that produces a supported polygon. It preserves contour bends instead of drawing a straight shortcut. This is an artificial cap, not a verified divide or necessarily the highest contour anywhere on the map. Other options stop at the map extent or a supplied boundary."),
            (self.boundary, "Used only with Custom boundary layer. Choose a line crossing both upstream paths, or a polygon enclosing the wells and starts. If features in that layer are selected, only those are used. Leave this field unused for the other stopping options."),
            (self.crs, "Choose a projected coordinate reference system with metre units for calculation. EPSG:25833 is suitable for Brandenburg. Input layers are transformed from their assigned CRSs. This does not transform vertical head datums."),
            (self.radius, "Initial analysis radius in metres (default 5000). With automatic expansion enabled, paths approaching its edge trigger a larger window when source contours continue. This radius is not a groundwater boundary."),
            (self.sample, "Distance in metres between sampled points along each contour. Default: 30 m. Smaller spacing follows line geometry more closely but uses more memory and time. Larger spacing reduces samples; inspect the effect on curved contours. Limit: 60,000 samples."),
            (self.step, "Nominal upstream tracing step in metres. Default: 20 m. Smaller steps can better follow bends but require more calculation. The tracer reduces steps automatically at sharp turns and requires increasing head."),
            (self.gap, "Largest permitted interpolation-triangle edge in metres. Default: 4000 m. Wider unsupported gaps are masked and paths stop there. Increasing this value allows interpolation across larger gaps; it does not add measured information. This does not set starting width."),
            (self.length, "Maximum length in metres of each lateral path. Default: 10000 m. Acts as a calculation limit when another stopping condition has not been reached. Check the recorded termination reason in the output."),
            (self.style_inputs, "Apply blue line symbols to the selected contour layer and dark point symbols to the well layer after a successful calculation. Existing input-layer symbols are replaced when enabled."),
            (self.paths_button, "Calculate the two upstream paths and starting points for inspection, without generating a polygon. You can also click Draw catchment directly to calculate paths and the polygon together."),
            (self.generate_button, "Interpolate groundwater head, trace both lateral boundaries upstream, close and validate a polygon containing all wells in this run. Results are temporary until exported. No polygon is added if construction fails."),
            (self.edit_button, "Enable normal QGIS vertex editing on the most recent catchment. The hidden original calculation is retained for comparison. Export validates the edited geometry and verifies that it still contains the selected wells."),
            (self.export_button, "Save the current result. GeoPackage includes edited and original polygons, flow paths and starts. Shapefile and GeoJSON save the current polygon. A JSON sidecar preserves full settings. Export before closing QGIS; choose a new filename."),
            (self.cancel_button, "Request cancellation of the active background calculation. Available while a calculation is running."),
            (example_button, "Load an artificial planar contour map and one artificial well to practise the workflow. This changes the inputs and numerical settings to the synthetic test defaults. For your own data choose your layers; real-data defaults are 5000 / 30 / 20 / 2000 / 10000 m."),
            (advanced_toggle, "Show or hide the numerical settings and calculation CRS. Defaults are intended as a starting point; hover over each field to see units, effects and limitations."),
            (self.log, "Calculation progress, suggested width, warnings and save confirmation appear here. If a calculation fails, this box explains the cause. Inspect termination reasons and export successful results."),
        ]
        self.help_widgets = [widget for widget, _ in tips]
        for widget, tip in tips:
            text = "<p>" + tip + "</p>"
            widget.setToolTip(text)
            widget.setWhatsThis(text)
            for layout in (form, advanced):
                label = layout.labelForField(widget)
                if label:
                    label.setToolTip(text)
        for i, tip in enumerate(("Only selected features in the chosen well layer.", "Every well in the layer: use only for one local group.", "Exactly the point chosen in the Point dropdown, independently of selections.")):
            self.selection.setItemData(i, tip, Qt.ToolTipRole)
        for i, tip in enumerate(("Width estimated from contour spacing and well geometry.", "Enter your provisional total starting width in metres.")):
            self.width_mode.setItemData(i, tip, Qt.ToolTipRole)
        for i, tip in enumerate(("Artificial upstream head cap; not a verified divide.", "Artificial stopping perimeter from the current visible map.", "Use an existing line or polygon to control termination.")):
            self.stop_mode.setItemData(i, tip, Qt.ToolTipRole)

    def watch_well_selection(self, layer):
        if self._summary_layer is not None:
            try:
                self._summary_layer.selectionChanged.disconnect(self.update_well_summary)
            except (RuntimeError, TypeError):
                pass
        self._summary_layer = layer
        if layer is not None:
            layer.selectionChanged.connect(self.update_well_summary)
        self.update_well_summary()

    def update_well_summary(self, *args):
        if self.selection.currentIndex() == 2:
            target = self.point_choice.target()
            self.well_summary.setText("This run: exactly 1 chosen point; other points are excluded." if target
                                     else "Choose one point below, or right-click a point on the map.")
            return
        layer = self.wells.currentLayer()
        if not layer:
            self.well_summary.setText("Choose a well layer.")
            return
        total, selected = layer.featureCount(), layer.selectedFeatureCount()
        if self.selection.currentIndex() == 0:
            text = f"This run: {selected} selected of {total} point features."
            if not selected:
                text += " Select wells in QGIS first."
        else:
            text = f"This run: all {total} point features in one shared catchment."
        self.well_summary.setText(text)

    @staticmethod
    def layer_combo(filters, empty=False):
        combo = QgsMapLayerComboBox()
        combo.setFilters(filters)
        combo.setAllowEmptyLayer(empty)
        if empty:
            combo.setLayer(None)
        return combo

    @staticmethod
    def spin(low, high, value, suffix):
        widget = QDoubleSpinBox()
        widget.setRange(low, high)
        widget.setDecimals(1)
        widget.setValue(value)
        widget.setSuffix(suffix)
        return widget

    def auto_select(self, initial=False):
        self.contours.refresh_sources()
        self.point_choice.refresh()
        candidates = [l for l in QgsProject.instance().mapLayers().values()
                      if hasattr(l, "geometryType") and l.geometryType() == QgsWkbTypes.PointGeometry
                      and l.selectedFeatureCount() > 0 and not l.customProperty("draw_catchments/role", "")]
        if candidates and (initial or self.selection.currentIndex() != 2):
            active = self.iface.activeLayer()
            self.wells.setLayer(active if active in candidates else candidates[0])
            self.selection.setCurrentIndex(0)
        self.select_well_identifier()
        if initial or self.contours.currentLayer() is None:
            project = QgsProject.instance()
            current = self.contours.currentLayer()
            node = project.layerTreeRoot().findLayer(current) if current else None
            if not node or not node.isVisible() or not suggested_head_field(current):
                candidates = sorted(project.mapLayers().values(), key=lambda l: l.name().casefold())
                for candidate in candidates:
                    candidate_node = project.layerTreeRoot().findLayer(candidate)
                    if (hasattr(candidate, "geometryType") and candidate.geometryType() == QgsWkbTypes.LineGeometry
                            and candidate_node and candidate_node.isVisible()
                            and suggested_head_field(candidate)):
                        self.contours.setLayer(candidate)
                        break
        self.select_contour_head(self.contours.currentLayer())

    def select_contour_head(self, layer):
        blocked = self.head.blockSignals(True)
        self.head.setLayer(layer)
        field = self._head_fields.get(layer.id(), suggested_head_field(layer)) if layer else ''
        if layer and field not in layer.fields().names():
            field = suggested_head_field(layer)
        self.head.setField(field)
        self.head.blockSignals(blocked)

    def remember_head_field(self, field):
        layer = self.contours.currentLayer()
        if layer is not None and field and field in layer.fields().names():
            self._head_fields[layer.id()] = field

    def select_well_identifier(self, layer=None):
        layer = layer or self.wells.currentLayer()
        if layer and "well_id" in layer.fields().names():
            self.ids.setField("well_id")

    def point_chosen(self, *args):
        target = self.point_choice.target()
        if target:
            layer = QgsProject.instance().mapLayer(target[0])
            if layer:
                self.wells.setLayer(layer)
                self.selection.setCurrentIndex(2)
        self.update_well_summary()

    def use_point(self, layer, feature_id, part=0):
        self.point_choice.refresh()
        index = self.point_choice.findData(point_key(layer, feature_id, part))
        if index < 0:
            self.error("This point is no longer available in the project.")
            return
        self.point_choice.setCurrentIndex(index)
        self.point_chosen()
        self.selection.setCurrentIndex(2)
        self.use_starts.setChecked(False)
        self.log.setPlainText("One point selected from the map. Choose its groundwater contours and head field, then Draw catchment. Other layer points and selections are excluded.")

    def load_example(self):
        from qgis.core import QgsVectorLayer, QgsRectangle
        path = Path(__file__).parent / "examples" / "synthetic_plane.gpkg"
        contours = QgsVectorLayer(str(path) + "|layername=contours", "Synthetic head contours", "ogr")
        wells = QgsVectorLayer(str(path) + "|layername=wells", "Synthetic test well", "ogr")
        if not contours.isValid() or not wells.isValid():
            self.error("Bundled test data is missing. Reinstall the complete plugin ZIP.")
            return
        project = QgsProject.instance()
        project.addMapLayers([contours, wells])
        wells.selectAll()
        self.iface.setActiveLayer(wells)
        self.contours.setLayer(contours)
        self.head.setField("head")
        self.wells.setLayer(wells)
        self.ids.setField("well_id")
        self.selection.setCurrentIndex(0)
        self.width_mode.setCurrentIndex(0)
        self.use_starts.setChecked(False)
        self.stop_mode.setCurrentIndex(0)
        self.crs.setCrs(contours.crs())
        self.radius.setValue(2000)
        self.sample.setValue(20)
        self.step.setValue(10)
        self.gap.setValue(300)
        self.length.setValue(700)
        self.style_inputs.setChecked(True)
        extent = QgsCoordinateTransform(contours.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), project).transformBoundingBox(contours.extent())
        self.iface.mapCanvas().setExtent(extent)
        self.iface.mapCanvas().refresh()
        self.log.setPlainText("Synthetic test data loaded and one well selected. Click Draw catchment. These coordinates and heads are artificial test fixtures, not real wells.")

    def inputs(self, ignore_starts=False):
        from .core.flow import CalculationError, Settings
        from .service import prepare, transformed, local_calculation_crs
        point_target = None
        well_layer = self.wells.currentLayer()
        if self.selection.currentIndex() == 2:
            target = self.point_choice.target()
            if target is None:
                raise CalculationError("Choose one point: click Pick one point on map, or select an exact entry from the Point search results.")
            well_layer = QgsProject.instance().mapLayer(target[0])
            if well_layer is None:
                raise CalculationError("The chosen point layer was removed. Choose another point.")
            if well_layer != self.wells.currentLayer():
                self.wells.setLayer(well_layer)
            point_target = target[1:]
        if self.auto_crs.isChecked():
            self.crs.setCrs(local_calculation_crs(self.contours.currentLayer(), well_layer,
                                                point_target, self.selection.currentIndex() == 0))
        settings = Settings(sample=self.sample.value(), step=self.step.value(), radius=self.radius.value(),
                            max_gap=self.gap.value() if self.limit_gap.isChecked() else 0, max_length=self.length.value(),
                            width=self.width.value() if self.width_mode.currentIndex() else 0,
                            downstream_shape=('rounded','straight')[self.downstream_shape.currentIndex()],arc_depth=self.arc_depth.value(),
                            stop=("highest", "map", "boundary", "target")[self.stop_mode.currentIndex()],
                            interpolation=('harmonic','cubic')[self.interpolation.currentIndex()],auto_extent=self.auto_extent.isChecked())
        crs = self.crs.crs()
        boundary = None
        if settings.stop == "map":
            extent = self.iface.mapCanvas().extent()
            boundary = transformed(QgsGeometry.fromRect(extent), self.iface.mapCanvas().mapSettings().destinationCrs(), crs)
        elif settings.stop == "boundary":
            layer = self.boundary.currentLayer()
            if not layer:
                raise CalculationError("Choose a stopping boundary layer.")
            features = layer.selectedFeatures() if layer.selectedFeatureCount() else list(layer.getFeatures())
            boundary = QgsGeometry.unaryUnion([transformed(f.geometry(), layer.crs(), crs) for f in features])
        domain = QgsGeometry(boundary) if boundary and boundary.type() == QgsWkbTypes.PolygonGeometry else None
        if boundary and boundary.type() == QgsWkbTypes.PolygonGeometry:
            boundary = QgsGeometry(boundary.constGet().boundary())
        starts = None
        if self.use_starts.isChecked() and not ignore_starts:
            layer = self.starts.currentLayer()
            if not layer or layer.featureCount() != 2:
                raise CalculationError("Choose a start-point layer with exactly two points.")
            starts = []
            for f in sorted(layer.getFeatures(), key=lambda f: f.id()):
                geom = transformed(f.geometry(), layer.crs(), crs)
                if geom.isEmpty() or geom.isMultipart():
                    raise CalculationError("Start points must be two single-point features.")
                p = geom.asPoint()
                starts.append([p.x(), p.y()])
        upstream_target = transformed(self.target_geometry,self.target_crs,crs) if self.target_geometry is not None and settings.stop=='target' else None
        data = prepare(self.contours.currentLayer(), self.head.currentField(), well_layer,
                       self.selection.currentIndex() == 0, settings, crs, self.ids.currentField() or None,
                       boundary, starts, point_target=point_target, upstream_target=upstream_target)
        data["stop_domain"] = domain
        return data

    def set_target(self,geometry,crs):
        self.target_geometry=QgsGeometry(geometry);self.target_crs=crs
        self.stop_mode.setCurrentIndex(3)
        project=QgsProject.instance()
        if self.target_layer is None or project.mapLayer(self.target_layer.id()) is None:
            self.target_layer=QgsVectorLayer('Polygon','Marked upstream target | preview','memory')
            self.target_layer.setCustomProperty('draw_catchments/role','target_preview')
            feature=QgsFeature();feature.setGeometry(geometry)
            self.target_layer.dataProvider().addFeatures([feature])
            self.target_layer.renderer().setSymbol(QgsFillSymbol.createSimple({'style':'no','outline_color':'140,65,190','outline_style':'dash','outline_width':'.6'}))
            project.addMapLayer(self.target_layer)
        else:
            feature=next(self.target_layer.getFeatures())
            self.target_layer.dataProvider().changeGeometryValues({feature.id():geometry})
        self.target_layer.setCrs(crs);self.target_layer.updateExtents();self.target_layer.triggerRepaint()
        self.update_target_visibility()
        self.target_summary.setText('Target marked. Analysis extent and path budget expand automatically to include it.')
        self.log.setPlainText('Upstream target selected. Click Draw catchment. Both boundary endpoints must reach the target; otherwise no polygon is returned.')

    def update_target_visibility(self,*args):
        if self.target_layer is not None:
            node=QgsProject.instance().layerTreeRoot().findLayer(self.target_layer.id())
            if node:node.setItemVisibilityChecked(self.stop_mode.currentIndex()==3)

    def suggest(self):
        try:
            from .service import build_surface, memory_layer
            from qgis.core import QgsPointXY
            data = self.inputs(ignore_starts=True)
            surface = build_surface(data)
            points, info = surface.initial(data["wells"])
            self.width.setValue(info["width_m"])
            layer = memory_layer("Point", "Adjustable boundary starts", self.crs.crs())
            for i, p in enumerate(points):
                f = QgsFeature(layer.fields())
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*p)))
                f["label"] = f"Start {i + 1}"
                layer.dataProvider().addFeatures([f])
            layer.updateExtents()
            layer.setCustomProperty("draw_catchments/role", "adjustable_starts")
            layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "diamond", "color": "231,126,34", "size": "3"}))
            QgsProject.instance().addMapLayer(layer)
            self.starts.setLayer(layer)
            self.log.setPlainText(f"Suggested width: {info['width_m']:.1f} m. Local contour spacing: {info['contour_spacing_m']:.1f} m.\nThis is a geometric assumption. Select Manual width to change it, or edit the two start points with QGIS tools and enable Use adjusted start-point layer.")
        except Exception as exc:
            self.error(exc)

    def set_start_points(self,points,crs):
        from .service import memory_layer
        layer=memory_layer('Point','User-placed arc endpoints',crs)
        for i,p in enumerate(points):
            f=QgsFeature(layer.fields());f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(*p)))
            f['label']=f'Arc endpoint / side {i+1}';layer.dataProvider().addFeatures([f])
        layer.updateExtents();layer.setCustomProperty('draw_catchments/role','adjustable_starts')
        layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({'name':'diamond','color':'231,126,34','size':'3'}))
        QgsProject.instance().addMapLayer(layer);self.starts.setLayer(layer);self.use_starts.setChecked(True)
        self.log.setPlainText('Your two arc endpoints are set and will be used unchanged. Arc depth controls the bulge around the well. Draw catchment or start manual drawing. The final polygon must contain the well.')

    def compute(self, polygon=True):
        try:
            data = self.inputs()
            self.set_busy(True)
            self.log.setPlainText("Interpolating the head surface and tracing upstream…")
            self.task = CalculationTask(data, polygon, self.completed)
            QgsApplication.taskManager().addTask(self.task)
        except Exception as exc:
            self.set_busy(False)
            self.error(exc)

    def set_busy(self, busy):
        self.controls_scroll.setEnabled(not busy)
        self.example_button.setEnabled(not busy)
        self.pick_button.setEnabled(not busy)
        for widget in (self.paths_button, self.generate_button, self.suggest_button, self.edit_button, self.export_button, self.manual_button,self.open_draft_button):
            widget.setEnabled(not busy)
        self.resume_manual_button.setEnabled(not busy and self.owner.manual_session is not None)
        self.discard_draft_button.setEnabled(not busy and self.owner.manual_session is not None)
        self.cancel_button.setEnabled(busy)

    def completed(self, result, error):
        self.task = None
        self.set_busy(False)
        if result is None:
            self.error(error)
            return
        from .service import result_layers
        self.layers = result_layers(result)
        project = QgsProject.instance()
        if self.target_layer is not None and 'target' in self.layers:
            project.removeMapLayer(self.target_layer.id());self.target_layer=None
        group = project.layerTreeRoot().insertGroup(0, "Draw Catchments · " + result["metadata"]["created_utc"][:19])
        for key, layer in self.layers.items():
            if key == "paths":
                symbol = QgsLineSymbol.createSimple({"line_color": "30,150,88", "line_width": "0.6"})
            elif key in ("cap","base"):
                symbol = QgsLineSymbol.createSimple({"line_color": "226,116,15", "line_width": "0.8"})
            elif key == 'target':
                symbol = QgsFillSymbol.createSimple({'style':'no','outline_color':'140,65,190','outline_style':'dash','outline_width':'.5'})
            elif key == "starts":
                symbol = QgsMarkerSymbol.createSimple({"name": "diamond", "color": "240,130,25", "size": "3"})
            else:
                symbol = QgsFillSymbol.createSimple({"color": "244,149,39,50", "outline_color": "226,116,15", "outline_width": "0.7"})
            layer.renderer().setSymbol(symbol)
            project.addMapLayer(layer, False)
            node = group.addLayer(layer)
            if key == "original":
                node.setItemVisibilityChecked(False)
        if self.style_inputs.isChecked():
            self.contours.currentLayer().renderer().setSymbol(QgsLineSymbol.createSimple({"line_color": "47,117,190", "line_width": "0.3"}))
            self.wells.currentLayer().renderer().setSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "color": "25,42,56", "outline_color": "white", "size": "3"}))
            self.contours.currentLayer().triggerRepaint()
            self.wells.currentLayer().triggerRepaint()
        target = self.layers.get("edited", self.layers["paths"])
        self.iface.setActiveLayer(target)
        extent = QgsCoordinateTransform(target.crs(), self.iface.mapCanvas().mapSettings().destinationCrs(), project).transformBoundingBox(target.extent())
        extent.scale(1.15)
        self.iface.mapCanvas().setExtent(extent)
        self.iface.mapCanvas().refresh()
        meta = result["metadata"]
        self.width.setValue(meta["initialization"]["width_m"])
        self.log.setPlainText("Completed. Temporary layers added to the map. Export to save permanently.\n" + "\n".join(meta["warnings"]))

    def edit(self):
        try:
            layer = self.current_result()
            self.iface.setActiveLayer(layer)
            layer.startEditing()
            self.iface.actionVertexTool().trigger()
            self.log.setPlainText("Edit vertices with QGIS. The hidden original layer remains unchanged. Toggle it to compare. Export saves the current geometry and the original in GeoPackage.")
        except Exception as exc:
            self.error(exc)

    def current_result(self):
        layer = self.layers.get("edited")
        if not layer or layer.id() not in QgsProject.instance().mapLayers():
            from .core.flow import CalculationError
            raise CalculationError("No finished polygon yet. Resume manual drawing and click Finish polygon or Finish & export. "
                                   "Use Save drawing draft to preserve unfinished paths.")
        return layer

    def export(self):
        try:
            if self.owner.manual_session is not None:
                self.owner.resume_manual()
                if self.owner.extra_tool is not None and hasattr(self.owner.extra_tool,'finish_polygon'):
                    self.owner.extra_tool.finish_polygon(True)
                return
            from .service import export_result
            layer = self.current_result()
            path, selected = QFileDialog.getSaveFileName(self, "Export preliminary catchment", "catchment.gpkg",
                "GeoPackage (*.gpkg);;ESRI Shapefile (*.shp);;GeoJSON (*.geojson)")
            if not path:
                return
            if not Path(path).suffix:
                path += ".gpkg" if "GeoPackage" in selected else ".shp" if "Shapefile" in selected else ".geojson"
            export_result(path, layer, self.layers)
            self.log.setPlainText(f"Saved: {path}\nFull calculation settings are also in the .drawcatchments.json sidecar. GeoPackage includes original boundaries and flow paths.")
        except Exception as exc:
            self.error(exc)

    def error(self, error):
        self.log.setPlainText(str(error))
        self.iface.messageBar().pushWarning("Draw Catchments", str(error))

    def closeEvent(self, event):
        if self.task:
            self.task.cancel()
        super().closeEvent(event)


class DrawCatchments:
    def __init__(self, iface):
        self.iface, self.dialog, self.action = iface, None, None
        self.picker, self.previous_map_tool, self.picked_layer = None, None, None
        self.extra_tool, self.extra_previous = None, None
        self.manual_session=None

    def initGui(self):
        self.action = QAction(QIcon(str(Path(__file__).with_name("icon.svg"))), "Draw Catchments", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu("Draw Catchments", self.action)
        self.iface.addToolBarIcon(self.action)
        self.identifier = QgsMapToolIdentify(self.iface.mapCanvas())
        self.iface.mapCanvas().contextMenuAboutToShow.connect(self.map_context_menu)

    def map_context_menu(self, menu, event):
        try:
            hits = points_at_click(self.iface.mapCanvas(), self.identifier, event)
            if not hits:
                point = QgsPointXY(event.mapPoint())
                crs = self.iface.mapCanvas().mapSettings().destinationCrs()
                action = menu.addAction("Draw Catchments at this location…")
                action.triggered.connect(lambda checked=False, point=point, crs=crs: self.run_at_location(point, crs))
                return
            menu.addSeparator()
            target_menu = menu if len(hits) == 1 else menu.addMenu("Draw Catchments for one point")
            for _, layer, feature_id, part, label in hits:
                text = "Draw Catchments for this point…" if len(hits) == 1 else label.replace('&', '&&')
                action = target_menu.addAction(text)
                action.setToolTip(label)
                action.triggered.connect(lambda checked=False, layer=layer, fid=feature_id, part=part: self.run_for_point(layer, fid, part))
        except Exception as exc:
            QgsMessageLog.logMessage(str(exc), "Draw Catchments", Qgis.Warning)

    def start_picking(self):
        if self.dialog and self.dialog.task:
            return
        canvas = self.iface.mapCanvas()
        self.previous_map_tool = canvas.mapTool()
        if self.picker is None:
            self.picker = SinglePointMapTool(self)
        if self.dialog:
            self.dialog.hide()
        canvas.setMapTool(self.picker)

    def start_target_picking(self):
        if self.dialog and self.dialog.task:return
        from .maptools import TargetAreaTool
        self.begin_extra_tool(TargetAreaTool(self))
        self.iface.messageBar().pushWarning('Upstream target','Click two opposite corners around the groundwater high. Right-click or Esc cancels.')

    def start_endpoint_picking(self):
        if self.dialog and self.dialog.task:return
        try:
            if self.manual_session is not None:
                self.resume_manual()
                if self.extra_tool:self.extra_tool.place_starts()
                return
            from .maptools import StartPointsTool
            data=self.dialog.inputs(ignore_starts=True)
            self.begin_extra_tool(StartPointsTool(self,data))
            self.iface.mapCanvas().setToolTip('Click the first arc endpoint beside the well, then the second. Esc cancels.')
            self.iface.messageBar().pushWarning('Arc endpoints','Click the first endpoint beside the well, then the second. The orange arc previews the closure; its depth is adjustable.')
        except Exception as exc:self.dialog.error(exc)

    def resume_manual(self):
        if self.dialog and self.dialog.task:return
        if self.manual_session is None:return
        try:
            from .maptools import PerpendicularTool
            self.begin_extra_tool(PerpendicularTool(self,self.manual_session))
        except Exception as exc:self.dialog.error(exc)

    def old_start_points_selected(self,session):
        """Avoid carrying one site's arc endpoints into a new site's drawing."""
        if session is None or not self.dialog.use_starts.isChecked():return False
        layer=self.dialog.starts.currentLayer()
        if layer is None:return False
        if layer.featureCount()!=2:return False
        import numpy as np
        from .service import transformed
        crs=QgsCoordinateReferenceSystem.fromWkt(session.data['crs_wkt'])
        points=[]
        for f in layer.getFeatures():
            g=transformed(f.geometry(),layer.crs(),crs)
            if g.isEmpty() or g.isMultipart():return False
            p=g.asPoint();points.append([p.x(),p.y()])
        return np.allclose(sorted(points),sorted(session.starts.tolist()),rtol=0,atol=1e-6)

    def choose_previous_drawing_action(self):
        box=QMessageBox(self.dialog)
        box.setWindowTitle('Start a new drawing')
        box.setText('An unfinished drawing already exists. Save it as a draft or discard it before starting at the selected point.')
        box.setInformativeText('Cancel keeps the old drawing available under Resume drawing. Saved files are never deleted.')
        save=box.addButton('Save draft & start new',QMessageBox.AcceptRole)
        discard=box.addButton('Discard & start new',QMessageBox.DestructiveRole)
        cancel=box.addButton(QMessageBox.Cancel);box.setDefaultButton(cancel);box.setEscapeButton(cancel)
        box.exec_()
        return 'save' if box.clickedButton()==save else 'discard' if box.clickedButton()==discard else 'cancel'

    def save_previous_drawing(self,session):
        path,_=QFileDialog.getSaveFileName(self.dialog,'Save previous drawing before starting new','catchment.draft.json','Drawing draft (*.draft.json)')
        if not path:return False
        if not path.lower().endswith('.json'):path+='.draft.json'
        from .draft import save_draft
        save_draft(path,session)
        return True

    def start_perpendicular(self):
        if self.dialog and self.dialog.task:return
        try:
            from .manual import ManualSession
            from .maptools import PerpendicularTool
            from .service import transformed
            from math import isfinite
            previous=self.manual_session
            reset_starts=self.old_start_points_selected(previous)
            # Validate/build first: invalid new inputs must never destroy the
            # old drawing, even if the user intends to discard it afterwards.
            data=self.dialog.inputs(ignore_starts=reset_starts)
            session=ManualSession(data)
            # Once starts are initialized, manual clicks may use any contour
            # in the selected layer, even beyond the initial analysis radius.
            source=self.dialog.contours.currentLayer();field=self.dialog.head.currentField()
            lines=[]
            for feature in source.getFeatures():
                head=float(feature[field])
                if not isfinite(head):raise ValueError(f'Contour {feature.id()}: invalid hydraulic head.')
                geom=transformed(feature.geometry(),source.crs(),self.dialog.crs.crs())
                if geom.isEmpty() or not geom.isGeosValid():raise ValueError(f'Contour {feature.id()}: invalid geometry.')
                for part in geom.asGeometryCollection() if geom.isMultipart() else [geom]:
                    if part.type()==QgsWkbTypes.LineGeometry and part.length()>0:lines.append((QgsGeometry(part),head))
            session.data['lines']=lines
            if previous is not None:
                action=self.choose_previous_drawing_action()
                if action=='cancel':return
                if action=='save' and not self.save_previous_drawing(previous):return
            # Dispose the OLD map tool before assigning the new session. Its
            # cleanup must not overwrite the new session with the old one.
            self.finish_extra_tool(False,preserve=False)
            if reset_starts:self.dialog.use_starts.setChecked(False)
            self.manual_session=session
            self.begin_extra_tool(PerpendicularTool(self,session))
        except Exception as exc:self.dialog.error(exc)

    def open_manual_draft(self):
        if self.dialog.task:return
        if self.manual_session is not None:
            self.dialog.error('A drawing is paused. Resume and save it, or click Discard paused drawing before opening another draft.');return
        path,_=QFileDialog.getOpenFileName(self.dialog,'Open unfinished drawing','','Drawing draft (*.draft.json);;JSON (*.json)')
        if not path:return
        try:
            from .draft import load_draft
            from .maptools import PerpendicularTool
            session=load_draft(path);self.manual_session=session
            # Saved contours stay in the snapshot; display them when their
            # original project layers are unavailable so clicks remain usable.
            project=QgsProject.instance()
            source=session.data['source_names'].get('contours','')
            if not any(layer.name()==source for layer in project.mapLayers().values()):
                layer=QgsVectorLayer('LineString','Draft source contours','memory')
                layer.setCrs(QgsCoordinateReferenceSystem.fromWkt(session.data['crs_wkt']))
                layer.dataProvider().addAttributes([QgsField('head',QVariant.Double)]);layer.updateFields()
                features=[]
                for geometry,head in session.data['lines']:
                    f=QgsFeature(layer.fields());f.setGeometry(geometry);f['head']=head;features.append(f)
                layer.dataProvider().addFeatures(features);layer.updateExtents()
                layer.renderer().setSymbol(QgsLineSymbol.createSimple({'line_color':'47,117,190','line_width':'.3'}))
                project.addMapLayer(layer)
            self.begin_extra_tool(PerpendicularTool(self,session))
        except Exception as exc:self.dialog.error(exc)

    def discard_manual(self):
        if self.old_start_points_selected(self.manual_session):self.dialog.use_starts.setChecked(False)
        self.finish_extra_tool(False,preserve=False)
        self.manual_session=None
        self.dialog.resume_manual_button.setEnabled(False)
        self.dialog.discard_draft_button.setEnabled(False)
        self.dialog.log.setPlainText('Paused drawing discarded. Saved draft files are unchanged. Choose inputs to start a new drawing.')

    def begin_extra_tool(self,tool):
        self.finish_extra_tool(False)
        self.extra_previous=self.iface.mapCanvas().mapTool();self.extra_tool=tool
        self.dialog.hide();self.iface.mapCanvas().setMapTool(tool)

    def finish_extra_tool(self,show_dialog=True,preserve=True):
        canvas=self.iface.mapCanvas()
        tool=self.extra_tool
        if tool is not None:
            if hasattr(tool,'session'):
                self.manual_session=tool.session if preserve else None
            if canvas.mapTool()==tool:
                canvas.unsetMapTool(tool)
                if self.extra_previous is not None:
                    try:canvas.setMapTool(self.extra_previous)
                    except RuntimeError:pass
            tool.dispose();tool.deleteLater()
        self.extra_tool,self.extra_previous=None,None
        if self.dialog:
            self.dialog.resume_manual_button.setEnabled(self.manual_session is not None and not self.dialog.task)
            self.dialog.discard_draft_button.setEnabled(self.manual_session is not None)
            if show_dialog and self.manual_session:
                self.dialog.log.setPlainText('Drawing paused. Resume drawing continues the old point. Start new drawing for selected point creates a fresh drawing and lets you save or discard the old one.')
        if show_dialog and self.dialog:self.dialog.show();self.dialog.raise_()

    def finish_picking(self, show_dialog=True):
        canvas = self.iface.mapCanvas()
        if self.picker is not None and canvas.mapTool() == self.picker:
            canvas.unsetMapTool(self.picker)
            if self.previous_map_tool is not None:
                try:
                    canvas.setMapTool(self.previous_map_tool)
                except RuntimeError:
                    pass
        self.previous_map_tool = None
        if show_dialog and self.dialog:
            self.dialog.show()
            self.dialog.raise_()

    def pick_at_event(self, event):
        canvas = self.iface.mapCanvas()
        hits = points_at_click(canvas, self.identifier, event)
        if len(hits) > 1:
            menu = QMenu(canvas)
            for _, layer, fid, part, label in hits:
                action = menu.addAction(label.replace('&', '&&'))
                action.triggered.connect(lambda checked=False, layer=layer, fid=fid, part=part: self.run_for_point(layer, fid, part))
            menu.exec_(canvas.mapToGlobal(event.pos()))
            if canvas.mapTool() == self.picker:
                self.finish_picking()
        elif hits:
            _, layer, fid, part, _ = hits[0]
            self.run_for_point(layer, fid, part)
        else:
            self.run_at_location(QgsPointXY(event.mapPoint()), canvas.mapSettings().destinationCrs())

    def run_at_location(self, point, crs):
        if self.dialog and self.dialog.task:
            self.iface.messageBar().pushWarning("Draw Catchments", "Wait for the current calculation or cancel it before choosing another point.")
            return
        project = QgsProject.instance()
        layer = self.picked_layer
        try:
            if layer is not None and project.mapLayer(layer.id()) is None:
                layer = None
        except RuntimeError:
            layer = None
        if layer is None:
            layer = QgsVectorLayer('Point', 'Picked map location', 'memory')
            layer.setCrs(crs)
            layer.dataProvider().addAttributes([QgsField('name', QVariant.String)])
            layer.updateFields()
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(point))
            feature['name'] = 'User-picked location'
            layer.dataProvider().addFeatures([feature])
            layer.setCustomProperty('draw_catchments/role', 'picked_location')
            layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({'name':'cross', 'color':'210,50,30', 'size':'5'}))
            project.addMapLayer(layer)
            self.picked_layer = layer
        else:
            transformed_point = QgsCoordinateTransform(crs, layer.crs(), project).transform(point)
            feature = next(layer.getFeatures())
            layer.dataProvider().changeGeometryValues({feature.id(): QgsGeometry.fromPointXY(transformed_point)})
        layer.updateExtents()
        layer.triggerRepaint()
        self.run_for_point(layer, next(layer.getFeatures()).id())

    def run_for_point(self, layer, feature_id, part=0):
        if self.dialog and self.dialog.task:
            self.iface.messageBar().pushWarning("Draw Catchments", "Wait for the current calculation or cancel it before choosing another point.")
            return
        self.finish_picking(False)
        self.run()
        if self.dialog:
            self.dialog.use_point(layer, feature_id, part)

    def unload(self):
        self.finish_extra_tool(False)
        self.manual_session=None
        self.finish_picking(False)
        if self.picker is not None:
            self.picker.deleteLater()
            self.picker = None
        try:
            self.iface.mapCanvas().contextMenuAboutToShow.disconnect(self.map_context_menu)
        except (RuntimeError, TypeError):
            pass
        if getattr(self, 'identifier', None):
            self.identifier.deleteLater()
            self.identifier = None
        if self.dialog:
            self.dialog.contours.disconnect_project()
            if self.dialog.task:
                self.dialog.task.callback = None
                self.dialog.task.cancel()
            self.dialog.watch_well_selection(None)
            if self.dialog.target_layer is not None:
                QgsProject.instance().removeMapLayer(self.dialog.target_layer.id())
                self.dialog.target_layer=None
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        if self.action:
            self.iface.removePluginVectorMenu("Draw Catchments", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()

    def run(self):
        try:
            import numpy  # noqa: F401
            import scipy  # noqa: F401
        except ImportError:
            QMessageBox.warning(self.iface.mainWindow(), "Draw Catchments", "NumPy and SciPy are required in QGIS Python. See the bundled README for installation instructions.")
            return
        if self.dialog is None:
            self.dialog = CatchmentDialog(self)
        else:
            self.dialog.auto_select()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
