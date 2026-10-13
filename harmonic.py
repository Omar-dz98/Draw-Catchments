# SPDX-License-Identifier: GPL-3.0-or-later
"""Contour-constrained harmonic interpolation, not a groundwater flow model.

Contour crossings cut grid edges and supply observed Dirichlet heads. The
positive-weight Laplacian cannot create interior extrema between contours.
"""
import numpy as np
from scipy.interpolate import RegularGridInterpolator
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve
from .flow import HeadSurface, CalculationError


class HarmonicSurface(HeadSurface):
    def __init__(self, xy, heads, max_gap, sample, lines, canceled=lambda: False):
        super().__init__(xy,heads,max_gap,sample,build_cubic=False)
        cell=max(5.,sample)
        low=self.xy.min(axis=0)+self.origin
        high=self.xy.max(axis=0)+self.origin
        # Keep wide regional data computable without imposing a distance cutoff.
        cell=max(cell,float(np.sqrt(np.prod(high-low)/800000)))
        self.gx=np.arange(low[0],high[0]+cell,cell)
        self.gy=np.arange(low[1],high[1]+cell,cell)
        nx,ny=len(self.gx),len(self.gy)
        if nx*ny>900000:
            raise CalculationError(f'Interpolation grid too large. Increase contour sample spacing above {cell*np.sqrt(nx*ny/850000):.0f} m.')
        xx,yy=np.meshgrid(self.gx,self.gy)
        points=np.column_stack([xx.ravel(),yy.ravel()])
        ids=self.tri.find_simplex(points-self.origin)
        vertices=self.xy[self.tri.simplices]
        close=np.maximum.reduce([np.linalg.norm(vertices[:,i]-vertices[:,j],axis=1) for i,j in ((0,1),(1,2),(2,0))])<=(max_gap if max_gap else np.inf)
        self.support=close
        valid=((ids>=0)&close[np.maximum(ids,0)]).reshape(ny,nx)
        # Store the first / last contour crossing of each grid edge. If several
        # contours cross it, each endpoint sees the nearest observed contour.
        crossings={}
        for coordinates,head in lines:
            if canceled():raise CalculationError('Calculation canceled.')
            for a,b in zip(coordinates[:-1],coordinates[1:]):
                a=(np.asarray(a)-low)/cell;b=(np.asarray(b)-low)/cell
                for axis in (0,1):
                    delta=b[axis]-a[axis]
                    if abs(delta)<1e-12:continue
                    for k in range(int(np.ceil(min(a[axis],b[axis]))),int(np.floor(max(a[axis],b[axis])))+1):
                        t=(k-a[axis])/delta
                        other=a[1-axis]+t*(b[1-axis]-a[1-axis])
                        j=int(np.floor(other));fraction=other-j
                        edges=[(j,fraction)]
                        # A contour on a grid node constrains BOTH incident
                        # edges, including a contour on the outermost column.
                        if fraction<1e-9:edges=[(j,0.),(j-1,1.)]
                        elif fraction>1-1e-9:edges=[(j,1.),(j+1,0.)]
                        for j,fraction in edges:
                            if axis==0:
                                if not (0<=k<nx and 0<=j<ny-1):continue
                                u=j*nx+k;v=u+nx
                            else:
                                if not (0<=k<ny and 0<=j<nx-1):continue
                                u=k*nx+j;v=u+1
                            crossings.setdefault((u,v),[]).append((float(fraction),float(head)))
        unknown=np.flatnonzero(valid.ravel());lookup=np.full(nx*ny,-1,dtype=int);lookup[unknown]=np.arange(len(unknown))
        rows=[];cols=[];weights=[];diag=np.zeros(len(unknown));rhs=np.zeros(len(unknown));anchored=np.zeros(len(unknown),bool)
        for offset,edge_mask in ((1,np.tile(np.arange(nx)<nx-1,ny)),(nx,np.repeat(np.arange(ny)<ny-1,nx))):
            starts=np.flatnonzero(edge_mask)
            for u in starts:
                v=u+offset
                cuts=crossings.get((u,v))
                if cuts:
                    first,last=min(cuts),max(cuts)
                    for node,(fraction,head) in ((u,first),(v,(1-last[0],last[1]))):
                        i=lookup[node]
                        if i<0:continue
                        weight=1/max(fraction,.001)
                        diag[i]+=weight;rhs[i]+=weight*head;anchored[i]=True
                elif valid.ravel()[u] and valid.ravel()[v]:
                    i,j=lookup[u],lookup[v]
                    rows.extend((i,j));cols.extend((j,i));weights.extend((-1.,-1.))
                    diag[i]+=1;diag[j]+=1
        # Isolated islands with no observed contour are unsupported, not data.
        from scipy.sparse.csgraph import connected_components
        graph=coo_matrix((np.ones(len(rows)),(rows,cols)),shape=(len(unknown),len(unknown))).tocsr()
        count,labels=connected_components(graph,directed=False)
        has_data=np.zeros(count,bool);np.logical_or.at(has_data,labels,anchored)
        unsupported=~has_data[labels]
        diag[unsupported]=1;rhs[unsupported]=0
        if np.any(unsupported):
            weights=np.asarray(weights)
            weights[unsupported[np.asarray(rows,dtype=int)]|unsupported[np.asarray(cols,dtype=int)]]=0
        matrix=coo_matrix((np.r_[weights,diag],(np.r_[rows,np.arange(len(unknown))],np.r_[cols,np.arange(len(unknown))])),shape=(len(unknown),len(unknown))).tocsr()
        if canceled():raise CalculationError('Calculation canceled.')
        values=spsolve(matrix,rhs)
        if not np.isfinite(values).all():raise CalculationError('Contour interpolation failed to converge.')
        residual=np.max(np.abs(matrix@values-rhs))
        if residual>1e-5:raise CalculationError('Contour interpolation residual is too large.')
        grid=np.full(nx*ny,np.nan);grid[unknown]=values;grid[unknown[unsupported]]=np.nan;grid=grid.reshape(ny,nx)
        self.grid_interp=RegularGridInterpolator((self.gy,self.gx),grid,bounds_error=False,fill_value=np.nan)
        gy,gx=np.gradient(grid,cell)
        self.gradient_interp=RegularGridInterpolator((self.gy,self.gx),np.stack([gx,gy],axis=-1),bounds_error=False,fill_value=np.nan)
        self.interpolation_info=dict(method='contour-constrained harmonic interpolation',cell_m=cell,nodes=len(unknown),residual=float(residual))

    def values(self,points):
        points=np.asarray(points,float)
        return self.grid_interp(points[:,[1,0]])

    def at(self,point):
        q=np.asarray(point,float)[[1,0]]
        value=float(self.grid_interp(q)[0]);gradient=self.gradient_interp(q)[0]
        if not np.isfinite(value) or not np.isfinite(gradient).all():
            raise CalculationError('Reliable contour coverage limit reached.')
        if np.linalg.norm(gradient)<1e-8:
            raise CalculationError('Gradient too small; this is not a verified groundwater divide.')
        return value,gradient
