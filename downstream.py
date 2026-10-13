# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit geometric downstream closure; not a pumping-based capture width."""
import numpy as np
from .core.flow import CalculationError


def downstream_arc(starts,wells,settings):
    """Return a base oriented from side 2 to side 1, outside the selected points."""
    a,b=np.asarray(starts,float);middle=(a+b)/2;width=np.linalg.norm(b-a)
    if width<.1:raise CalculationError('The two start points must be distinct.')
    if settings.downstream_shape=='straight':return np.array([b,a])
    tangent=(b-a)/width;away=np.array([-tangent[1],tangent[0]])
    offsets=(np.asarray(wells)-middle)@away
    if offsets.mean()>0:away=-away
    depth=settings.arc_depth or width*.5
    t=np.linspace(0,np.pi,65)
    result=middle+np.cos(t)[:,None]*width*.5*tangent+np.sin(t)[:,None]*depth*away
    result[0]=b;result[-1]=a
    return result
