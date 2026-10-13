"""Draw Catchments — preliminary groundwater catchment proposals. GPL-3.0-or-later."""


def classFactory(iface):
    from .plugin import DrawCatchments
    return DrawCatchments(iface)
