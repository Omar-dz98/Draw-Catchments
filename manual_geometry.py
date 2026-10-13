# SPDX-License-Identifier: GPL-3.0-or-later
"""Intersection checks for straight contour-to-contour boundary segments."""
import numpy as np
from .service import geometry


def contour_bounds(lines):
    return np.array([[g.boundingBox().xMinimum(),g.boundingBox().yMinimum(),
                      g.boundingBox().xMaximum(),g.boundingBox().yMaximum()] for g,h in lines])


def clear_connector(points,lines,current_head,index,obstacles,bounds=None):
    segment=geometry(points)
    if not segment.isSimple() or any(segment.intersects(g) for g in obstacles):return False
    candidates=range(len(lines))
    if bounds is not None:
        low=np.min(points,axis=0);high=np.max(points,axis=0)
        candidates=np.flatnonzero((bounds[:,0]<=high[0])&(bounds[:,2]>=low[0])&
                                 (bounds[:,1]<=high[1])&(bounds[:,3]>=low[1]))
    for i in candidates:
        line,_=lines[i]
        if not line.boundingBox().intersects(segment.boundingBox()):continue
        hits=segment.intersection(line)
        for vertex in hits.vertices():
            pt=np.array([vertex.x(),vertex.y()])
            if np.linalg.norm(pt-points[0])<.02:continue
            if i==index and np.linalg.norm(pt-points[-1])<.02:continue
            return False
    return True
