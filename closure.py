"""Close upstream on the actual contour geometry, retaining its bends."""
import numpy as np
from qgis.core import QgsGeometry,QgsPointXY


def substring(points, start, end):
    """Polyline arc preserving every interior original vertex."""
    points=np.asarray(points,float)
    distances=np.r_[0,np.linalg.norm(np.diff(points,axis=0),axis=1).cumsum()]
    keep=np.r_[True,np.diff(distances)>1e-9]
    distances,points=distances[keep],points[keep]
    lo,hi=sorted((start,end))
    values=np.r_[lo,distances[(distances>lo+1e-8)&(distances<hi-1e-8)],hi]
    segment=np.column_stack([np.interp(values,distances,points[:,axis]) for axis in (0,1)])
    return segment if start<=end else segment[::-1]


def contour_caps(paths, lines, level, minimum_length):
    """Yield paths trimmed to intersections and connecting same-contour arcs."""
    components=[line for line,head in lines if abs(head-level)<1e-8]
    if not components:return
    merged=QgsGeometry.unaryUnion(components)
    if merged.isMultipart():merged=merged.mergeLines()
    if merged.isEmpty():return
    path_geometries=[QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in path['points']]) for path in paths]
    for contour in merged.asGeometryCollection() if merged.isMultipart() else [merged]:
        coords=np.array([[p.x(),p.y()] for p in contour.vertices()])
        if len(coords)<2:continue
        intersections=[]
        for path_geom in path_geometries:
            hits=path_geom.intersection(contour)
            locations=[]
            for point in hits.vertices():
                geom=QgsGeometry.fromPointXY(QgsPointXY(point))
                length=path_geom.lineLocatePoint(geom)
                along=contour.lineLocatePoint(geom)
                if length>=minimum_length and along>=0:
                    locations.append((length,along,np.array([point.x(),point.y()])))
            # A segment/contour hit written back through floating-point XY can
            # end a fraction of a nanometre off the original segment. GEOS may
            # then report no intersection at the endpoint. Accept only a
            # sub-micrometre endpoint coincidence, never a nearby unvisited line.
            endpoint=path_geom.asPolyline()[-1]
            point_geom=QgsGeometry.fromPointXY(endpoint)
            if path_geom.length()>=minimum_length and contour.distance(point_geom)<=1e-6:
                along=contour.lineLocatePoint(point_geom)
                if along>=0:
                    locations.append((path_geom.length(),along,np.array([endpoint.x(),endpoint.y()])))
            intersections.append(sorted(locations,key=lambda hit:hit[0]))
        # Uphill paths should cross each level once. If interpolation creates
        # repeated crossings, use the first genuine crossing along each path.
        if any(not hits for hits in intersections):continue
        a,b=(hits[0] for hits in intersections)
        trimmed=[]
        for path,hit in zip(paths,(a,b)):
            points=substring(path['points'],0,hit[0]);points[-1]=hit[2]
            distances=np.r_[0,np.linalg.norm(np.diff(path['points'],axis=0),axis=1).cumsum()]
            cut_distances=np.r_[0,np.linalg.norm(np.diff(points,axis=0),axis=1).cumsum()]
            heads=np.interp(cut_distances,distances,path['heads'])
            trimmed.append(dict(points=points,heads=heads,length_m=float(cut_distances[-1]),
                stop_reason=f'Highest connected usable contour: {level:g} m (artificial; not a divide)'))
        arcs=[substring(coords,a[1],b[1])]
        # For closed isohypses, test both arcs; the caller checks containment.
        if np.linalg.norm(coords[0]-coords[-1])<1e-7:
            length=contour.length()
            if a[1]<=b[1]:
                other=np.vstack([substring(coords,a[1],0),substring(coords,length,b[1])[1:]])
            else:
                other=np.vstack([substring(coords,a[1],length),substring(coords,0,b[1])[1:]])
            arcs.append(other)
        for arc in sorted(arcs,key=lambda part:np.linalg.norm(np.diff(part,axis=0),axis=1).sum()):
            arc[0]=a[2];arc[-1]=b[2]
            yield trimmed,arc
