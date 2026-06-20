"""Modelos de datos para títulos mineros de la ANM (Colombia)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class TituloMinero:
    """Representación de un título minero vigente en Colombia."""

    codigo_exp: str
    estado_exp: str | None = None
    modalidade: str | None = None
    etapa: str | None = None
    minerales: str | None = None
    municipios: str | None = None
    departamento: str | None = None
    solicitante: str | None = None
    grupo_trab: str | None = None
    area_ha: float | None = None
    fecha_insc: datetime | None = None
    fecha_term: datetime | None = None
    tipo_explo: str | None = None
    capaminera: str | None = None
    producto: int | None = None
    fid: int | None = None
    geometry: dict[str, Any] | None = None
    spatial_reference: dict[str, Any] | None = None

    # Metadatos adicionales derivados de la geometría.
    shape_area: float | None = None
    shape_length: float | None = None

    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_feature(cls, feature: dict[str, Any]) -> "TituloMinero":
        """Construye un TituloMinero desde una feature de ArcGIS REST.

        ``feature`` debe seguir el formato devuelto por el endpoint ``query``
        del FeatureServer: ``{"attributes": {...}, "geometry": {...}}``.
        """
        attrs: dict[str, Any] = feature.get("attributes", {}) or {}

        def _parse_date(value: Any) -> datetime | None:
            if value is None or value == "":
                return None
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
                except (ValueError, OSError, OverflowError):
                    return None
            if isinstance(value, str):
                for fmt in ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            return None

        return cls(
            codigo_exp=attrs.get("codigo_exp") or "",
            estado_exp=attrs.get("estado_exp"),
            modalidade=attrs.get("modalidade"),
            etapa=attrs.get("etapa"),
            minerales=attrs.get("minerales"),
            municipios=attrs.get("municipios"),
            departamento=attrs.get("departamento"),
            solicitante=attrs.get("solicitante"),
            grupo_trab=attrs.get("grupo_trab"),
            area_ha=attrs.get("area_ha"),
            fecha_insc=_parse_date(attrs.get("fecha_insc")),
            fecha_term=_parse_date(attrs.get("fecha_term")),
            tipo_explo=attrs.get("tipo_explo"),
            capaminera=attrs.get("capaminera"),
            producto=attrs.get("producto"),
            fid=attrs.get("fid"),
            geometry=feature.get("geometry"),
            shape_area=attrs.get("SHAPE__Area"),
            shape_length=attrs.get("SHAPE__Length"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializa el título a un diccionario plano apto para JSON."""

        def _fmt(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            return value

        data = asdict(self)
        for key in ("fecha_insc", "fecha_term"):
            data[key] = _fmt(data.get(key))
        return data
