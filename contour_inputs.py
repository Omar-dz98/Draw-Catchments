"""Distinguish source head contours from this plugin's generated boundaries.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from qgis.core import QgsMapLayerProxyModel, QgsProject
from qgis.gui import QgsMapLayerComboBox


def is_result_layer(layer):
    if layer is None:
        return False
    if layer.customProperty('draw_catchments/role', ''):
        return True
    # GeoPackage/Shapefile reimports may lose QGIS custom properties. All
    # seven fields form our output signature; names of layers are irrelevant.
    return (hasattr(layer, 'fields') and
            {'run_id', 'label', 'well_ids', 'width_m', 'created', 'status', 'stop'}
            .issubset(set(layer.fields().names())))


def suggested_head_field(layer):
    if layer is None or is_result_layer(layer):
        return ''
    names = {f.name().casefold(): f.name() for f in layer.fields() if f.isNumeric()}
    for name in ('zlevel', 'head', 'hydraulic_head', 'head_m', 'elevation', 'hoehe', 'höhe'):
        if name in names:
            return names[name]
    # Arbitrary numeric field names remain selectable, but an ID must never
    # become a hydraulic head merely because it is the first numeric field.
    return ''


class ContourLayerCombo(QgsMapLayerComboBox):
    def __init__(self):
        super().__init__()
        self.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.setAllowEmptyLayer(True)
        project = QgsProject.instance()
        project.layersAdded.connect(self.refresh_sources)
        project.layersRemoved.connect(self.refresh_sources)
        self.refresh_sources()

    def refresh_sources(self, *args):
        current = self.currentLayer()
        blocked = self.blockSignals(True)
        self.setExceptedLayerList([layer for layer in QgsProject.instance().mapLayers().values()
                                  if is_result_layer(layer)])
        if current is not None and not is_result_layer(current):
            self.setLayer(current)
        chosen = self.currentLayer()
        self.blockSignals(blocked)
        if chosen != current:
            self.layerChanged.emit(chosen)

    def showPopup(self):
        self.refresh_sources()
        super().showPopup()

    def disconnect_project(self):
        project = QgsProject.instance()
        project.layersAdded.disconnect(self.refresh_sources)
        project.layersRemoved.disconnect(self.refresh_sources)
