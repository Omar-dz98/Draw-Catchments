"""Individual project-point picker and map-canvas point identification."""
from math import hypot

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QComboBox, QCompleter
from qgis.core import QgsCoordinateTransform, QgsProject, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsMapToolIdentify


class SinglePointMapTool(QgsMapTool):
    """Explicit one-click picker, independent of QGIS layer selection mode."""
    def __init__(self, owner):
        super().__init__(owner.iface.mapCanvas())
        self.owner = owner
        self.setCursor(Qt.CrossCursor)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.owner.pick_at_event(event)
        elif event.button() == Qt.RightButton:
            self.owner.finish_picking()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.owner.finish_picking()
        else:
            super().keyPressEvent(event)


def point_layers():
    return sorted((layer for layer in QgsProject.instance().mapLayers().values()
                   if hasattr(layer, 'geometryType') and layer.isValid()
                   and layer.geometryType() == QgsWkbTypes.PointGeometry), key=lambda layer: layer.name().casefold())


def point_label(layer, feature, part=0):
    fields = {name.casefold(): name for name in layer.fields().names()}
    parts = []
    for choices in [('site',), ('well_id', 'brunnen', 'bezeichn', 'bezeichnung', 'name', 'label', 'mkz', 'id')]:
        for candidate in choices:
            if candidate in fields:
                value = str(feature[fields[candidate]])
                if value and value != 'NULL':
                    parts.append(value)
                    break
    suffix = f' / part {part + 1}' if feature.geometry().isMultipart() else ''
    return f"{layer.name()} / {' - '.join(parts) or 'Point'} [FID {feature.id()}]{suffix}"


def point_key(layer, feature_id, part):
    return f'{layer.id()}:{int(feature_id)}:{int(part)}'


class ProjectPointCombo(QComboBox):
    def __init__(self):
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMinimumContentsLength(25)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMaxVisibleItems(16)
        self.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.completer().setFilterMode(Qt.MatchContains)
        self.completer().setCaseSensitivity(Qt.CaseInsensitive)

    def refresh(self):
        previous = self.currentData()
        self.blockSignals(True)
        self.clear()
        self.addItem('Choose a point (type a name or layer to search)…', None)
        for layer in point_layers():
            for feature in layer.getFeatures():
                geometry = feature.geometry()
                if geometry.isEmpty():
                    continue
                points = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
                for part, _ in enumerate(points):
                    text = point_label(layer, feature, part)
                    self.addItem(text, point_key(layer, feature.id(), part))
                    self.setItemData(self.count() - 1, text, Qt.ToolTipRole)
        index = self.findData(previous) if previous else 0
        self.setCurrentIndex(max(0, index))
        self.blockSignals(False)

    def showPopup(self):
        self.refresh()
        super().showPopup()

    def target(self):
        # QCompleter may update edit text before currentIndexChanged. Resolve
        # the displayed exact label so mouse/keyboard completion behaves alike.
        index = self.findText(self.currentText(), Qt.MatchExactly)
        key = self.itemData(index) if index >= 0 else None
        if not key:
            return None
        layer_id, feature_id, part = key.rsplit(':', 2)
        return layer_id, int(feature_id), int(part)


def points_at_click(canvas, identifier, event):
    """Use QGIS identify tolerance; distinguish overlapping and multipart points."""
    layers = [layer for layer in canvas.layers() if layer in point_layers()]
    if not layers:
        return []
    results = identifier.identify(event.pos().x(), event.pos().y(), layers, QgsMapToolIdentify.TopDownAll)
    hits = []
    seen = set()
    destination = canvas.mapSettings().destinationCrs()
    for result in results:
        layer, feature = result.mLayer, result.mFeature
        geometry = feature.geometry()
        points = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
        transform = QgsCoordinateTransform(layer.crs(), destination, QgsProject.instance())
        # QGIS has already identified the feature. For multipart geometries,
        # expose only the closest component(s), never the whole multipoint.
        parts = []
        for part, point in enumerate(points):
            pixel = canvas.getCoordinateTransform().transform(transform.transform(point))
            distance = hypot(pixel.x() - event.pos().x(), pixel.y() - event.pos().y())
            parts.append((distance, part))
        if not parts:
            continue
        closest = min(distance for distance, _ in parts)
        for distance, part in parts:
            if distance > max(closest + 0.5, 6.0):
                continue
            key = point_key(layer, feature.id(), part)
            if key not in seen:
                hits.append((distance, layer, feature.id(), part, point_label(layer, feature, part)))
                seen.add(key)
    return sorted(hits, key=lambda item: (item[0], item[4]))
