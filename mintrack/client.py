"""Cliente de la API REST de la Agencia Nacional de Minería (ANM) de Colombia.

Se basa en el FeatureServer público de títulos mineros publicados por la ANM
sobre ArcGIS Enterprise:

    https://gisanm.anm.gov.co/server/rest/services/Hosted/Titulos_mineros/FeatureServer

La capa ``titulos_vigentes`` (id 0) expone los atributos de cada título minero
vigente (código del expediente, estado, modalidad, etapa, minerales, área,
fechas, municipio, departamento, etc.) y su geometría en MAGNA-SIRGAS (SR 4686).
"""

from __future__ import annotations

from typing import Any

import requests

from .models import TituloMinero

FEATURESERVER_URL = (
    "https://gisanm.anm.gov.co/server/rest/services/Hosted/Titulos_mineros/FeatureServer"
)
LAYER_ID = 0
DEFAULT_TIMEOUT = 30

ALL_FIELDS = (
    "fid,codigo_exp,estado_exp,modalidade,etapa,minerales,municipios,departamento,"
    "solicitante,grupo_trab,area_ha,fecha_insc,fecha_term,tipo_explo,capaminera,"
    "producto,SHAPE__Area,SHAPE__Length"
)


class ANMError(Exception):
    """Error devuelto por el servicio de la ANM."""


class ANMClient:
    """Cliente para consultar títulos mineros vigentes en Colombia."""

    def __init__(
        self,
        base_url: str = FEATURESERVER_URL,
        layer_id: int = LAYER_ID,
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.layer_id = layer_id
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.setdefault(
            "User-Agent", "MinTrack/1.0 (+https://github.com/Kilo-Org/kilocode)"
        )

    @property
    def query_url(self) -> str:
        return f"{self.base_url}/{self.layer_id}/query"

    def _query(
        self,
        where: str,
        *,
        out_fields: str = ALL_FIELDS,
        return_geometry: bool = True,
        result_record_count: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true" if return_geometry else "false",
            "f": "json",
        }
        if result_record_count is not None:
            payload["resultRecordCount"] = result_record_count
        if params:
            payload.update(params)

        try:
            resp = self._session.post(
                self.query_url, data=payload, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ANMError(f"Error de red consultando la ANM: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise ANMError("La respuesta de la ANM no es JSON válido") from exc

        if data.get("error"):
            err = data["error"]
            raise ANMError(
                f"Error del servicio ANM (code {err.get('code', '?')}): "
                f"{err.get('message', 'desconocido')}"
            )
        return data

    def consultar_por_expediente(
        self,
        codigo: str,
        *,
        return_geometry: bool = True,
    ) -> list[TituloMinero]:
        """Devuelve los títulos mineros cuyo código de expediente coincide.

        El código de expediente usa el formato ``AAA-#####`` (por ejemplo
        ``TGU-14471``). La búsqueda es sensible a mayúsculas/minúsculas y guiones,
        tal como los devuelve el servicio. Se realiza una coincidencia exacta.

        Si no se encuentran resultados, se devuelve una lista vacía.
        """
        codigo = (codigo or "").strip()
        if not codigo:
            raise ValueError("El código de expediente no puede estar vacío.")

        where = f"codigo_exp = '{codigo.replace(chr(39), chr(39) * 2)}'"
        data = self._query(where, return_geometry=return_geometry)
        features = data.get("features", []) or []
        return [TituloMinero.from_feature(f) for f in features]

    def buscar_por_codigo(
        self,
        texto: str,
        *,
        return_geometry: bool = False,
        limit: int = 50,
    ) -> list[TituloMinero]:
        """Búsqueda parcial por código de expediente (LIKE).

        Útil cuando no se conoce el código exacto. Coincide con códigos que
        contengan ``texto`` (sin distinguir mayúsculas/minúsculas gracias al
        operador ``UPPER`` de la base de datos subyacente).
        """
        texto = (texto or "").strip()
        if not texto:
            raise ValueError("El texto de búsqueda no puede estar vacío.")

        safe = texto.replace("'", "''")
        where = f"UPPER(codigo_exp) LIKE UPPER('%{safe}%')"
        data = self._query(
            where,
            return_geometry=return_geometry,
            result_record_count=limit,
        )
        features = data.get("features", []) or []
        return [TituloMinero.from_feature(f) for f in features]
