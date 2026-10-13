"""Contour interpolation and upstream tracing. Distances must be in metres.

No pumping, recharge, travel time or capture-width calculation is performed.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from dataclasses import dataclass
import warnings

import numpy as np
from scipy.interpolate import CloughTocher2DInterpolator
from scipy.spatial import Delaunay, cKDTree, QhullError
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


class CalculationError(ValueError):
    """Input or construction cannot support a defensible geometric proposal."""


@dataclass
class Settings:
    sample: float = 30.0
    step: float = 20.0
    radius: float = 5000.0
    max_gap: float = 2000.0
    max_length: float = 10000.0
    width: float = 0.0
    stop: str = "highest"
    max_steps: int = 5000
    interpolation: str = "cubic"
    auto_extent: bool = False
    downstream_shape: str = 'straight'
    arc_depth: float = 0.0
    manual_angle_mode: str = 'best'

    def validate(self):
        if self.manual_angle_mode not in ('best', 'strict'):
            raise CalculationError('Unknown manual connection mode.')
        if self.downstream_shape not in ('straight','rounded'):
            raise CalculationError('Unknown downstream closure shape.')
        if not np.isfinite(self.arc_depth) or self.arc_depth<0:
            raise CalculationError('Arc depth must be positive, or 0 for half the starting width.')
        for key in ("sample", "step", "radius", "max_length"):
            if not np.isfinite(getattr(self, key)) or getattr(self, key) <= 0:
                raise CalculationError(f"{key}: a positive finite value is required.")
        if not np.isfinite(self.max_gap) or self.max_gap < 0:
            raise CalculationError('Maximum support gap must be nonnegative; 0 disables the distance cutoff.')
        if not np.isfinite(self.width) or self.width < 0:
            raise CalculationError("Width must be positive or automatic (0).")
        if self.stop not in ("highest", "map", "boundary", "target"):
            raise CalculationError("Unknown stopping condition.")
        if self.interpolation not in ('cubic','harmonic'):
            raise CalculationError('Unknown interpolation method.')


class HeadSurface:
    """C1 Clough-Tocher surface with conservative support and overshoot checks."""

    def __init__(self, xy, heads, max_gap, sample, build_cubic=True):
        xy, heads = np.asarray(xy, float), np.asarray(heads, float)
        if xy.ndim != 2 or xy.shape[1] != 2 or len(xy) != len(heads):
            raise CalculationError("Invalid interpolation samples.")
        if not np.isfinite(xy).all() or not np.isfinite(heads).all():
            raise CalculationError("Coordinates or heads contain invalid values.")
        self.origin = np.mean(xy, axis=0)
        xy = xy - self.origin
        # Coincident contour samples with different heads are inconsistent.
        rounded = np.round(xy, 5)
        _, first, inverse = np.unique(rounded, axis=0, return_index=True, return_inverse=True)
        low = np.full(len(first), np.inf)
        high = np.full(len(first), -np.inf)
        np.minimum.at(low, inverse, heads)
        np.maximum.at(high, inverse, heads)
        if np.any(high - low > 1e-5):
            raise CalculationError("Conflicting head values at the same location.")
        self.xy, self.heads = xy[first], heads[first]
        self.levels = np.unique(self.heads)
        if len(self.levels) < 3 or len(first) < 9:
            raise CalculationError("At least three distinct contour heads are required.")
        try:
            self.tri = Delaunay(self.xy)
            self.interp = None
            if build_cubic:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    self.interp = CloughTocher2DInterpolator(self.tri, self.heads, maxiter=800)
                if caught:
                    raise CalculationError("Head interpolation did not converge; review the contour data.")
        except QhullError as exc:
            raise CalculationError("Contours do not provide two-dimensional coverage.") from exc
        vertices = self.xy[self.tri.simplices]
        edges = np.stack([np.linalg.norm(vertices[:, i] - vertices[:, j], axis=1)
                          for i, j in ((0, 1), (1, 2), (2, 0))])
        close = edges.max(axis=0) <= (max_gap if max_gap else np.inf)
        triangle_heads = self.heads[self.tri.simplices]
        self.lower = triangle_heads.min(axis=1)
        self.upper = triangle_heads.max(axis=1)
        flat = self.upper - self.lower <= 1e-8
        # A curved contour can generate a whole connected fan of same-head
        # triangles. A fixed number of adjacency rings depends on sample spacing
        # and creates artificial barriers just above a contour. Bound each fan
        # by the observed heads on its adjacent non-flat triangles instead.
        # Never propagate across an oversized triangle or another contour head.
        neighbors = self.tri.neighbors
        flat_ids = np.flatnonzero(flat & close)
        if len(flat_ids):
            lookup = np.full(len(flat), -1, dtype=int)
            lookup[flat_ids] = np.arange(len(flat_ids))
            src = np.repeat(flat_ids, 3)
            dst = neighbors[flat_ids].ravel()
            ok = dst >= 0
            src, dst = src[ok], dst[ok]
            links = flat[dst] & close[dst] & (np.abs(self.lower[src]-self.lower[dst]) < 1e-8)
            graph = coo_matrix((np.ones(np.sum(links)), (lookup[src[links]], lookup[dst[links]])),
                               shape=(len(flat_ids),len(flat_ids))).tocsr()
            count, labels = connected_components(graph, directed=False)
            lower = np.full(count, np.inf); upper = np.full(count, -np.inf)
            np.minimum.at(lower, labels, self.lower[flat_ids])
            np.maximum.at(upper, labels, self.upper[flat_ids])
            ok = close[dst] & ~flat[dst]
            border_labels = labels[lookup[src[ok]]]
            np.minimum.at(lower, border_labels, self.lower[dst[ok]])
            np.maximum.at(upper, border_labels, self.upper[dst[ok]])
            self.lower[flat_ids], self.upper[flat_ids] = lower[labels], upper[labels]
        self.support = close & (self.upper - self.lower > 1e-8)
        self.neighborhood_support = flat & self.support
        self.tree = cKDTree(self.xy)
        self.eps = max(0.02, min(sample * 0.03, 1.0))
        self.dh = float(np.median(np.diff(self.levels)))
        self.hmin, self.hmax = float(self.levels[0]), float(self.levels[-1])

    def values(self, points):
        points = np.asarray(points, float)
        q = points - self.origin
        simplex = self.tri.find_simplex(q)
        valid = (simplex >= 0) & self.support[np.maximum(simplex, 0)]
        values = np.asarray(self.interp(q)).reshape(-1)
        # Cubic overshoot is allowed only within 5% of one contour interval.
        valid &= (values >= self.lower[np.maximum(simplex, 0)] - 0.05 * self.dh)
        valid &= (values <= self.upper[np.maximum(simplex, 0)] + 0.05 * self.dh)
        return np.where(valid, values, np.nan)

    def at(self, point):
        e = self.eps
        q = np.asarray(point, float)
        v = self.values([q, q + [e, 0], q - [e, 0], q + [0, e], q - [0, e]])
        if not np.isfinite(v).all():
            raise CalculationError("Reliable contour coverage limit reached.")
        gradient = np.array([v[1] - v[2], v[3] - v[4]]) / (2 * e)
        magnitude = np.linalg.norm(gradient)
        if magnitude < 1e-8:
            raise CalculationError("Gradient too small; this is not a verified groundwater divide.")
        return float(v[0]), gradient

    def initial(self, wells, width=0.0, method="spacing", check_starts=True):
        wells = np.asarray(wells, float)
        center = wells.mean(axis=0)
        _, g = self.at(center)
        direction = g / np.linalg.norm(g)
        normal = np.array([-direction[1], direction[0]])
        gradients = [self.at(p)[1] for p in wells]
        if any(np.dot(v / np.linalg.norm(v), direction) < 0.5 for v in gradients):
            raise CalculationError("Well group has divergent flow directions; calculate separate groups.")
        spacing = self.dh / np.linalg.norm(g)
        along, across = (wells - center) @ direction, (wells - center) @ normal
        spread = float(np.ptp(across))
        # Explicit geometric hypothesis, NOT hydraulic capture width.
        margin = 0.25 * spacing if method == "spacing" else 100.0
        proposed = spread + 2 * margin
        width = float(width or proposed)
        if width <= spread + 0.1:
            raise CalculationError(f"Width must exceed the cross-flow well span ({spread:.1f} m).")
        back = max(5.0, min(spacing * 0.10, width * 0.25))
        base = center + (float(along.min()) - back) * direction
        middle = float((across.min() + across.max()) / 2)
        starts = [base + (middle - width / 2) * normal,
                  base + (middle + width / 2) * normal]
        if check_starts:
            for p in starts:
                self.at(p)
        return np.array(starts), {
            "width_m": width, "suggested_width_m": proposed,
            "contour_spacing_m": float(spacing), "well_span_m": spread,
            "downstream_offset_m": back, "initialization": method,
            "width_basis": "geometric assumption; not hydraulic capture width",
        }


def trace(surface, start, settings, stop_segment=None, canceled=lambda: False):
    """Adaptive midpoint integration of normalized positive head gradient.

    stop_segment(a,b) returns an exact boundary hit or None. Monotone head
    ascent, turn control and later GEOS topology validation prevent bad rings.
    """
    p = np.asarray(start, float)
    h, grad = surface.at(p)
    points, heads = [p.copy()], [h]
    distance, previous = 0.0, None
    reason = "Step limit (artificial)"
    for _ in range(settings.max_steps):
        if canceled():
            raise CalculationError("Calculation canceled.")
        if settings.stop == "highest" and stop_segment is None and h >= surface.hmax - surface.dh * 0.02:
            reason = "Highest usable contour (artificial; not a divide)"
            break
        remaining = settings.max_length - distance
        if remaining < settings.step * 1e-4:
            reason = "Maximum path length (artificial)"
            break
        ds = min(settings.step, remaining)
        direction = grad / np.linalg.norm(grad)
        accepted = False
        error = "Stagnation / direction change; not a verified divide"
        for _attempt in range(12):
            try:
                _, gm = surface.at(p + direction * ds * 0.5)
                dm = gm / np.linalg.norm(gm)
                candidate = p + ds * dm
                hit = stop_segment(p, candidate) if stop_segment else None
                if hit is not None:
                    candidate = np.asarray(hit, float)
                nh, ng = surface.at(candidate)
                if nh <= h + 1e-10 or np.dot(dm, direction) < 0.7:
                    ds *= 0.5
                    continue
                # Check the end-of-step gradient BEFORE accepting the step.
                # The old comparison with the previous midpoint could halt on
                # the following iteration after already overshooting a bend.
                if np.dot(ng / np.linalg.norm(ng), dm) < 0.7:
                    ds *= 0.5
                    continue
                accepted = True
                break
            except CalculationError as exc:
                error = str(exc)
                ds *= 0.5
        if not accepted or ds < settings.step * 1e-4:
            reason = error
            break
        distance += float(np.linalg.norm(candidate - p))
        p, h, grad, previous = candidate, nh, ng, dm
        points.append(p.copy())
        heads.append(h)
        if hit is not None:
            reason = ("Highest observed contour reached (not a verified divide)" if settings.stop in ('highest','target') else
                      "Custom boundary reached" if settings.stop == "boundary" else "Map boundary (artificial)")
            break
    if distance < max(2 * settings.step, 1.0):
        raise CalculationError("Flow path too short. Review starts, width or contour coverage.")
    return {"points": np.array(points), "heads": np.array(heads),
            "length_m": distance, "stop_reason": reason}
