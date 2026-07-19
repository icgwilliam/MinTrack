"""Cliente de AnnA Minería basado en ``existing_scripts/monitoreotitulo.py``."""

from __future__ import annotations

from typing import Any

import requests

from existing_scripts.monitoreotitulo import (
    AnnaError,
    AnnaPublicClient,
    build_report,
    clean_title,
)

from .models import TituloMinero

DEFAULT_TIMEOUT = 45


class ANMError(Exception):
    """Error al consultar los servicios públicos de AnnA Minería."""


class ANMClient:
    """Adapta el script oficial del proyecto a la interfaz usada por MinTrack."""

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._client = AnnaPublicClient(verify_ssl=verify_ssl, timeout=timeout)
        if session is not None:
            self._client.session = session

    def consultar_reporte(self, codigo: str) -> dict[str, Any]:
        """Consulta título y liberaciones SAR usando ``monitoreotitulo.py``."""
        try:
            return build_report(
                self._client,
                clean_title(codigo),
                include_catalogs=False,
            )
        except (AnnaError, requests.RequestException) as exc:
            raise ANMError(f"Error consultando AnnA Minería: {exc}") from exc

    @staticmethod
    def _titulos_desde_reporte(
        report: dict[str, Any],
        *,
        exactos: bool,
        limit: int | None = None,
    ) -> list[TituloMinero]:
        titles = report.get("titles") or {}
        items = list(titles.get("exact") or [])
        if not exactos:
            items.extend(titles.get("related") or [])
        if limit is not None:
            items = items[:limit]
        analysis = report.get("releaseAnalysis") or {}
        return [
            TituloMinero.from_anna(item, release_analysis=analysis)
            for item in items
            if isinstance(item, dict)
        ]

    def consultar_por_expediente(
        self,
        codigo: str,
        *,
        return_geometry: bool = True,
    ) -> list[TituloMinero]:
        """Devuelve coincidencias exactas desde la búsqueda pública de AnnA."""
        del return_geometry  # El endpoint público consultado no devuelve geometría.
        report = self.consultar_reporte(codigo)
        return self._titulos_desde_reporte(report, exactos=True)

    def buscar_por_codigo(
        self,
        texto: str,
        *,
        return_geometry: bool = False,
        limit: int = 50,
    ) -> list[TituloMinero]:
        """Busca en AnnA y devuelve coincidencias exactas e históricas."""
        del return_geometry
        if limit < 1:
            raise ValueError("El límite debe ser mayor que cero.")
        report = self.consultar_reporte(texto)
        return self._titulos_desde_reporte(report, exactos=False, limit=limit)
