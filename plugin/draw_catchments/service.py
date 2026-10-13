"""PyQGIS input validation, geometry construction and loss-aware export.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import numpy as np
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsFeatureRequest, QgsField, QgsGeometry, QgsPointXY, QgsProject,
    QgsRectangle, QgsSpatialIndex, QgsUnitTypes, QgsVectorFileWriter,
    QgsVectorLayer, QgsWkbTypes,
)
from .core.flow import CalculationError, HeadSurface, trace
from .closure import contour_caps
from .contour_inputs import is_result_layer
from .downstream import downstream_arc


def geometry(points):
    return QgsGeometry.fromPolylineXY([QgsPointXY(float(x), float(y)) for x, y in points])


def transformed(geom, source, target):
    if not source.isValid():
        raise CalculationError("Input layer has no valid CRS. Assign its actual CRS first.")
    result = QgsGeometry(geom)
    if source != target:
        result.transform(QgsCoordinateTransform(source, target, QgsProject.instance()))
    return result


def local_calculation_crs(contours, wells, point_target=None, selected=False):
    """Prefer input metre CRSs, otherwise use the UTM zone at the chosen point."""
    for layer in (contours, wells):
        if layer is not None:
            crs = layer.crs()
            if (crs.isValid() and not crs.isGeographic() and crs.mapUnits() == QgsUnitTypes.DistanceMeters
                    and crs.authid() not in ('EPSG:3857', 'EPSG:3395')):
                return crs
    if wells is None or not wells.isValid():
        raise CalculationError('Choose a point or click Pick one point on map.')
    if point_target is not None:
        feature = wells.getFeature(int(point_target[0]))
    elif selected:
        feature = next(iter(wells.selectedFeatures()), None)
    else:
        feature = next(wells.getFeatures(), None)
    if feature is None or not feature.isValid() or feature.geometry().isEmpty():
        raise CalculationError('Choose an existing point before selecting the local calculation CRS.')
    geom = transformed(feature.geometry(), wells.crs(), QgsCoordinateReferenceSystem('EPSG:4326'))
    points = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
    part = int(point_target[1]) if point_target is not None else 0
    if not 0 <= part < len(points):
        raise CalculationError('The chosen point component no longer exists.')
    lon, lat = points[part].x(), points[part].y()
    if lat > 84:
        code = 3413
    elif lat < -80:
        code = 3031
    else:
        zone = max(1, min(60, int((lon + 180) // 6) + 1))
        code = (32600 if lat >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem(f'EPSG:{code}')


def prepare(contours, field, wells, selected, settings, crs=None,
            id_field=None, stop_geometry=None, starts=None, point_target=None, upstream_target=None):
    """Snapshot QGIS layers on the main thread before submitting background work."""
    settings.validate()
    if not contours or not wells or not contours.isValid() or not wells.isValid():
        raise CalculationError("Choose a valid contour layer and well layer.")
    if contours.geometryType() != QgsWkbTypes.LineGeometry:
        raise CalculationError("Contours must be a line layer.")
    if is_result_layer(contours):
        raise CalculationError("This is a Draw Catchments result layer, not a groundwater contour plan. "
                               "Choose the original groundwater contours and their hydraulic-head field "
                               "(for example ZLEVEL). A contour closure or flow path cannot be used as input.")
    if wells.geometryType() != QgsWkbTypes.PointGeometry:
        raise CalculationError("Wells must be a point layer.")
    if field not in contours.fields().names():
        raise CalculationError("Select the hydraulic-head attribute in metres (for example ZLEVEL). "
                               "A feature identifier such as fid is not a groundwater level.")
    crs = crs or QgsCoordinateReferenceSystem("EPSG:25833")
    if not crs.isValid() or crs.isGeographic() or crs.mapUnits() != QgsUnitTypes.DistanceMeters:
        raise CalculationError("The calculation CRS must be projected and use metres.")
    if point_target is not None:
        feature = wells.getFeature(int(point_target[0]))
        if not feature.isValid():
            raise CalculationError("The chosen point no longer exists. Choose another point from the dropdown.")
        features = [feature]
    else:
        features = wells.selectedFeatures() if selected else list(wells.getFeatures())
    if not features:
        raise CalculationError("No wells selected. Select wells on the map or choose All wells.")
    if len(features) > 500:
        raise CalculationError("Select at most 500 wells in one local group.")
    coords, identifiers = [], []
    for f in features:
        if f.geometry().isEmpty():
            raise CalculationError(f"Well {f.id()} has an empty geometry.")
        g = transformed(f.geometry(), wells.crs(), crs)
        pts = g.asMultiPoint() if g.isMultipart() else [g.asPoint()]
        if point_target is not None:
            part = int(point_target[1])
            if part < 0 or part >= len(pts):
                raise CalculationError("The chosen point component no longer exists. Choose the point again.")
            pts = [pts[part]]
        for p in pts:
            coords.append([p.x(), p.y()])
            identifiers.append({"fid": f.id(), "id": str(f[id_field]) if id_field else str(f.id())})
            if point_target is not None:
                identifiers[-1]['part_index'] = int(point_target[1])
    coords = np.asarray(coords)
    if not np.isfinite(coords).all():
        raise CalculationError("Invalid well coordinates.")
    rect = QgsRectangle(float(coords[:, 0].min()), float(coords[:, 1].min()),
                        float(coords[:, 0].max()), float(coords[:, 1].max()))
    rect.grow(settings.radius)
    if starts is not None:
        for point in np.asarray(starts,float):
            point_rect=QgsRectangle(float(point[0]),float(point[1]),float(point[0]),float(point[1]))
            point_rect.grow(max(settings.sample*5,settings.step*5))
            rect.combineExtentWith(point_rect)
    requested_settings = asdict(settings)
    if settings.stop == 'target':
        if upstream_target is None or upstream_target.isEmpty() or not upstream_target.isGeosValid():
            raise CalculationError('Mark an upstream target area on the map first.')
        if upstream_target.type() != QgsWkbTypes.PolygonGeometry:
            raise CalculationError('The upstream target must be a polygon area.')
        target_rect = upstream_target.boundingBox()
        target_rect.grow(max(500, settings.sample*5))
        rect.combineExtentWith(target_rect)
        # Target selection expands analysis and the path-length budget only;
        # it does not steer the gradient or invent a connecting line.
        distance = np.linalg.norm(np.array([target_rect.center().x(),target_rect.center().y()])-coords.mean(axis=0))
        settings = replace(settings, max_length=max(settings.max_length,3*distance), interpolation='harmonic')
    clip = QgsGeometry.fromRect(rect)
    input_rect = QgsCoordinateTransform(crs, contours.crs(), QgsProject.instance()).transformBoundingBox(rect)
    lines = []
    for f in contours.getFeatures(QgsFeatureRequest().setFilterRect(input_rect)):
        try:
            h = float(f[field])
        except (TypeError, ValueError):
            raise CalculationError(f"Contour {f.id()}: missing or non-numeric head.")
        if not np.isfinite(h):
            raise CalculationError(f"Contour {f.id()}: head must be finite.")
        g = transformed(f.geometry(), contours.crs(), crs)
        if g.isEmpty() or not g.isGeosValid():
            raise CalculationError(f"Contour {f.id()}: empty or invalid geometry.")
        g = g.intersection(clip)
        if g.isEmpty():
            continue
        for part in g.asGeometryCollection() if g.isMultipart() else [g]:
            if part.length() > 0 and part.type() == QgsWkbTypes.LineGeometry:
                lines.append((QgsGeometry(part), h))
    if not lines:
        if not settings.auto_extent:
            raise CalculationError("No contours within the analysis radius of the wells.")
    if settings.auto_extent:
        hull=QgsGeometry.collectGeometry([line for line,head in lines]).convexHull() if lines else QgsGeometry()
        covered=len(set(head for line,head in lines))>=3 and all(
            hull.contains(QgsGeometry.fromPointXY(QgsPointXY(*pt))) for pt in coords)
        if not covered:
            full_extent=QgsCoordinateTransform(contours.crs(),crs,QgsProject.instance()).transformBoundingBox(contours.extent())
            center=coords.mean(axis=0)
            maximum=max(abs(full_extent.xMinimum()-center[0]),abs(full_extent.xMaximum()-center[0]),
                        abs(full_extent.yMinimum()-center[1]),abs(full_extent.yMaximum()-center[1]))+settings.sample*5
            if settings.radius<maximum:
                expanded=prepare(contours,field,wells,selected,replace(settings,radius=min(maximum,settings.radius*2)),
                    crs,id_field,stop_geometry,starts,point_target,upstream_target)
                expanded['requested_settings']=requested_settings
                return expanded
            raise CalculationError('The available contours do not surround this point with at least three different head values. '
                                   'No spacing cutoff was applied. Load a contour plan covering this point and aquifer.')
        length=max(settings.max_length,3*np.hypot(rect.width(),rect.height()))
        settings=replace(settings,max_length=float(length),max_steps=max(settings.max_steps,int(np.ceil(length/settings.step))+100))
    source_lines=None
    if settings.auto_extent and settings.stop in ('highest','target'):
        # Read on the main thread. Workers only receive copied geometries;
        # they may enlarge the analysis without accessing a live provider.
        source_lines=[]
        for f in contours.getFeatures():
            head=float(f[field]);g=transformed(f.geometry(),contours.crs(),crs)
            if not np.isfinite(head) or g.isEmpty() or not g.isGeosValid():
                raise CalculationError(f'Contour {f.id()}: invalid geometry or head.')
            for part in g.asGeometryCollection() if g.isMultipart() else [g]:
                if part.type()==QgsWkbTypes.LineGeometry and part.length()>0:source_lines.append((QgsGeometry(part),head))
    return dict(lines=lines, source_lines=source_lines, analysis_rect=QgsRectangle(rect),
                wells=coords, identifiers=identifiers, settings=settings,
                crs=crs.authid(), crs_wkt=crs.toWkt(),
                source_names={"contours": contours.name(), "head_field": field,
                              "wells": wells.name(), "id_field": id_field},
                stop_geometry=stop_geometry, starts=starts, upstream_target=upstream_target,
                requested_settings=requested_settings)


def build_surface(data, canceled=lambda: False):
    settings, lines = data["settings"], data["lines"]
    if canceled():
        raise CalculationError("Calculation canceled.")
    sample_count = sum(max(1, int(np.ceil(line.length() / settings.sample))) + 1
                       for line, _ in lines)
    if sample_count > 60000:
        span = float(np.linalg.norm(np.ptp(data["wells"], axis=0)))
        if len(data["wells"]) > 1 and span > 2 * settings.radius:
            raise CalculationError(
                f"The {len(data['wells'])} wells in this run span {span / 1000:.1f} km and require "
                f"{sample_count:,} contour samples (limit: 60,000). Select wells from one local "
                "waterworks and choose Use: Selected wells. Calculate distant waterworks separately.")
        available = 60000 - 2 * len(lines)
        if available <= 0:
            raise CalculationError("Too many contour parts in the analysis area. Reduce the analysis radius.")
        spacing = float(np.ceil(sum(line.length() for line, _ in lines) / available * 10) / 10)
        if settings.stop == 'target' or settings.auto_extent:
            settings = replace(settings, sample=max(spacing*1.02,settings.sample))
            data['settings'] = settings
        else:
            raise CalculationError(
                f"The analysis requires {sample_count:,} contour samples (limit: 60,000). "
                f"Under Calculation settings, increase Contour sample spacing to at least {spacing:g} m "
                "or reduce Analysis radius. If using All wells, check that they belong to one local group.")
    index, stored = QgsSpatialIndex(), {}
    xy, heads = [], []
    for i, (line, head) in enumerate(lines):
        if canceled():
            raise CalculationError("Calculation canceled.")
        for j in index.intersects(line.boundingBox()):
            other, value = stored[j]
            if abs(head - value) > 1e-6 and line.intersects(other):
                raise CalculationError("Contours with different heads intersect. Correct the input data.")
        f = QgsFeature(i)
        f.setGeometry(line)
        index.addFeature(f)
        stored[i] = (line, head)
        n = max(1, int(np.ceil(line.length() / settings.sample)))
        for distance in np.linspace(0, line.length(), n + 1):
            p = line.interpolate(float(distance)).asPoint()
            xy.append([p.x(), p.y()])
            heads.append(head)
    if settings.interpolation == 'harmonic':
        from .core.harmonic import HarmonicSurface
        segments = [(np.array([[v.x(),v.y()] for v in line.vertices()]),head) for line,head in lines]
        return HarmonicSurface(xy, heads, settings.max_gap, settings.sample, segments, canceled)
    return HeadSurface(xy, heads, settings.max_gap, settings.sample)


def boundary_callback(boundary):
    if boundary is None:
        return None
    def stop(a, b):
        segment = geometry([a, b])
        hit = segment.intersection(boundary)
        if hit.isEmpty():
            return None
        vertices = list(hit.vertices())
        if not vertices:
            return None
        points = [np.array([p.x(), p.y()]) for p in vertices]
        return min(points, key=lambda p: np.linalg.norm(p - a))
    return stop


def calculate(data, polygon=True, canceled=lambda: False):
    """Expand an artificial analysis edge while real upstream contours continue."""
    working=dict(data);history=[];initial_info=None
    source=data.get('source_lines');rect=data.get('analysis_rect')
    enabled=source and rect is not None and data['settings'].auto_extent and data['settings'].stop in ('highest','target')
    for attempt in range(9):
        if canceled():raise CalculationError('Calculation canceled.')
        failure=None
        try:result=calculate_window(working,polygon,canceled)
        except CalculationError as exc:
            if not enabled:raise
            failure=exc
            # A failed closing contour can still have traceable inspection
            # paths. Use their actual endpoints to distinguish a clipped window.
            try:result=calculate_window(working,False,canceled)
            except CalculationError:raise failure
        meta=result['metadata'];rect=working.get('analysis_rect')
        edge_sides=set()
        if enabled:
            padding=max(working['settings'].sample*5,working['settings'].step*10,working['settings'].radius*.1)
            for path in meta['untrimmed_paths']:
                x,y=path['end_xy']
                if x-rect.xMinimum()<padding:edge_sides.add('west')
                if rect.xMaximum()-x<padding:edge_sides.add('east')
                if y-rect.yMinimum()<padding:edge_sides.add('south')
                if rect.yMaximum()-y<padding:edge_sides.add('north')
            # Only an edge with source contours beyond it warrants expansion.
            beyond=any(('west' in edge_sides and g.boundingBox().xMinimum()<rect.xMinimum()-1) or
                       ('east' in edge_sides and g.boundingBox().xMaximum()>rect.xMaximum()+1) or
                       ('south' in edge_sides and g.boundingBox().yMinimum()<rect.yMinimum()-1) or
                       ('north' in edge_sides and g.boundingBox().yMaximum()>rect.yMaximum()+1) for g,h in source)
        else:beyond=False
        if not beyond:
            if failure:raise failure
            meta['analysis_expansions']=history
            if initial_info is not None:meta['initialization']=initial_info
            if history:meta['warnings'].insert(0,f"Analysis expanded {len(history)} time(s) to {working['settings'].radius:g} m because upstream paths reached the calculation window edge. Starting points were kept fixed.")
            return result
        if attempt==8:
            raise CalculationError('Upstream contours continue beyond the calculation window. Automatic expansion reached its limit; no truncated catchment was returned. Mark an upstream target area or increase Analysis radius.')
        history.append(dict(radius_m=working['settings'].radius,closing_head_m=meta['upstream_closure']['head_m'],reason='paths approached calculation window edge'))
        if initial_info is None:initial_info=dict(meta['initialization'])
        new_rect=QgsRectangle(rect);new_rect.grow(working['settings'].radius)
        clip=QgsGeometry.fromRect(new_rect);lines=[]
        for g,h in source:
            if canceled():raise CalculationError('Calculation canceled.')
            if not g.boundingBox().intersects(new_rect):continue
            clipped=g.intersection(clip)
            for part in clipped.asGeometryCollection() if clipped.isMultipart() else [clipped]:
                if not part.isEmpty() and part.type()==QgsWkbTypes.LineGeometry and part.length()>0:lines.append((QgsGeometry(part),h))
        length=max(working['settings'].max_length,3*np.hypot(new_rect.width(),new_rect.height()))
        settings=replace(working['settings'],radius=working['settings'].radius*2,max_length=float(length),
                         max_steps=max(working['settings'].max_steps,int(length/working['settings'].step)+100))
        working=dict(working,lines=lines,analysis_rect=new_rect,settings=settings,starts=result['starts'].copy())


def calculate_window(data, polygon=True, canceled=lambda: False):
    settings = data["settings"]
    surface = build_surface(data, canceled)
    # Reuse one interpolated surface. Automatic width is a geometric proposal:
    # try smaller envelopes if the first one crosses unsupported coverage.
    if settings.width or data.get("starts") is not None:
        return calculate_on_surface(data, surface, polygon, canceled)
    try:
        _, proposed = surface.initial(data["wells"], check_starts=False)
    except CalculationError as exc:
        if 'coverage' in str(exc).lower():
            raise CalculationError("The chosen point is outside reliable contour coverage, or lies in an unresolved contour gap. "
                                   "Choose the contour layer covering this location and aquifer. More detailed local contours "
                                   "may be needed; choosing another point layer will not fill this data gap.") from exc
        raise
    failures = []
    for factor in (1.0, 0.75, 0.5, 0.35, 0.25, 0.15, 0.1):
        if canceled():
            raise CalculationError("Calculation canceled.")
        spread = proposed["well_span_m"]
        width = spread + (proposed["width_m"] - spread) * factor
        # Do not produce arbitrarily thin slivers below the sampling scale.
        if factor != 1.0 and width - spread < max(settings.sample, 2 * settings.step):
            continue
        try:
            result = calculate_on_surface(data, surface, polygon, canceled, width)
            result['metadata']['initialization']['automatic_width_factor'] = factor
            result['metadata']['initialization']['attempts'] = len(failures) + 1
            if failures:
                result['metadata']['warnings'].append(
                    f"Automatic geometric width reduced from {proposed['width_m']:.1f} to {width:.1f} m "
                    "to keep the boundary within usable contour coverage. Review or set a manual width.")
            return result
        except CalculationError as exc:
            if canceled():
                raise
            failures.append(str(exc))
    raise CalculationError(
        f"No supported {'polygon' if polygon else 'flow paths'} could be constructed for the chosen point(s). Tried {len(failures)} "
        f"geometric starting widths. First issue: {failures[0]} "
        "Use a contour plan covering this point and the same aquifer, or adjust the start points/upstream limit.")


def calculate_on_surface(data, surface, polygon=True, canceled=lambda: False, automatic_width=None):
    settings = data["settings"]
    starts, initialization = surface.initial(data["wells"], (automatic_width or settings.width) if data.get("starts") is None else 0,
                                              check_starts=data.get("starts") is None)
    if data.get("starts") is not None:
        starts = np.asarray(data["starts"], float)
        if starts.shape != (2, 2) or np.linalg.norm(starts[0] - starts[1]) < 0.1:
            raise CalculationError("Provide exactly two distinct start points.")
        initialization.update(initialization="user start points", width_m=float(np.linalg.norm(starts[0] - starts[1])))
    if data.get("stop_domain") is not None:
        domain = data["stop_domain"]
        if any(not domain.contains(QgsGeometry.fromPointXY(QgsPointXY(*p))) for p in np.vstack([starts, data["wells"]])):
            raise CalculationError("All wells and start points must be inside the selected map or polygon boundary.")
    boundary = data.get("stop_geometry")
    if settings.stop in ("map", "boundary") and (boundary is None or boundary.isEmpty()):
        raise CalculationError("The selected stopping boundary is empty.")
    stop = boundary_callback(boundary)
    if settings.interpolation == 'harmonic' and settings.stop in ('highest','target'):
        stop = boundary_callback(QgsGeometry.unaryUnion([line for line,head in data['lines'] if head == surface.hmax]))
    paths = [trace(surface, p, settings, stop, canceled) for p in starts]
    untrimmed_paths = paths
    cap, cap_head = None, None
    base=downstream_arc(starts,data['wells'],settings)

    def make_area(paths, cap=None):
        connection = np.asarray([paths[0]['points'][-1],paths[1]['points'][-1]]) if cap is None else cap
        ring = np.vstack([paths[0]["points"], connection[1:], paths[1]["points"][::-1][1:], base[1:]])
        return QgsGeometry.fromPolygonXY([[QgsPointXY(float(x), float(y)) for x, y in ring]]), ring

    unsupported = []
    for triangle in surface.tri.simplices[~surface.support]:
        vertices = surface.xy[triangle] + surface.origin
        unsupported.append(QgsGeometry.fromPolygonXY([[QgsPointXY(*p) for p in np.vstack([vertices, vertices[0]])]]))

    def supported_interior(area):
        return not any(area.boundingBox().intersects(gap.boundingBox()) and
                       area.intersection(gap).area() > settings.sample ** 2 * 0.01 for gap in unsupported)

    def supported_closure(paths, ring):
        # Check the actual perimeter, including the selected contour arc.
        line = geometry(ring)
        n = min(5000, max(10, int(line.length() / settings.sample)))
        samples = [line.interpolate(float(d)).asPoint() for d in np.linspace(0, line.length(), n)]
        if not np.isfinite(surface.values([[p.x(), p.y()] for p in samples])).all():
            return False
        return True

    well_geometries = [QgsGeometry.fromPointXY(QgsPointXY(float(x), float(y))) for x, y in data['wells']]

    def valid_candidate(paths, area, ring):
        # Evaluate all requirements when selecting an upstream cap; a longer
        # invalid cap must not prevent use of a shorter supported contour.
        if any(p['length_m'] < max(2 * settings.step, 1.0) for p in paths):
            return False
        if area.isEmpty() or not area.isGeosValid() or area.area() <= 1:
            return False
        if not all(area.contains(well) for well in well_geometries):
            return False
        return supported_closure(paths, ring) and supported_interior(area)

    area, ring = make_area(paths)
    if polygon and settings.stop in ("highest", "target"):
        # Connect exact path/contour intersections using the intervening
        # original contour vertices, rather than a straight chord or a guessed
        # equal-head endpoint on the interpolated surface.
        # Test actual vector intersections. A bilinear interpolated head at an
        # exact contour hit can differ slightly from its observed head; that
        # must not discard the highest contour that both paths really reached.
        candidates = surface.levels
        for level in candidates[::-1]:
            if canceled():
                raise CalculationError("Calculation canceled.")
            if level <= max(p["heads"][0] for p in untrimmed_paths):
                break
            for trimmed, arc in contour_caps(untrimmed_paths, data['lines'], level, max(2*settings.step,1.0)):
                if settings.stop == 'target' and not all(data['upstream_target'].intersects(
                        QgsGeometry.fromPointXY(QgsPointXY(*path['points'][-1]))) for path in trimmed):
                    continue
                candidate_area, candidate_ring = make_area(trimmed,arc)
                if valid_candidate(trimmed,candidate_area,candidate_ring):
                    paths,cap,cap_head = trimmed,arc,float(level)
                    area,ring = candidate_area,candidate_ring
                    break
            if cap is not None:
                break
        if cap is None:
            if settings.stop == 'target':
                raise CalculationError('Both upstream paths must reach the marked target area and share a closing contour there. '
                                       'No polygon was created. The target may lie outside their upstream flow direction, '
                                       'or contour coverage may be insufficient. Review the target, starting width and support gap.')
            raise CalculationError('No higher connected contour can close both paths within reliable coverage. '
                                   'Choose a custom upstream boundary, review the starts, or use more detailed contours.')
    line_geometries = [geometry(p["points"]) for p in paths]
    for line in line_geometries:
        if not line.isSimple():
            raise CalculationError("A flow path self-intersects. Reduce the step size or review contours.")
    if line_geometries[0].intersects(line_geometries[1]):
        raise CalculationError("Lateral paths intersect. Adjust starting width or points.")
    if polygon:
        if settings.stop in ('map','boundary') and not all('boundary' in p['stop_reason'].lower() for p in paths):
            raise CalculationError('Both paths must reach the selected stopping boundary. No shortened polygon was created. '
                                   'Increase contour coverage or the analysis radius, or revise the boundary. Calculate flow paths to inspect where they stop.')
        if any(p["length_m"] < max(2 * settings.step, 1.0) for p in paths):
            raise CalculationError("The common supported upstream limit is too close to the starts.")
        if area.isEmpty() or not area.isGeosValid() or area.area() <= 1:
            raise CalculationError("Boundary construction failed: invalid or empty polygon. No polygon created.")
        if not all(area.contains(QgsGeometry.fromPointXY(QgsPointXY(float(x), float(y)))) for x, y in data["wells"]):
            raise CalculationError("Paths do not enclose all wells. Increase width, move starts or split the group.")
        # Validate the closure and interior against unsupported gaps too.
        check = area.boundary() if hasattr(area, "boundary") else geometry(ring)
        n = min(5000, max(10, int(check.length() / settings.sample)))
        samples = [check.interpolate(float(d)).asPoint() for d in np.linspace(0, check.length(), n)]
        if not np.isfinite(surface.values([[p.x(), p.y()] for p in samples])).all():
            raise CalculationError("The closing boundary crosses unsupported contour coverage. Adjust the stopping boundary. " + paths[0]["stop_reason"])
        # Reject enclosed unsupported triangles, not just unsupported edges.
        if not supported_interior(area):
            raise CalculationError("Polygon interior crosses a gap in supported contour coverage. Select a closer stopping boundary.")
        area = area.forcePolygonCounterClockwise()
    notes = [f"Final flow-path lengths: {paths[0]['length_m']:.0f} m / {paths[1]['length_m']:.0f} m.",
             "Geometric preliminary proposal; not a validated groundwater catchment.",
             "Starting width is a geometric assumption, not pumping-related capture width.",
             "Isotropic steady head geometry assumed; no pumping/recharge/travel-time model.",
             "Termination does not identify or verify a groundwater divide."]
    notes.extend(p["stop_reason"] for p in paths)
    if cap_head is not None:
        notes.insert(0,f'Upstream closure follows the actual {cap_head:g} m contour. This is an artificial limit, not a verified divide.')
        for i,path in enumerate(untrimmed_paths):
            notes.append(f"Untrimmed path {i+1}: {path['length_m']:.0f} m, head {path['heads'][-1]:.2f} m; {path['stop_reason']}")
    visited = surface.tri.find_simplex(np.vstack([p['points'] for p in paths]) - surface.origin)
    if settings.interpolation == 'cubic' and np.any(surface.neighborhood_support[np.maximum(visited, 0)]):
        notes.append("At curved contours, slopes use nearby contour triangles where three triangle vertices share a head value. Inspect these interpolated flow paths.")
    if settings.stop in ("map", "boundary") and not all("boundary" in p["stop_reason"].lower() for p in paths):
        notes.append("The requested stopping boundary was not reached by both paths; inspect the recorded termination reasons.")
    if any("coverage" in p["stop_reason"].lower() for p in paths):
        notes.append("A path stopped at unreliable coverage before the requested upstream condition.")
    if settings.stop == 'target':
        reached=all(data['upstream_target'].intersects(QgsGeometry.fromPointXY(QgsPointXY(*path['points'][-1]))) for path in paths)
        notes.insert(0,'Both boundary endpoints reached the marked upstream target area.' if reached else
                     'The upstream target was not reached by both paths. These are inspection paths, not a completed catchment.')
    if hasattr(surface,'interpolation_info'):
        notes.append('Contour-constrained harmonic interpolation; no pumping or recharge model.')
    if not settings.max_gap:
        notes.append('No contour-spacing cutoff applied. Widely spaced contours are interpolated; local detail is limited by the source data.')
    if data.get('requested_settings',{}).get('radius',settings.radius)!=settings.radius:
        notes.append(f'Analysis radius automatically expanded to {settings.radius:g} m to include surrounding contours.')
    if settings.downstream_shape=='rounded':notes.append('Rounded downstream closure is a geometric assumption, not a pumping-based capture boundary.')
    meta = dict(version="0.1.12", run_id=str(uuid.uuid4()), created_utc=datetime.now(timezone.utc).isoformat(),
                crs=data["crs"], crs_wkt=data["crs_wkt"], settings=asdict(settings),
                requested_settings=data.get('requested_settings',asdict(settings)),
                interpolation=getattr(surface,'interpolation_info',{'method':'Clough-Tocher cubic'}),
                target_wkt=data['upstream_target'].asWkt() if data.get('upstream_target') is not None else None,
                initialization=initialization, wells=[dict(**ident, xy=pt.tolist()) for ident, pt in zip(data["identifiers"], data["wells"])],
                sources=data["source_names"], warnings=notes,
                paths=[{k: p[k] for k in ("length_m", "stop_reason")} for p in paths],
                upstream_closure=dict(method='original contour arc' if cap is not None else 'straight connection', head_m=cap_head),
                downstream_closure=dict(method=settings.downstream_shape,depth_m=settings.arc_depth or (np.linalg.norm(starts[1]-starts[0])*.5 if settings.downstream_shape=='rounded' else 0)),
                untrimmed_paths=[dict(length_m=p['length_m'],end_head_m=float(p['heads'][-1]),end_xy=p['points'][-1].tolist(),stop_reason=p['stop_reason']) for p in untrimmed_paths],
                head_range=[surface.hmin, surface.hmax], sample_count=len(surface.xy))
    return dict(polygon=area if polygon else None, paths=line_geometries,
                path_data=paths, starts=starts, metadata=meta,
                upstream_boundary=geometry(cap) if cap is not None else None,downstream_boundary=geometry(base))


def memory_layer(kind, name, crs):
    layer = QgsVectorLayer(kind, name, "memory")
    layer.setCrs(crs)
    layer.dataProvider().addAttributes([
        QgsField("run_id", QVariant.String, len=36),
        QgsField("label", QVariant.String, len=100),
        QgsField("well_ids", QVariant.String, len=254),
        QgsField("width_m", QVariant.Double),
        QgsField("created", QVariant.String, len=40),
        QgsField("status", QVariant.String, len=40),
        QgsField("stop", QVariant.String, len=254),
    ])
    layer.updateFields()
    return layer


def result_layers(result):
    meta = result["metadata"]
    crs = QgsCoordinateReferenceSystem.fromWkt(meta["crs_wkt"])
    layers = {}
    for key, kind, name, geoms in (
        ("paths", "LineString", "Upstream flow paths", result["paths"]),
        ("cap", "LineString", "Upstream contour closure", [result['upstream_boundary']] if result.get('upstream_boundary') else []),
        ("base", "LineString", "Downstream closure (geometric assumption)", [result['downstream_boundary']] if result.get('downstream_boundary') else []),
        ("starts", "Point", "Boundary start points", [QgsGeometry.fromPointXY(QgsPointXY(*p)) for p in result["starts"]]),
        ("original", "Polygon", "Preliminary catchment — original", [result["polygon"]] if result["polygon"] else []),
        ("edited", "Polygon", "Preliminary catchment proposal", [result["polygon"]] if result["polygon"] else []),
        ("target", "Polygon", "Upstream target area", [QgsGeometry.fromWkt(meta['target_wkt'])] if meta.get('target_wkt') else []),
    ):
        if not geoms:
            continue
        layer = memory_layer(kind, name, crs)
        feats = []
        for i, g in enumerate(geoms):
            f = QgsFeature(layer.fields())
            f.setGeometry(g)
            stop = meta["paths"][i]["stop_reason"] if key == "paths" else " | ".join(p["stop_reason"] for p in meta["paths"])
            f.setAttributes([meta["run_id"], name, ", ".join(w["id"] for w in meta["wells"])[:254],
                             meta["initialization"]["width_m"], meta["created_utc"], "preliminary", stop[:254]])
            feats.append(f)
        layer.dataProvider().addFeatures(feats)
        layer.updateExtents()
        layer.setCustomProperty("draw_catchments/metadata", json.dumps(meta))
        layer.setCustomProperty("draw_catchments/role", key)
        if key in ("original", "paths", "cap", "base"):
            layer.setReadOnly(True)
        layers[key] = layer
    return layers


def export_result(path, layer, companions=None):
    """Never overwrite silently. GeoJSON uses RFC7946 WGS84; source CRS in sidecar."""
    path = Path(path)
    sidecar = path.with_suffix(path.suffix + ".drawcatchments.json")
    if path.exists() or sidecar.exists():
        raise CalculationError("Output already exists. Choose a new filename to preserve earlier results.")
    drivers = {".gpkg": "GPKG", ".shp": "ESRI Shapefile", ".geojson": "GeoJSON"}
    if path.suffix.lower() not in drivers:
        raise CalculationError("Use .gpkg, .shp or .geojson.")
    features = list(layer.getFeatures())
    if not features or any(f.geometry().isEmpty() or not f.geometry().isGeosValid() for f in features):
        raise CalculationError("Cannot export an empty or invalid polygon. Finish corrections first.")
    raw = layer.customProperty("draw_catchments/metadata", "{}")
    meta = json.loads(raw)
    for well in meta.get("wells", []):
        point = QgsGeometry.fromPointXY(QgsPointXY(*well["xy"]))
        source = QgsCoordinateReferenceSystem.fromWkt(meta["crs_wkt"])
        point = transformed(point, source, layer.crs())
        if not any(f.geometry().contains(point) for f in features):
            raise CalculationError("The edited polygon excludes a selected well. Correct the geometry before export.")
    meta["exported_utc"] = datetime.now(timezone.utc).isoformat()
    meta["export_layer"] = layer.name()
    meta["export_crs"] = "EPSG:4326" if path.suffix.lower() == ".geojson" else layer.crs().authid()
    if companions and companions.get("original"):
        originals = list(companions["original"].getFeatures())
        meta["geometry_edited"] = len(originals) != len(features) or any(
            not a.geometry().equals(b.geometry()) for a, b in zip(originals, features))
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = drivers[path.suffix.lower()]
    options.fileEncoding = "UTF-8"
    options.layerName = "catchment"
    if options.driverName == "GeoJSON":
        options.ct = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        options.layerOptions = ["RFC7946=YES"]
    status = QgsVectorFileWriter.writeAsVectorFormatV3(layer, str(path), QgsProject.instance().transformContext(), options)
    if status[0] != QgsVectorFileWriter.NoError:
        raise CalculationError(str(status))
    if options.driverName == "GPKG" and companions:
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        for key in ("original", "paths", "starts", "cap", "base", "target"):
            if key in companions:
                options.layerName = key
                status = QgsVectorFileWriter.writeAsVectorFormatV3(companions[key], str(path), QgsProject.instance().transformContext(), options)
                if status[0] != QgsVectorFileWriter.NoError:
                    raise CalculationError(f"Companion export failed: {status}")
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
