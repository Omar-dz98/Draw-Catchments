# SPDX-License-Identifier: GPL-3.0-or-later
"""User-directed perpendicular contour steps with exact vector geometry checks."""
from dataclasses import asdict
from datetime import datetime, timezone
import uuid
import numpy as np
from qgis.core import QgsGeometry,QgsPointXY
from .core.flow import CalculationError
from .closure import contour_caps
from .service import geometry, build_surface
from .manual_geometry import clear_connector,contour_bounds
from .downstream import downstream_arc


def crosses_lower_contour(segment,start,lines,current_head):
    for other,head in lines:
        if head>current_head+1e-6 or not other.boundingBox().intersects(segment.boundingBox()):continue
        hits=other.intersection(segment)
        if any(np.linalg.norm(np.array([v.x(),v.y()])-start)>.02 for v in hits.vertices()):return True
    return False


def perpendicular_step(start, click, lines, current_head, tolerance, obstacles=(),contour_index=None,angle_mode='strict',bounds=None):
    """Pick a clicked contour, then project onto an actual straight segment.

    The click chooses a contour, not a free endpoint. Search its normal feet,
    closest to the click first. Every returned step has exactly two vertices.
    Prefer exact normals. In best mode search straight, unobstructed arrivals
    nearest to 90 degrees. Never bend the connector or pretend it is normal.
    """
    start,click=np.asarray(start,float),np.asarray(click,float)
    clicked=QgsGeometry.fromPointXY(QgsPointXY(*click))
    nearby=[(line.distance(clicked),i,line,head) for i,(line,head) in enumerate(lines)
            if contour_index is None or i==contour_index
            if head>current_head+1e-7 and line.distance(clicked)<=tolerance]
    if not nearby:raise CalculationError('Click the next higher contour near the intended connection.')
    _,index,line,head=min(nearby,key=lambda item:item[0])
    if bounds is None:bounds=contour_bounds(lines)
    coords=np.array([[p.x(),p.y()] for p in line.vertices()]);candidates=[]
    if len(coords)>2:
        left=np.diff(coords,axis=0)[:-1];right=np.diff(coords,axis=0)[1:]
        cross=left[:,0]*right[:,1]-left[:,1]*right[:,0]
        keep=np.r_[True,(np.abs(cross)>1e-10*np.linalg.norm(left,axis=1)*np.linalg.norm(right,axis=1))|(np.sum(left*right,axis=1)<=0),True]
        coords=coords[keep]
    for a,b in zip(coords[:-1],coords[1:]):
        tangent=b-a;denom=float(tangent@tangent)
        if denom<1e-12:continue
        t=float((start-a)@tangent/denom)
        if not 1e-7<t<1-1e-7:continue
        foot=a+t*tangent;direction=foot-start;length=float(np.linalg.norm(direction))
        if length<.001:continue
        candidates.append((float(np.linalg.norm(foot-click)),foot,length))
    for _,foot,length in sorted(candidates,key=lambda item:item[0]):
        if not clear_connector(np.array([start,foot]),lines,current_head,index,obstacles,bounds):continue
        step=dict(point=foot,points=np.array([start,foot]),head=float(head),length_m=length,contour_index=index)
        try:step['angle_degrees']=straight_normal_angle(step,lines)
        except CalculationError:continue
        return step
    if angle_mode=='best':
        # Search the ORIGINAL segments. An inset avoids ambiguous corner
        # tangents. The clipped normal is the unconstrained optimum on each
        # segment; other samples allow a route around intervening obstacles.
        raw=np.array([[p.x(),p.y()] for p in line.vertices()])
        a=raw[:-1];delta=np.diff(raw,axis=0);lengths=np.linalg.norm(delta,axis=1)
        mask=lengths>.002;a=a[mask];delta=delta[mask];lengths=lengths[mask]
        inset=np.minimum(.001/lengths,.1)
        normal=np.sum((start-a)*delta,axis=1)/lengths**2
        clicked_t=np.sum((click-a)*delta,axis=1)/lengths**2
        fractions=np.column_stack([np.clip(normal,inset,1-inset),np.clip(clicked_t,inset,1-inset),
                                   inset,1-inset,np.tile(np.linspace(.025,.975,39),(len(a),1))])
        feet=a[:,None,:]+fractions[:,:,None]*delta[:,None,:]
        vectors=feet-start;distances=np.linalg.norm(vectors,axis=2)
        cosine=np.abs(np.sum(vectors*delta[:,None,:],axis=2))/np.maximum(distances*lengths[:,None],1e-20)
        angles=np.degrees(np.arccos(np.clip(cosine,0,1)))
        proximity=np.linalg.norm(feet-click,axis=2)
        order=np.lexsort((distances.ravel(),proximity.ravel(),-angles.ravel()))
        for flat in order:
            segment_index,position=np.unravel_index(flat,angles.shape)
            foot=feet[segment_index,position];length=float(distances[segment_index,position])
            if length<.001 or angles[segment_index,position]<1e-5:continue
            points=np.array([start,foot])
            if not clear_connector(points,lines,current_head,index,obstacles,bounds):continue
            step=dict(point=foot,points=points,head=float(head),length_m=length,contour_index=index)
            step['angle_degrees']=straight_arrival_angle(step,lines)
            step['deviation_degrees']=90-step['angle_degrees']
            step['method']='closest_feasible_straight_candidate'
            return step
        raise CalculationError('No clear straight connection to this contour. Another contour or boundary blocks it. '
                               'Choose another connected part, add an intermediate contour, or undo the previous step. No segment was added.')
    raise CalculationError('No straight 90-degree connection to this contour without crossing another contour or boundary. '
                           'Undo the previous step or choose another connected part of the next higher contour. No segment was added.')


