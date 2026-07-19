"""Modelos de datos para títulos mineros de la ANM (Colombia)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class TituloMinero:
    """Representación completa de un título/expediente minero en Colombia.

    Conserva los nombres usados originalmente por MinTrack para mantener estable
    la interfaz del bot, la CLI y los snapshots. Las propiedades con prefijo
    ``fecha_*`` se convierten a :class:`~datetime.datetime`.
    """

    # Identificación
    codigo_exp: str = ""
    tenure_id: str | None = None
    objectid: float | None = None
    fid: int | None = None

    # Estado y clasificación
    titulo_est: str | None = None          # TITULO_ESTADO ("Activo", ...)
    tenure_sta: str | None = None          # TENURE_STATUS_CODE (A, ...)
    tenure_s01: str | None = None          # TENURE_STAGE_CODE (EXPT, ...)
    title_type: str | None = None          # TITLE_TYPE_CODE (CC, ...)
    mining_cla: str | None = None          # MINING_CLASSIFICATION_CODE (PEQ, ...)
    clasificac: str | None = None          # CLASIFICACION_MINERIA ("Pequeña")
    etapa: str | None = None               # ETAPA ("Explotación", "Exploración")
    modalidad: str | None = None          # MODALIDAD
    publicado_: str | None = None          # PUBLICADO_EN_RUCOM (S/N)
    active_ten: str | None = None          # ACTIVE_TENURE_STATUS_IND (Y/N)
    active_app: str | None = None         # ACTIVE_APPLICATION_STATUS_IND (Y/N)
    tipo_termi: str | None = None         # TIPO_TERMINACION
    terminatio: str | None = None         # TERMINATION_TYPE_CODE

    # Ubicación y área
    departamen: str | None = None         # DEPARTAMENTOS
    municipios: str | None = None         # MUNICIPIOS
    area_ha: float | None = None          # AREA_HA
    centroid_c: str | None = None         # CENTROID_COORDINATE ("lon,lat")
    par: str | None = None                # PAR ("PAR IBAGUE")

    # Titulares / solicitantes y minerales
    solicitant: str | None = None        # SOLICITANTES_O_TITULARES
    minerales: str | None = None         # MINERALES
    minerales_: str | None = None         # MINERALES_INACTIVOS

    # Fechas (timestamps epoch ms -> datetime)
    fecha_de_s: datetime | None = None    # FECHA_DE_SOLICITUD
    fecha_de_e: datetime | None = None    # FECHA_DE_EXPEDICION
    fecha_de_a: datetime | None = None    # FECHA_DE_ANIVERSARIO
    fecha_de01: datetime | None = None    # FECHA_DE_EXPIRACION

    # Geometría
    geometry: dict[str, Any] | None = None
    shape_area: float | None = None       # SHAPE__Area
    shape_length: float | None = None     # SHAPE__Length

    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_feature(cls, feature: dict[str, Any]) -> "TituloMinero":
        """Construye un TituloMinero desde una feature de ArcGIS REST.

        ``feature`` debe seguir el formato devuelto por ``query`` del
        FeatureServer: ``{"attributes": {...}, "geometry": {...}}``.
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
                for fmt in (
                    "%Y/%m/%d %H:%M:%S.%f",
                    "%Y/%m/%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                ):
                    try:
                        return datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            return None

        def _trim(v: Any) -> Any:
            if isinstance(v, str):
                v = v.strip()
                return v if v != "" else None
            return v

        return cls(
            codigo_exp=attrs.get("codigo_exp") or attrs.get("tenure_id") or "",
            tenure_id=_trim(attrs.get("tenure_id")),
            objectid=attrs.get("objectid"),
            fid=attrs.get("fid"),
            titulo_est=_trim(attrs.get("titulo_est")),
            tenure_sta=_trim(attrs.get("tenure_sta")),
            tenure_s01=_trim(attrs.get("tenure_s01")),
            title_type=_trim(attrs.get("title_type")),
            mining_cla=_trim(attrs.get("mining_cla")),
            clasificac=_trim(attrs.get("clasificac")),
            etapa=_trim(attrs.get("etapa")),
            modalidad=_trim(attrs.get("modalidad")),
            publicado_=_trim(attrs.get("publicado_")),
            active_ten=_trim(attrs.get("active_ten")),
            active_app=_trim(attrs.get("active_app")),
            tipo_termi=_trim(attrs.get("tipo_termi")),
            terminatio=_trim(attrs.get("terminatio")),
            departamen=_trim(attrs.get("departamen")),
            municipios=_trim(attrs.get("municipios")),
            area_ha=attrs.get("area_ha"),
            centroid_c=_trim(attrs.get("centroid_c")),
            par=_trim(attrs.get("par")),
            solicitant=_trim(attrs.get("solicitant")),
            minerales=_trim(attrs.get("minerales")),
            minerales_=_trim(attrs.get("minerales_")),
            fecha_de_s=_parse_date(attrs.get("fecha_de_s")),
            fecha_de_e=_parse_date(attrs.get("fecha_de_e")),
            fecha_de_a=_parse_date(attrs.get("fecha_de_a")),
            fecha_de01=_parse_date(attrs.get("fecha_de01")),
            geometry=feature.get("geometry"),
            shape_area=attrs.get("SHAPE__Area"),
            shape_length=attrs.get("SHAPE__Length"),
        )

    @classmethod
    def from_anna(
        cls,
        item: dict[str, Any],
        *,
        release_analysis: dict[str, Any] | None = None,
    ) -> "TituloMinero":
        """Construye un título desde la búsqueda pública de AnnA Minería."""

        def _description(value: Any) -> str | None:
            if isinstance(value, dict):
                return value.get("description") or value.get("code")
            return str(value) if value not in (None, "") else None

        def _code(value: Any) -> str | None:
            return str(value.get("code")) if isinstance(value, dict) and value.get("code") else None

        def _date(value: Any) -> datetime | None:
            if value in (None, ""):
                return None
            try:
                number = float(value)
                if abs(number) > 10_000_000_000:
                    number /= 1000
                return datetime.fromtimestamp(number, tz=timezone.utc)
            except (TypeError, ValueError, OSError, OverflowError):
                return None

        tenure_id = str(item.get("tenureId") or item.get("rmnCode") or "")
        status = item.get("tenureStatus")
        stage = item.get("tenureStage")
        tenure_type = item.get("tenureType")
        classification = item.get("miningClassfication")
        extras = {
            "no_of_cells": item.get("noOfCells"),
            "approved_environmental_licence": item.get("approvedEnvironmentalLicence"),
            "release_analysis": release_analysis or {},
        }
        return cls(
            codigo_exp=tenure_id,
            tenure_id=tenure_id,
            titulo_est=_description(status),
            tenure_sta=_code(status),
            tenure_s01=_code(stage),
            title_type=_code(tenure_type),
            mining_cla=_code(classification),
            clasificac=_description(classification),
            etapa=_description(stage),
            modalidad=_description(tenure_type),
            publicado_=item.get("publishedRucom"),
            active_ten="Y" if _code(status) == "A" else None,
            departamen=item.get("departmentCsv"),
            municipios=item.get("municipalityCsv"),
            solicitant=item.get("clientOwnerInfoVOs") or item.get("titleHoldersCsv"),
            minerales=_description(item.get("mineral")),
            par=_description(item.get("regionalOffice")),
            fecha_de_s=_date(item.get("submissionDate")),
            fecha_de_e=_date(item.get("registrationDate")),
            fecha_de01=_date(item.get("expiryDate")),
            extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializa el título a un diccionario plano apto para JSON."""

        def _fmt(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            return value

        data = asdict(self)
        for key in ("fecha_de_s", "fecha_de_e", "fecha_de_a", "fecha_de01"):
            data[key] = _fmt(data.get(key))
        return data
