# SPDX-License-Identifier: GPL-3.0-or-later
"""Map interaction for an upstream rectangle and manual perpendicular paths."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QFileDialog,QComboBox,QDoubleSpinBox
from qgis.core import QgsGeometry,QgsPointXY,QgsRectangle,QgsWkbTypes,QgsCoordinateTransform,QgsProject
from qgis.gui import QgsMapTool,QgsRubberBand,QgsVertexMarker
from .core.flow import CalculationError


class StartPointsTool(QgsMapTool):
    """Two user-placed endpoints; live geometric arc, never automatic relocation."""
    def __init__(self,owner,data):
        super().__init__(owner.iface.mapCanvas());self.owner=owner;self.data=data;self.first=None
        self.setCursor(Qt.CrossCursor)
        self.band=QgsRubberBand(self.canvas(),QgsWkbTypes.LineGeometry)
        self.band.setColor(QColor('orange'));self.band.setWidth(3)
        self.marker=QgsVertexMarker(self.canvas());self.marker.setColor(QColor('orange'));self.marker.hide()
        from qgis.core import QgsCoordinateReferenceSystem
        self.transform=QgsCoordinateTransform(QgsCoordinateReferenceSystem.fromWkt(data['crs_wkt']),
                          self.canvas().mapSettings().destinationCrs(),QgsProject.instance())

    def point(self,event):
        p=self.transform.transform(QgsPointXY(event.mapPoint()),QgsCoordinateTransform.ReverseTransform)
        return [p.x(),p.y()]

    def canvasMoveEvent(self,event):
        if self.first is None:return
        from .downstream import downstream_arc
        from .service import geometry
        try:
            arc=geometry(downstream_arc([self.first,self.point(event)],self.data['wells'],self.data['settings']))
            arc.transform(self.transform);self.band.setToGeometry(arc,None)
        except CalculationError:pass

    def canvasReleaseEvent(self,event):
        if event.button()==Qt.RightButton:self.owner.finish_extra_tool();return
        if event.button()!=Qt.LeftButton:return
        point=self.point(event)
        if self.first is None:
            self.first=point;self.marker.setCenter(QgsPointXY(event.mapPoint()));self.marker.show()
            self.canvas().setToolTip('Click the second arc endpoint. Orange previews the closure around the well. Esc cancels.');return
        import numpy as np
        if np.linalg.norm(np.asarray(point)-self.first)<.1:return
        from qgis.core import QgsCoordinateReferenceSystem
        self.owner.dialog.set_start_points([self.first,point],QgsCoordinateReferenceSystem.fromWkt(self.data['crs_wkt']))
        self.owner.finish_extra_tool()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape:self.owner.finish_extra_tool()

    def deactivate(self):
        self.band.reset(QgsWkbTypes.LineGeometry);self.marker.hide();self.canvas().setToolTip('');super().deactivate()

    def dispose(self):
        self.canvas().scene().removeItem(self.band);self.canvas().scene().removeItem(self.marker)


class TargetAreaTool(QgsMapTool):
    def __init__(self,owner):
        super().__init__(owner.iface.mapCanvas());self.owner=owner;self.first=None
        self.setCursor(Qt.CrossCursor)
        self.band=QgsRubberBand(self.canvas(),QgsWkbTypes.PolygonGeometry)
        self.band.setColor(QColor(135,65,195,70));self.band.setWidth(2)

    def canvasReleaseEvent(self,event):
        if event.button()==Qt.RightButton:self.owner.finish_extra_tool();return
        if event.button()!=Qt.LeftButton:return
        if self.first is None:self.first=QgsPointXY(event.mapPoint());return
        rect=QgsRectangle(self.first,event.mapPoint())
        if rect.width()<=0 or rect.height()<=0:return
        self.owner.dialog.set_target(QgsGeometry.fromRect(rect),self.canvas().mapSettings().destinationCrs())
        self.owner.finish_extra_tool()

    def canvasMoveEvent(self,event):
        if self.first is not None:self.band.setToGeometry(QgsGeometry.fromRect(QgsRectangle(self.first,event.mapPoint())),None)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape:self.owner.finish_extra_tool()

    def deactivate(self):
        self.band.reset(QgsWkbTypes.PolygonGeometry);super().deactivate()

    def dispose(self):self.canvas().scene().removeItem(self.band)


class DrawingPanel(QDialog):
    def __init__(self,tool):
        super().__init__(tool.owner.iface.mainWindow(),Qt.Tool)
        self.tool=tool;self.setWindowTitle('Manual drawing — Draw Catchments')
        self.setMinimumWidth(430);layout=QVBoxLayout(self)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.status.setMinimumHeight(65)
        row=QHBoxLayout();layout.addLayout(row);row.addWidget(QLabel('New connections:'))
        self.angle_mode=QComboBox();self.angle_mode.addItems(['Closest feasible to 90°','Exact 90° only'])
        self.angle_mode.setCurrentIndex(0 if tool.session.data['settings'].manual_angle_mode=='best' else 1)
        self.angle_mode.setToolTip('Prefer an exact normal. If impossible, use the best clear straight candidate found and show its actual angle. No curved side segments. This setting affects new steps.')
        row.addWidget(self.angle_mode);self.angle_mode.currentIndexChanged.connect(tool.change_angle)
        row=QHBoxLayout();layout.addLayout(row)
        row.addWidget(QLabel('Closure at well only:'))
        self.shape=QComboBox();self.shape.addItems(['Rounded arc','Straight'])
        self.shape.setCurrentIndex(0 if tool.session.data['settings'].downstream_shape=='rounded' else 1);row.addWidget(self.shape)
        self.depth=QDoubleSpinBox();self.depth.setRange(0,1000000);self.depth.setSuffix(' m');self.depth.setSpecialValueText('Auto depth')
        self.depth.setValue(tool.session.data['settings'].arc_depth);row.addWidget(self.depth)
        self.shape.setToolTip('Geometric downstream closure outside the selected point(s). The dashed orange line previews the closure.')
        self.depth.setToolTip('Arc depth measured outwards from the line between starts. Zero = half the width. Change to update the preview; this is not a pumping-based boundary.')
        self.shape.currentIndexChanged.connect(tool.change_base);self.depth.valueChanged.connect(tool.change_base)
        self.depth.setEnabled(self.shape.currentIndex()==0)
        self.buttons={}
        for actions in ((('Place arc endpoints on map',tool.place_starts),),
                        (('Add next contour',tool.add_next),),
                        (('Switch side',tool.switch_side),('Undo last step',tool.undo)),
                        (('Finish polygon',tool.finish_polygon),('Finish & export…',lambda:tool.finish_polygon(True))),
                        (('Save drawing draft…',tool.save_draft),('Pause / return',lambda:tool.owner.finish_extra_tool()))):
            row=QHBoxLayout();layout.addLayout(row)
            for text,callback in actions:
                button=QPushButton(text);button.clicked.connect(callback);row.addWidget(button);self.buttons[text]=button
        self.buttons['Finish polygon'].setToolTip('Close between the upstream endpoints along their shared contour and between the starts near the station. Both sides must finish on the same contour.')
        self.buttons['Finish & export…'].setToolTip('Create the closed polygon, then choose GeoPackage, Shapefile or GeoJSON.')
        self.buttons['Save drawing draft…'].setToolTip('Save both unfinished paths, undo history, selected points and source contours to a portable JSON draft. Open it with Open drawing draft in the main dialog.')
        self.buttons['Add next contour'].setToolTip('Commit the orange preview to the next reachable higher contour on the active side. Or click a farther contour to include intermediate steps automatically.')
        self.buttons['Place arc endpoints on map'].setToolTip('Place the two starts yourself before drawing either side. First click = one arc end, second click = the other. Existing paths must be undone first. Arc depth remains adjustable.')

    def closeEvent(self,event):
        event.ignore();self.tool.owner.finish_extra_tool()


class PerpendicularTool(QgsMapTool):
    def __init__(self,owner,session):
        super().__init__(owner.iface.mapCanvas());self.owner=owner;self.session=session;self.setCursor(Qt.CrossCursor);self.placing_starts=None
        self.bands=[QgsRubberBand(self.canvas(),QgsWkbTypes.LineGeometry) for _ in range(4)]
        self.markers=[QgsVertexMarker(self.canvas()) for _ in range(2)]
        for marker,point in zip(self.markers,self.session.starts):
            marker.setCenter(self.transform().transform(QgsPointXY(*point)));marker.setIconType(QgsVertexMarker.ICON_BOX);marker.setIconSize(10);marker.setPenWidth(3)
        for band,color in zip(self.bands,('green','green','orange')):band.setColor(QColor(color));band.setWidth(3)
        self.bands[3].setColor(QColor('orange'));self.bands[3].setWidth(2);self.bands[3].setLineStyle(Qt.DashLine)
        self.angle_band=QgsRubberBand(self.canvas(),QgsWkbTypes.LineGeometry)
        self.angle_band.setColor(QColor('darkmagenta'));self.angle_band.setWidth(2)
        self.panel=DrawingPanel(self)
        self.update()

    def transform(self):
        from qgis.core import QgsCoordinateReferenceSystem
        return QgsCoordinateTransform(QgsCoordinateReferenceSystem.fromWkt(self.session.data['crs_wkt']),self.canvas().mapSettings().destinationCrs(),QgsProject.instance())

    def hint(self,message=None):
        text=message or 'Click a higher contour, or Add next contour. Straight segments prefer 90 degrees; approximate arrivals show their actual angle. Only the closure at the well is rounded.'
        self.canvas().setToolTip(text)
        self.owner.dialog.log.setPlainText(text)
        self.owner.iface.messageBar().pushWarning('Perpendicular drawing',text)
        self.update_status(message)

    def update_status(self,message=None):
        heads=' / '.join(f'{h[-1]:.2f} m' for h in self.session.heads)
        mode='closest feasible to 90°' if self.session.data['settings'].manual_angle_mode=='best' else 'exact 90° only'
        self.panel.status.setText(f'Straight segments · {mode} | Side {self.session.side+1} | Heads: {heads}\n'+
            (self.session.constraint_issue() or message or 'Click a higher contour or Add next contour. Finish & export creates and saves the polygon.'))

    def activate(self):
        super().activate();self.update();self.panel.show()

    def switch_side(self):
        self.session.side=1-self.session.side;self.update()

    def change_angle(self,*args):
        from dataclasses import replace
        self.session.data['settings']=replace(self.session.data['settings'],manual_angle_mode=('best','strict')[self.panel.angle_mode.currentIndex()])
        self.update()

    def place_starts(self):
        if any(self.session.steps):
            self.hint('Undo both sides before moving the arc endpoints. Your drawing has been kept.');return
        self.placing_starts=[]
        self.bands[2].reset(QgsWkbTypes.LineGeometry);self.angle_band.reset(QgsWkbTypes.LineGeometry)
        self.placement_controls()
        self.update_status('Click the first arc endpoint beside the well, then the second. Esc cancels placement.')

    def placement_controls(self):
        placing=self.placing_starts is not None
        for name,button in self.panel.buttons.items():
            if name not in ('Pause / return','Place arc endpoints on map'):button.setEnabled(not placing)
        self.panel.angle_mode.setEnabled(not placing);self.panel.shape.setEnabled(not placing)
        self.panel.depth.setEnabled(not placing and self.panel.shape.currentIndex()==0)

    def clicked_xy(self,event):
        p=self.transform().transform(QgsPointXY(event.mapPoint()),QgsCoordinateTransform.ReverseTransform)
        return [p.x(),p.y()]

    def change_base(self,*args):
        from dataclasses import replace
        panel=self.panel
        self.session.data['settings']=replace(self.session.data['settings'],downstream_shape=('rounded','straight')[panel.shape.currentIndex()],arc_depth=panel.depth.value())
        panel.depth.setEnabled(panel.shape.currentIndex()==0)
        self.update()

    def add_next(self):
        try:
            step=self.session.next_step();self.session.append(step);self.update()
            self.update_status(f"Added the {step['head']:g} m contour at {step['angle_degrees']:.4f}°. "+self.next_description)
        except CalculationError as exc:self.hint(str(exc))

    def undo(self):
        self.session.undo();self.update()

    def finish_polygon(self,export=False):
        try:
            result=self.session.result()
            self.owner.finish_extra_tool(preserve=False)
            self.owner.dialog.completed(result,None)
            if export:self.owner.dialog.export()
        except CalculationError as exc:self.hint(str(exc))

    def save_draft(self):
        path,_=QFileDialog.getSaveFileName(self.panel,'Save unfinished drawing','catchment.draft.json','Drawing draft (*.draft.json)')
        if not path:return
        if not path.lower().endswith('.json'):path+='.draft.json'
        try:
            from .draft import save_draft
            save_draft(path,self.session);self.hint(f'Drawing draft saved: {path}. Use Finish polygon when both sides reach the same contour.')
        except Exception as exc:self.hint(str(exc))

    def candidate(self,event):
        p=self.transform().transform(QgsPointXY(event.mapPoint()),QgsCoordinateTransform.ReverseTransform)
        # Use a map-pixel tolerance transformed into the calculation CRS.
        mp=event.mapPoint();q=QgsPointXY(mp.x()+self.canvas().mapUnitsPerPixel()*15,mp.y())
        q=self.transform().transform(q,QgsCoordinateTransform.ReverseTransform)
        return self.session.propose([p.x(),p.y()],max(.01,p.distance(q)))

    def update(self):
        from .service import geometry
        self.placement_controls()
        if self.placing_starts is not None:return
        transform=self.transform()
        for band,points in zip(self.bands,self.session.points):
            if len(points)>1:
                geom=geometry(points);geom.transform(transform);band.setToGeometry(geom,None)
            else:band.reset(QgsWkbTypes.LineGeometry)
        self.bands[2].reset(QgsWkbTypes.LineGeometry)
        self.angle_band.reset(QgsWkbTypes.LineGeometry)
        geom=geometry(self.session.base());geom.transform(transform);self.bands[3].setToGeometry(geom,None)
        for side,marker in enumerate(self.markers):
            marker.setCenter(transform.transform(QgsPointXY(*self.session.starts[side])))
            marker.setColor(QColor('orange' if side==self.session.side else 'purple'))
        try:
            step=self.session.next_step();self.preview([step])
            self.next_description=f"Next: {step['head']:g} m at {step['angle_degrees']:.2f}° ({90-step['angle_degrees']:.2f}° from perpendicular). Orange preview; click Add next contour or a farther contour."
            self.panel.buttons['Add next contour'].setEnabled(True)
        except CalculationError as exc:
            self.next_description=str(exc);self.panel.buttons['Add next contour'].setEnabled(False)
        self.update_status(self.next_description)

    def preview(self,steps):
        from .service import geometry
        import numpy as np
        points=np.vstack([steps[0]['points']]+[s['points'][1:] for s in steps[1:]])
        geom=geometry(points);geom.transform(self.transform());self.bands[2].setToGeometry(geom,None)
        self.angle_band.reset(QgsWkbTypes.LineGeometry)
        if abs(steps[-1]['angle_degrees']-90)>1e-6:return
        vertices=geom.asPolyline();a,b=vertices[-2],vertices[-1]
        direction=np.array([b.x()-a.x(),b.y()-a.y()]);direction/=np.linalg.norm(direction)
        tangent=np.array([-direction[1],direction[0]])
        size=self.canvas().mapUnitsPerPixel()*9;end=np.array([b.x(),b.y()])
        self.angle_band.setToGeometry(geometry([end-direction*size,end-direction*size+tangent*size,end+tangent*size]),None)

    def canvasMoveEvent(self,event):
        if self.placing_starts is not None:
            if self.placing_starts:
                from .service import geometry
                from .downstream import downstream_arc
                try:
                    arc=geometry(downstream_arc([self.placing_starts[0],self.clicked_xy(event)],self.session.data['wells'],self.session.data['settings']))
                    arc.transform(self.transform());self.bands[3].setToGeometry(arc,None)
                except CalculationError:pass
            return
        try:
            steps=self.candidate(event);self.preview(steps)
            self.update_status(f"Preview: {len(steps)} straight step(s) to {steps[-1]['head']:g} m; {steps[-1]['angle_degrees']:.6f}° arrival. Click to add.")
        except CalculationError as exc:
            self.bands[2].reset(QgsWkbTypes.LineGeometry);self.angle_band.reset(QgsWkbTypes.LineGeometry)
            self.update_status(str(exc))

    def canvasReleaseEvent(self,event):
        try:
            if self.placing_starts is not None:
                if event.button()==Qt.RightButton:self.placing_starts=None;self.update();return
                if event.button()!=Qt.LeftButton:return
                point=self.clicked_xy(event)
                if not self.placing_starts:
                    self.placing_starts=[point];self.markers[0].setCenter(QgsPointXY(event.mapPoint()))
                    self.update_status('Click the second arc endpoint. Orange previews the downstream closure.');return
                self.session.set_starts([self.placing_starts[0],point]);self.placing_starts=None
                from qgis.core import QgsCoordinateReferenceSystem
                self.owner.dialog.set_start_points(self.session.starts,QgsCoordinateReferenceSystem.fromWkt(self.session.data['crs_wkt']))
                self.update();return
            if event.button()==Qt.LeftButton:
                steps=self.candidate(event)
                for step in steps:self.session.append(step)
                self.update()
                deviation=max(90-s['angle_degrees'] for s in steps)
                self.update_status(f"Added {len(steps)} contour step(s) to {steps[-1]['head']:g} m. Largest deviation from 90°: {deviation:.2f}°. Continue or switch side.")
            elif event.button()==Qt.RightButton:
                if self.session.side==0:
                    if len(self.session.points[0])<2:raise CalculationError('Draw at least one step on side 1 first.')
                    self.session.side=1;self.update();self.hint()
                else:
                    self.finish_polygon()
        except CalculationError as exc:self.hint(str(exc))

    def keyPressEvent(self,event):
        if self.placing_starts is not None:
            if event.key()==Qt.Key_Escape:self.placing_starts=None;self.update()
            return
        if event.key()==Qt.Key_Escape:self.owner.finish_extra_tool()
        elif event.key()==Qt.Key_Backspace:self.undo()
        elif event.key()==Qt.Key_Tab:self.switch_side()
        elif event.key() in (Qt.Key_Return,Qt.Key_Enter):self.finish_polygon()

    def deactivate(self):
        for band in self.bands:band.reset(QgsWkbTypes.LineGeometry)
        self.angle_band.reset(QgsWkbTypes.LineGeometry)
        self.panel.hide()
        self.canvas().setToolTip('');super().deactivate()

    def dispose(self):
        self.panel.hide();self.panel.deleteLater()
        for band in self.bands:self.canvas().scene().removeItem(band)
        self.canvas().scene().removeItem(self.angle_band)
        for marker in self.markers:self.canvas().scene().removeItem(marker)