def straight_arrival_angle(step,lines):
    """Independently measure the WHOLE connector against the original geometry."""
    vertices=np.asarray(step['points'],float)
    if vertices.shape!=(2,2) or not np.isfinite(vertices).all():
        raise CalculationError('This step contains a bend; a contour connection must be one straight segment.')
    index=int(step['contour_index'])
    if not 0<=index<len(lines):raise CalculationError('The source contour for this step is missing.')
    line,head=lines[index]
    if abs(head-float(step['head']))>1e-6:raise CalculationError('The step does not match its source contour head.')
    endpoint=QgsPointXY(*vertices[-1])
    distance,_,after,_=line.closestSegmentWithContext(endpoint)
    coords=list(line.vertices())
    if distance<0 or distance>1e-10 or not 0<after<len(coords):
        raise CalculationError('The step endpoint is not on its source contour.')
    tangent=np.array([coords[after].x()-coords[after-1].x(),coords[after].y()-coords[after-1].y()])
    direction=vertices[-1]-vertices[0];denom=np.linalg.norm(tangent)*np.linalg.norm(direction)
    if denom<1e-10:raise CalculationError('The connection or source segment has zero length.')
    angle=float(np.degrees(np.arccos(np.clip(abs(direction@tangent)/denom,0,1))))
    return angle


def straight_normal_angle(step,lines):
    angle=straight_arrival_angle(step,lines)
    if abs(angle-90)>1e-6:
        raise CalculationError(f'The whole connection meets its source contour at {angle:.4f} degrees, not 90 degrees.')
    return angle


