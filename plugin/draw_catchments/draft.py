"""Portable manual drawing snapshots. JSON only; no executable serialization.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np
from qgis.PyQt.QtCore import QSaveFile,QIODevice
from qgis.core import QgsGeometry, QgsCoordinateReferenceSystem
from .core.flow import Settings, CalculationError


def save_draft(path, session):
    data = session.data
    snapshot = {key: data[key] for key in ('wells', 'identifiers', 'crs', 'crs_wkt', 'source_names')}
    snapshot.update(settings=asdict(data['settings']), requested_settings=data.get('requested_settings', {}),
                    lines=[dict(wkt=g.asWkt(), head=h) for g, h in data['lines']],
                    upstream_target=data['upstream_target'].asWkt() if data.get('upstream_target') else None)
    payload = dict(format='Draw Catchments manual draft', schema=1, data=snapshot,
                   starts=session.starts, initialization=session.initialization,
                   start_heads=[h[0] for h in session.heads], steps=session.steps, side=session.side)
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                     default=lambda v: v.tolist() if isinstance(v, np.ndarray) else float(v))
    # QGIS/Qt handles platform-specific atomic replacement of an existing draft.
    stream=QSaveFile(str(path))
    if not stream.open(QIODevice.WriteOnly):raise CalculationError(stream.errorString())
    encoded=raw.encode('utf-8')
    if stream.write(encoded)!=len(encoded):
        stream.cancelWriting();raise CalculationError(stream.errorString())
    if not stream.commit():raise CalculationError(stream.errorString())


def load_draft(path):
    from .manual import ManualSession
    try:
        path = Path(path)
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError('Draft exceeds the 100 MB limit.')
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload['format'] != 'Draw Catchments manual draft' or payload['schema'] != 1:
            raise ValueError('Unsupported drawing format.')
        data = payload['data']; data['settings'] = Settings(**data['settings']); data['settings'].validate()
        if not QgsCoordinateReferenceSystem.fromWkt(data['crs_wkt']).isValid():
            raise ValueError('Invalid draft CRS.')
        data['wells'] = np.asarray(data['wells'], float)
        if data['wells'].ndim != 2 or data['wells'].shape[1] != 2 or not np.isfinite(data['wells']).all():
            raise ValueError('Invalid draft points.')
        data['lines'] = [(QgsGeometry.fromWkt(item['wkt']), float(item['head'])) for item in data['lines']]
        if not data['lines'] or any(g.isEmpty() or g.type()!=1 or not g.isGeosValid() or not np.isfinite(h) for g,h in data['lines']):
            raise ValueError('Invalid draft contours.')
        data['upstream_target'] = QgsGeometry.fromWkt(data['upstream_target']) if data.get('upstream_target') else None
        if data['settings'].stop == 'target' and (not data['upstream_target'] or not data['upstream_target'].isGeosValid()):
            raise ValueError('Missing or invalid draft target.')
        session = ManualSession.__new__(ManualSession)
        session.data=data; session.starts=np.asarray(payload['starts'],float); session.initialization=payload['initialization']
        if session.starts.shape!=(2,2) or not np.isfinite(session.starts).all():
            raise ValueError('Invalid draft starting points.')
        session.points=[[p.copy()] for p in session.starts]
        heads=np.asarray(payload['start_heads'],float)
        if heads.shape!=(2,) or not np.isfinite(heads).all(): raise ValueError('Invalid initial heads.')
        session.heads=[[float(h)] for h in heads];session.steps=[[],[]]
        if len(payload['steps'])!=2:raise ValueError('A draft must contain exactly two sides.')
        for side,steps in enumerate(payload['steps']):
            session.side=side
            for stored in steps:
                step=dict(stored,points=np.asarray(stored['points'],float),point=np.asarray(stored['point'],float))
                if (step['points'].ndim!=2 or step['points'].shape[1]!=2 or len(step['points'])<2
                    or not np.isfinite(step['points']).all() or not np.isfinite(step['head'])
                    or step['head']<=session.heads[side][-1]
                    or not np.allclose(step['points'][0],session.points[side][-1],rtol=0,atol=1e-6)):
                    raise ValueError('Invalid draft step.')
                # Retain older bent drafts so they can be inspected and undone.
                # Finishing requires straight whole segments; angles are
                # measured afresh, never trusted from saved metadata.
                session.append(step,restore_legacy=True)
        session.side=int(payload['side'])
        if session.side not in (0,1):raise ValueError('Invalid active side.')
        return session
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise CalculationError(f'Cannot load drawing draft: {exc}') from exc
