"""Utilidades de geometría: conversión de geometría ArcGIS REST a GeoJSON."""

from __future__ import annotations

from typing import Any


def _rings_to_polygon_coords(rings: list[list[list[float]]]) -> list[list[list[float]]]:
    """Normaliza los anillos de un polígono de ArcGIS a coordenadas GeoJSON."""
    return [[[float(x), float(y)] for x, y in ring] for ring in rings]


def arcgis_feature_to_geojson(titulo) -> dict[str, Any]:
    """Convierte un :class:`~mintrack.models.TituloMinero` a un Feature GeoJSON.

    Si el título no tiene geometría, se devuelve un Feature con geometría
    ``None`` (válido para propósitos de atributos).
    """
    from .models import TituloMinero  # evita import circular en anotaciones

    if not isinstance(titulo, TituloMinero):
        raise TypeError("Se requiere una instancia de TituloMinero")

    geom = titulo.geometry
    geojson_geom: dict[str, Any] | None = None
    if geom:
        if "rings" in geom:
            geojson_geom = {
                "type": "Polygon",
                "coordinates": _rings_to_polygon_coords(geom["rings"]),
            }
        elif "x" in geom and "y" in geom:
            geojson_geom = {
                "type": "Point",
                "coordinates": [float(geom["x"]), float(geom["y"])],
            }
        elif "paths" in geom:
            geojson_geom = {
                "type": "MultiLineString",
                "coordinates": _rings_to_polygon_coords(geom["paths"]),
            }

    props = titulo.to_dict()
    for k in ("geometry", "spatial_reference"):
        props.pop(k, None)

    return {
        "type": "Feature",
        "geometry": geojson_geom,
        "properties": props,
    }


def titulos_to_feature_collection(titulos: list) -> dict[str, Any]:
    """Agrupa una lista de títulos en un FeatureCollection GeoJSON."""
    return {
        "type": "FeatureCollection",
        "features": [arcgis_feature_to_geojson(t) for t in titulos],
    }