class ManualSession:
    def __init__(self,data):
        self.data=data;surface=build_surface(data);self.surface=surface
        starts,info=surface.initial(data['wells'],data['settings'].width,check_starts=data.get('starts') is None)
        if data.get('starts') is not None:
            starts=np.asarray(data['starts'],float)
            info.update(initialization='user start points',width_m=float(np.linalg.norm(starts[1]-starts[0])))
        self.starts=starts;self.initialization=info
        self.points=[[p.copy()] for p in starts]
        self.heads=[[surface.at(p)[0]] for p in starts]
        self.steps=[[],[]];self.side=0

    def set_starts(self,points):
        if any(self.steps):
            raise CalculationError('Undo both sides before changing the arc endpoints. Existing paths have been kept.')
        points=np.asarray(points,float)
        if points.shape!=(2,2) or not np.isfinite(points).all() or np.linalg.norm(points[1]-points[0])<.1:
            raise CalculationError('Place two distinct arc endpoints, at least 0.1 m apart.')
        surface=getattr(self,'surface',None)
        if surface is None:surface=build_surface(self.data)
        heads=[[surface.at(p)[0]] for p in points]
        self.surface=surface;self.starts=points.copy();self.data['starts']=points.copy()
        self.points=[[p.copy()] for p in points];self.heads=heads;self.side=0
        self.initialization.update(initialization='user start points',width_m=float(np.linalg.norm(points[1]-points[0])))

    def candidate(self,click,tolerance):
        other=self.points[1-self.side]
        obstacles=[geometry(other)] if len(other)>1 else []
        start=self.points[self.side][-1];lines=self.data['lines'];head=self.heads[self.side][-1]
        clicked=QgsGeometry.fromPointXY(QgsPointXY(*click))
        choices=[(line.distance(clicked),i) for i,(line,h) in enumerate(lines) if h>head+1e-7]
        distance,index=min(choices,default=(float('inf'),None))
        if distance>tolerance:raise CalculationError('Click a higher contour, or use Add next contour to follow the orange preview.')
        return self.step_to(index,click,obstacles)

    def step_to(self,index,click,obstacles=None):
        issue=self.constraint_issue()
        if issue:raise CalculationError(issue)
        points=self.points[self.side];start=points[-1];head=self.heads[self.side][-1];lines=self.data['lines']
        if obstacles is None:
            other=self.points[1-self.side];obstacles=[geometry(other)] if len(other)>1 else []
        # A new straight segment may change direction at its starting contour.
        # Do not impose tangent continuity: that would require rounding the side.
        if getattr(self,'_bounds_source',None) is not lines:
            self._bounds_source=lines;self._bounds=contour_bounds(lines)
        return perpendicular_step(start,click,lines,head,float('inf'),obstacles,contour_index=index,
                                  angle_mode=self.data['settings'].manual_angle_mode,bounds=self._bounds)

    def next_step(self,max_head=float('inf')):
        issue=self.constraint_issue()
        if issue:raise CalculationError(issue)
        start=self.points[self.side][-1];point=QgsGeometry.fromPointXY(QgsPointXY(*start))
        options=sorted([(line.distance(point),i,line) for i,(line,h) in enumerate(self.data['lines'])
                        if self.heads[self.side][-1]+1e-7<h<=max_head],key=lambda v:v[0])
        for _,index,line in options[:12]:
            p=line.nearestPoint(point).asPoint()
            try:return self.step_to(index,[p.x(),p.y()])
            except CalculationError:pass
        raise CalculationError('No clear next straight step was found. Click another contour segment, adjust the starts, or undo the last step.')

    def propose(self,click,tolerance):
        """Preview a whole click, adding intervening contours without mutating work."""
        point=QgsGeometry.fromPointXY(QgsPointXY(*click));lines=self.data['lines']
        choices=[(line.distance(point),i,h) for i,(line,h) in enumerate(lines) if h>self.heads[self.side][-1]+1e-7]
        distance,index,target=min(choices,default=(float('inf'),None,None))
        if distance>tolerance:raise CalculationError('Click a higher contour, or use Add next contour to follow the orange preview.')
        side=self.side;old_points=list(self.points[side]);old_heads=list(self.heads[side]);old_steps=list(self.steps[side]);batch=[]
        try:
            for _ in range(80):
                # Work through nearer intermediate contours first. Searching
                # thousands of oblique arrivals on a distant, blocked contour
                # before each intermediate step made a single click freeze.
                current=QgsGeometry.fromPointXY(QgsPointXY(*self.points[side][-1]))
                target_distance=lines[index][0].distance(current)
                nearer=any(self.heads[side][-1]+1e-7<h<target-1e-7 and
                           line.distance(current)<target_distance for line,h in lines)
                if nearer:
                    try:step=self.next_step(max_head=target-1e-7)
                    except CalculationError:step=self.step_to(index,click)
                else:
                    try:step=self.step_to(index,click)
                    except CalculationError:step=self.next_step(max_head=target-1e-7)
                self.append(step);batch.append(step)
                if abs(step['head']-target)<1e-7:return batch
            raise CalculationError('This click spans over 80 contour intervals. Add a nearer contour first.')
        finally:
            self.points[side]=old_points;self.heads[side]=old_heads;self.steps[side]=old_steps

    def base(self):return downstream_arc(self.starts,self.data['wells'],self.data['settings'])

    def append(self,step,restore_legacy=False):
        if not restore_legacy:
            if not np.allclose(step['points'][0],self.points[self.side][-1],rtol=0,atol=1e-6):
                raise CalculationError('The step must start at the current boundary endpoint.')
            measure=straight_normal_angle if self.data['settings'].manual_angle_mode=='strict' else straight_arrival_angle
            step['angle_degrees']=measure(step,self.data['lines'])
            step['deviation_degrees']=90-step['angle_degrees']
            step['method']='exact_normal' if step['deviation_degrees']<1e-6 else 'closest_feasible_straight_candidate'
            if step['head']<=self.heads[self.side][-1]+1e-7:
                raise CalculationError('A boundary step must reach a higher head contour.')
            if not clear_connector(step['points'],self.data['lines'],self.heads[self.side][-1],step['contour_index'],[]):
                raise CalculationError('This connector crosses another contour. Add the intermediate contour first.')
        additions=list(step['points'][1:]);points=np.array(self.points[self.side]+additions)
        line=geometry(points)
        if not line.isSimple():raise CalculationError('This step would make the boundary self-intersect. Choose another contour segment.')
        other=geometry(self.points[1-self.side]) if len(self.points[1-self.side])>1 else None
        if other is not None and line.intersects(other):raise CalculationError('The lateral boundaries would cross. Choose another contour segment.')
        self.points[self.side].extend(additions)
        self.heads[self.side].extend(np.linspace(self.heads[self.side][-1],step['head'],len(additions)+1)[1:])
        self.steps[self.side].append(step)

    def undo(self):
        if len(self.points[self.side])>1:
            step=self.steps[self.side].pop();count=len(step['points'])-1
            del self.points[self.side][-count:];del self.heads[self.side][-count:]

    def result(self):
        issue=self.constraint_issue()
        if issue:raise CalculationError(issue)
        if any(len(points)<2 for points in self.points):raise CalculationError('Draw at least one perpendicular step on each side.')
        if abs(self.heads[0][-1]-self.heads[1][-1])>1e-6:
            raise CalculationError('Finish both sides on the same head contour before closing.')
        paths=[dict(points=np.array(points),heads=np.array(heads),length_m=float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum()),
                    stop_reason='Manually selected closing contour') for points,heads in zip(self.points,self.heads)]
        level=self.heads[0][-1];data=self.data
        cap_paths=[]
        for path in paths:
            delta=path['points'][-1]-path['points'][-2]
            cap_paths.append(dict(path,points=np.vstack([path['points'],path['points'][-1]+delta/np.linalg.norm(delta)*1e-5]),
                                  heads=np.r_[path['heads'],path['heads'][-1]]))
        for trimmed,cap in contour_caps(cap_paths,data['lines'],level,.001):
            base=self.base()
            ring=np.vstack([trimmed[0]['points'],cap[1:],trimmed[1]['points'][::-1][1:],base[1:]])
            area=QgsGeometry.fromPolygonXY([[QgsPointXY(*p) for p in ring]])
            if not area.isGeosValid() or area.area()<=1:continue
            if not all(area.contains(QgsGeometry.fromPointXY(QgsPointXY(*p))) for p in data['wells']):continue
            if data['settings'].stop=='target' and not all(data['upstream_target'].intersects(QgsGeometry.fromPointXY(QgsPointXY(*path['points'][-1]))) for path in trimmed):continue
            meta=dict(version='0.1.13',run_id=str(uuid.uuid4()),created_utc=datetime.now(timezone.utc).isoformat(),
                crs=data['crs'],crs_wkt=data['crs_wkt'],settings=asdict(data['settings']),initialization=self.initialization,
                target_wkt=data['upstream_target'].asWkt() if data.get('upstream_target') is not None else None,
                requested_settings=data.get('requested_settings',{}),
                wells=[dict(**ident,xy=p.tolist()) for ident,p in zip(data['identifiers'],data['wells'])],sources=data['source_names'],
                paths=[dict(length_m=p['length_m'],stop_reason='Manually selected closing contour') for p in trimmed],
                upstream_closure=dict(method='original contour arc',head_m=level),
                manual_steps=[[dict(head=s['head'],angle_degrees=straight_arrival_angle(s,data['lines']),
                                   deviation_degrees=90-straight_arrival_angle(s,data['lines']),length_m=s['length_m']) for s in side] for side in self.steps],
                manual_constraint='one straight segment per contour interval; prefer 90 degrees, report actual arrival angles',
                downstream_closure=dict(method=data['settings'].downstream_shape,depth_m=data['settings'].arc_depth or (np.linalg.norm(self.starts[1]-self.starts[0])*.5 if data['settings'].downstream_shape=='rounded' else 0)),
                warnings=['Each manual interval is one straight segment. Exact normal arrivals are preferred; approximate arrivals are recorded with their measured angles. No side smoothing.',
                          'The downstream closure and upstream contour arc are not flow-path steps.',
                          'Rounded downstream closure is a geometric assumption, not a pumping-based capture boundary.',
                          'Geometric preliminary proposal; not a validated groundwater catchment.'])
            return dict(polygon=area.forcePolygonCounterClockwise(),paths=[geometry(p['points']) for p in trimmed],starts=self.starts,
                        metadata=meta,upstream_boundary=geometry(cap),downstream_boundary=geometry(base))
        raise CalculationError('The two paths do not form a valid polygon enclosing the point on a common contour. Undo or extend the paths.')

    def constraint_issue(self):
        for side,steps in enumerate(self.steps):
            for number,step in enumerate(steps,1):
                try:straight_arrival_angle(step,self.data['lines'])
                except CalculationError as exc:
                    return f'Side {side+1}, step {number}: {exc} Old draft retained. Undo back to this step and redraw before finishing.'
        return None
