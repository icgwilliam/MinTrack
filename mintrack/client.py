"""Cliente de la API REST de la Agencia Nacional de Minería (ANM) de Colombia.

Fuente principal (por defecto): el FeatureServer ``Título_Vigente`` publicado por
la ANM sobre ArcGIS Enterprise. Es la **misma capa que alimenta el visor ANNA
Minería** y contiene los campos completos del expediente (estado, fechas de
solicitud/expedición/aniversario/expiración, clasificación de minería,
solicitantes con identificación, códigos de estado, centroide, etc.)::

    https://gisanm.anm.gov.co/server/rest/services/Hosted/Título_Vigente/FeatureServer/0

No requiere token para consultas (``Query`` anónimo habilitado).

Fuente secundaria (legacy): el FeatureServer ``Titulos_mineros`` con la capa
``titulos_vigentes``, con un esquema más simple y sin fechas reales. Se conserva
como *fallback* cuando la capa principal no responde o no trae un expediente.
"""

from __future__ import annotations

from typing import Any

import requests

from .models import TituloMinero

# Fuente principal (esquema completo, igual que ANNA Minería).
FEATURESERVER_PRINCIPAL_URL = (
    "https://gisanm.anm.gov.co/server/rest/services/Hosted/T%C3%ADtulo_Vigente/FeatureServer"
)
LAYER_PRINCIPAL_ID = 0

# Fuente legacy (solo vigentes, esquema simple).
FEATURESERVER_LEGACY_URL = (
    "https://gisanm.anm.gov.co/server/rest/services/Hosted/Titulos_mineros/FeatureServer"
)
LAYER_LEGACY_ID = 0

DEFAULT_TIMEOUT = 30

ALL_FIELDS = "*"


class ANMError(Exception):
    """Error devuelto por el servicio de la ANM."""


class ANMClient:
    """Cliente para consultar títulos/expedientes mineros de Colombia."""

    def __init__(
        self,
        base_url: str = FEATURESERVER_PRINCIPAL_URL,
        layer_id: int = LAYER_PRINCIPAL_ID,
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
        legacy_url: str = FEATURESERVER_LEGACY_URL,
        legacy_layer_id: int = LAYER_LEGACY_ID,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.layer_id = layer_id
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.setdefault(
            "User-Agent", "MinTrack/1.0 (+https://github.com/Kilo-Org/kilocode)"
        )
        self.legacy_url = legacy_url.rstrip("/")
        self.legacy_layer_id = legacy_layer_id

    @property
    def query_url(self) -> str:
        return f"{self.base_url}/{self.layer_id}/query"

    @property
    def legacy_query_url(self) -> str:
        return f"{self.legacy_url}/{self.legacy_layer_id}/query"

    def _query(
        self,
        url: str,
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
            resp = self._session.post(url, data=payload, timeout=self.timeout)
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

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("'", "''")

    def consultar_por_expediente(
        self,
        codigo: str,
        *,
        return_geometry: bool = True,
    ) -> list[TituloMinero]:
        """Devuelve los títulos cuyo código de expediente coincide exactamente.

        El código de expediente usa el formato ``AAA-#####`` (p. ej.
        ``ICQ-09083``). Se busca primero en la capa principal ``Título_Vigente``
        (campos completos, igual que ANNA) por ``tenure_id`` y ``codigo_exp``.

        Si la capa principal no trae resultados, reintenta en la capa legacy
        ``titulos_vigentes`` (solo títulos vigentes, esquema simple).
        """
        codigo = (codigo or "").strip()
        if not codigo:
            raise ValueError("El código de expediente no puede estar vacío.")

        esc = self._escape(codigo)
        # La capa principal guarda el mismo valor en tenure_id y codigo_exp.
        where = f"tenure_id = '{esc}' OR codigo_exp = '{esc}'"
        try:
            data = self._query(self.query_url, where, return_geometry=return_geometry)
        except ANMError:
            data = {"features": []}

        features = data.get("features", []) or []
        if features:
            return [TituloMinero.from_feature(f) for f in features]

        # Fallback legacy: esquema simple (codigo_exp), solo vigentes.
        where_legacy = f"codigo_exp = '{esc}'"
        try:
            data = self._query(
                self.legacy_query_url, where_legacy, return_geometry=return_geometry
            )
        except ANMError:
            return []
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
        contengan ``texto`` (sin distinguir mayúsculas/minúsculas).
        """
        texto = (texto or "").strip()
        if not texto:
            raise ValueError("El texto de búsqueda no puede estar vacío.")

        esc = self._escape(texto)
        where = (
            f"UPPER(tenure_id) LIKE UPPER('%{esc}%') "
            f"OR UPPER(codigo_exp) LIKE UPPER('%{esc}%')"
        )
        try:
            data = self._query(
                self.query_url,
                where,
                return_geometry=return_geometry,
                result_record_count=limit,
            )
        except ANMError:
            data = {"features": []}

        features = data.get("features", []) or []
        if features:
            return [TituloMinero.from_feature(f) for f in features]

        # Fallback legacy.
        where_legacy = f"UPPER(codigo_exp) LIKE UPPER('%{esc}%')"
        try:
            data = self._query(
                self.legacy_query_url,
                where_legacy,
                return_geometry=return_geometry,
                result_record_count=limit,
            )
        except ANMError:
            return []
        features = data.get("features", []) or []
        return [TituloMinero.from_feature(f) for f in features]
