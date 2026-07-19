"""Consulta y monitorea titulos mineros y liberaciones de area en AnnA Mineria.

Usa exclusivamente los endpoints publicos que alimentan estas pantallas:
  - /sigm/staSearchTitleApplications?lang=es (titulos/solicitudes)
  - /sigm/sarSearchAreaReleases?lang=es (publicaciones de liberacion)

Ejemplos:
  python monitoreotitulo.py ICQ-09083
  python monitoreotitulo.py ICQ-09083 --json
  python monitoreotitulo.py ICQ-09083 --intervalo 300
  python monitoreotitulo.py ICQ-09083 --sin-verificar-ssl

El programa no predice una fecha que la ANM no haya publicado. Una liberacion se
considera publicada solo cuando SAR devuelve un registro con releaseDate.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
import urllib3


BASE_URL = "https://annamineria.anm.gov.co/sigm"
COLOMBIA_TZ = timezone(timedelta(hours=-5))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TITLE_SEARCH_PATH = "/ext/m/search/m/title/getSearchTitleResults"
AREA_RELEASE_SEARCH_PATH = "/ext/m/search/m/sar/getSearchAreaReleaseResults"


class AnnaError(RuntimeError):
    """Error al consultar la plataforma AnnA Mineria."""


def clean_title(value: str) -> str:
    title = value.strip().upper()
    if not title:
        raise ValueError("El numero de expediente no puede estar vacio")
    return title


def timestamp_to_iso(value: Any) -> str | None:
    """Convierte timestamps Unix en milisegundos a hora de Colombia."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) > 10_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(
            COLOMBIA_TZ
        ).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return str(value)


def normalize_dates(value: Any, key: str = "") -> Any:
    """Agrega un campo *_iso junto a cada fecha numerica sin borrar el dato crudo."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            result[child_key] = normalize_dates(child_value, child_key)
            if (
                child_value is not None
                and isinstance(child_value, (int, float))
                and (child_key.lower().endswith("date") or "datetime" in child_key.lower())
            ):
                result[f"{child_key}_iso"] = timestamp_to_iso(child_value)
        return result
    if isinstance(value, list):
        return [normalize_dates(item, key) for item in value]
    return value


def description(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("description") or value.get("code")
    return str(value) if value not in (None, "") else None


class AnnaPublicClient:
    def __init__(self, *, verify_ssl: bool = True, timeout: int = 45):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session.headers.update({"User-Agent": USER_AGENT})
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def initialize(self) -> None:
        """Crea la sesion publica y obtiene las cookies JSESSIONID/XSRF-TOKEN."""
        response = self.session.get(
            f"{BASE_URL}/staSearchTitleApplications",
            params={"lang": "es"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        # El controlador publico queda listo despues de cargar index.html.
        response = self.session.get(f"{BASE_URL}/index.html", timeout=self.timeout)
        response.raise_for_status()
        if not self.session.cookies.get("XSRF-TOKEN"):
            raise AnnaError("AnnA no entrego la cookie publica XSRF-TOKEN")

    def _headers(self) -> dict[str, str]:
        token = self.session.cookies.get("XSRF-TOKEN")
        if not token:
            self.initialize()
            token = self.session.cookies.get("XSRF-TOKEN")
        return {
            "X-XSRF-TOKEN": str(token),
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://annamineria.anm.gov.co",
            "Referer": f"{BASE_URL}/index.html",
        }

    def _post(self, path: str, *, params: dict[str, Any], payload: dict[str, Any]) -> dict:
        response = self.session.post(
            f"{BASE_URL}{path}",
            params=params,
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code in (401, 403):
            self.initialize()
            response = self.session.post(
                f"{BASE_URL}{path}",
                params=params,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as exc:
            raise AnnaError(
                f"AnnA devolvio una respuesta no JSON en {path}: {response.text[:300]!r}"
            ) from exc
        if not isinstance(body, dict):
            raise AnnaError(f"Respuesta inesperada de AnnA en {path}: {type(body).__name__}")
        if body.get("errorMessage"):
            raise AnnaError(str(body["errorMessage"]))
        return body

    def _get_json(self, path: str) -> Any:
        response = self.session.get(
            f"{BASE_URL}{path}",
            params={"lang": "es"},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def search_titles(self, title: str, *, page_size: int = 100) -> dict:
        payload = {
            "tenureNumberId": title,
            "tenureTypes": [],
            "statusCodes": [],
            "submissionDateFrom": None,
            "submissionDateTo": None,
            "registrationDateFrom": None,
            "registrationDateTo": None,
            "transactionNumberId": None,
            "idNumber": None,
            "publicSearch": True,
        }
        return self._post(
            TITLE_SEARCH_PATH,
            params={
                "sortedColumnName": "tenureNumberId",
                "sortedColumnDirection": "desc",
                "currentPage": 1,
                "itemsPerPage": page_size,
                "searchTitlesOnly": "true",
                "lang": "es",
            },
            payload=payload,
        )

    def search_area_releases(self, title: str, *, page_size: int = 100) -> dict:
        # No usar los rangos que la interfaz rellena por defecto: ocultarian
        # publicaciones anteriores al mes actual.
        payload = {
            "tenureId": title,
            "titleTypeCodes": [],
            "resolution": None,
            "publicationDateFrom": None,
            "publicationDateTo": None,
            "firmnessDateFrom": None,
            "firmnessDateTo": None,
            "releaseDateFrom": None,
            "releaseDateTo": None,
            "constanciaEjecutoria": None,
        }
        return self._post(
            AREA_RELEASE_SEARCH_PATH,
            params={
                "sortedColumnName": "tenureId",
                "sortedColumnDirection": "desc",
                "currentPage": 1,
                "itemsPerPage": page_size,
                "lang": "es",
            },
            payload=payload,
        )

    def catalogs(self) -> dict[str, Any]:
        return {
            "titleStatuses": self._get_json("/ext/m/search/m/title/getTitleStatuses"),
            "titleTypes": self._get_json("/ext/m/search/m/title/getTitleTypes"),
            "releaseVersion": self._get_text("/ext/m/core/m/header/getReleaseVersion"),
        }

    def _get_text(self, path: str) -> str:
        response = self.session.get(
            f"{BASE_URL}{path}",
            params={"lang": "es"},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text.strip()


def exact_and_related(items: list[dict[str, Any]], title: str) -> dict[str, list[dict[str, Any]]]:
    """Separa el expediente exacto de variantes historicas como _ICQ-09083."""
    exact = []
    related = []
    target = title.upper()
    for item in items:
        item_id = str(item.get("tenureId") or item.get("rmnCode") or "").upper()
        if item_id == target:
            exact.append(item)
        else:
            related.append(item)
    return {"exact": exact, "related": related}


def analyze_release(
    title: str,
    title_items: list[dict[str, Any]],
    releases: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(COLOMBIA_TZ)
    exact_title = [
        item for item in title_items
        if str(item.get("tenureId") or "").upper() == title.upper()
    ]
    exact_releases = [
        item for item in releases
        if str(item.get("tenureId") or "").upper() == title.upper()
    ]
    statuses = [description(item.get("tenureStatus")) for item in exact_title]

    if exact_releases:
        latest = max(
            exact_releases,
            key=lambda item: item.get("releaseDate") or item.get("publicationDate") or 0,
        )
        release_date = latest.get("releaseDate")
        release_at = None
        seconds_until_release = None
        if release_date:
            release_at = datetime.fromtimestamp(
                float(release_date) / 1000, tz=timezone.utc
            ).astimezone(COLOMBIA_TZ)
            seconds_until_release = int((release_at - now).total_seconds())
            if seconds_until_release > 0:
                state = "LIBERACION_PROGRAMADA"
                message = (
                    "SAR publico una fecha futura de liberacion. El area aun no debe "
                    "considerarse liberada antes de ese instante."
                )
            else:
                state = "LIBERACION_EFECTIVA"
                message = (
                    "SAR publico una fecha de liberacion que ya se cumplio. El area "
                    "figura como liberada segun la publicacion oficial consultada."
                )
        elif latest.get("firmnessDate"):
            state = "ACTO_EN_FIRME_SIN_FECHA_LIBERACION"
            message = (
                "SAR contiene acto en firme/constancia, pero todavia no una fecha de liberacion."
            )
        else:
            state = "PUBLICACION_SIN_FECHA_LIBERACION"
            message = "SAR contiene una publicacion, pero no registra releaseDate."
        return {
            "state": state,
            "message": message,
            "currentTitleStatuses": statuses,
            "latestReleaseRecord": latest,
            "releaseAtColombia": release_at.isoformat(timespec="seconds")
            if release_at else None,
            "secondsUntilRelease": seconds_until_release,
            "signals": {
                "publicationDate": latest.get("publicationDate"),
                "firmnessDate": latest.get("firmnessDate"),
                "releaseDate": latest.get("releaseDate"),
                "resolution": latest.get("resolution") or latest.get("documentName"),
                "constanciaEjecutoria": latest.get("constanciaEjecutoria"),
            },
        }

    if any(status == "Activo" for status in statuses):
        state = "TITULO_ACTIVO_SIN_PUBLICACION_SAR"
        message = (
            "El expediente exacto sigue Activo y no aparece en la consulta publica SAR. "
            "No hay una fecha oficial de liberacion publicada en estos endpoints."
        )
    else:
        state = "SIN_PUBLICACION_SAR"
        message = (
            "No aparece una publicacion de liberacion para el expediente exacto. "
            "El cambio de estado por si solo no confirma que el area ya este liberada."
        )
    return {
        "state": state,
        "message": message,
        "currentTitleStatuses": statuses,
        "latestReleaseRecord": None,
        "releaseAtColombia": None,
        "secondsUntilRelease": None,
        "signals": {
            "publicationDate": None,
            "firmnessDate": None,
            "releaseDate": None,
            "resolution": None,
            "constanciaEjecutoria": None,
        },
    }


def build_report(client: AnnaPublicClient, title: str, *, include_catalogs: bool) -> dict:
    now = datetime.now(COLOMBIA_TZ)
    checked_at = now.isoformat(timespec="seconds")
    title_response = client.search_titles(title)
    release_response = client.search_area_releases(title)
    title_items = title_response.get("searchResultItems") or []
    release_items = release_response.get("searchResultItems") or []
    if not isinstance(title_items, list) or not isinstance(release_items, list):
        raise AnnaError("AnnA devolvio searchResultItems con un formato inesperado")

    report = {
        "query": title,
        "checkedAtColombia": checked_at,
        "source": {
            "titleSearchPage": f"{BASE_URL}/staSearchTitleApplications?lang=es",
            "areaReleasePage": f"{BASE_URL}/sarSearchAreaReleases?lang=es",
            "titleEndpoint": TITLE_SEARCH_PATH,
            "areaReleaseEndpoint": AREA_RELEASE_SEARCH_PATH,
        },
        "releaseAnalysis": analyze_release(title, title_items, release_items, now=now),
        "titles": {
            "count": title_response.get("searchResultCount", len(title_items)),
            **exact_and_related(title_items, title),
        },
        "areaReleases": {
            "count": release_response.get("searchResultCount", len(release_items)),
            **exact_and_related(release_items, title),
        },
        "raw": {
            "titleSearch": title_response,
            "areaReleaseSearch": release_response,
        },
    }
    if include_catalogs:
        report["catalogs"] = client.catalogs()
    return normalize_dates(report)


def report_digest(report: dict) -> str:
    # Los catalogos/version de la aplicacion son informativos y no deben crear
    # una falsa alerta. Solo se comparan los datos propios del expediente.
    stable = copy.deepcopy({
        "query": report.get("query"),
        "releaseAnalysis": report.get("releaseAnalysis"),
        "titles": report.get("titles"),
        "areaReleases": report.get("areaReleases"),
    })
    analysis = stable.get("releaseAnalysis")
    if isinstance(analysis, dict):
        analysis.pop("secondsUntilRelease", None)
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def history_path(title: str, directory: Path) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in title)
    return directory / f"{safe}.jsonl"


def append_history(report: dict, directory: Path) -> tuple[bool, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    path = history_path(str(report["query"]), directory)
    digest = report_digest(report)
    previous_digest = None
    if path.exists():
        try:
            last_line = path.read_text(encoding="utf-8").splitlines()[-1]
            previous_digest = json.loads(last_line).get("digest")
        except (IndexError, OSError, ValueError, json.JSONDecodeError):
            previous_digest = None
    changed = digest != previous_digest
    record = {
        "checkedAtColombia": report["checkedAtColombia"],
        "digest": digest,
        "changed": changed,
        "releaseState": report["releaseAnalysis"]["state"],
        "report": report,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return changed, path


def print_human(report: dict, *, changed: bool | None = None, history: Path | None = None) -> None:
    analysis = report["releaseAnalysis"]
    print(f"\nTitulo consultado: {report['query']}")
    print(f"Fecha de consulta: {report['checkedAtColombia']}")
    print(f"Estado de monitoreo: {analysis['state']}")
    print(f"Interpretacion: {analysis['message']}")
    if analysis.get("releaseAtColombia"):
        print(f"Fecha oficial de liberacion: {analysis['releaseAtColombia']}")
    remaining = analysis.get("secondsUntilRelease")
    if isinstance(remaining, int) and remaining > 0:
        days, remainder = divmod(remaining, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        print(
            "Tiempo aproximado restante: "
            f"{days} dias, {hours} horas, {minutes} minutos, {seconds} segundos"
        )
    if changed is not None:
        print(f"Cambio desde la consulta anterior: {'SI' if changed else 'NO'}")

    print("\nRegistros de titulo/solicitud:")
    all_titles = report["titles"]["exact"] + report["titles"]["related"]
    if not all_titles:
        print("  No se encontraron registros.")
    for item in all_titles:
        print(
            "  - {id} | estado={status} ({code}) | etapa={stage} | modalidad={kind}".format(
                id=item.get("tenureId") or item.get("rmnCode"),
                status=description(item.get("tenureStatus")) or "sin dato",
                code=(item.get("tenureStatus") or {}).get("code")
                if isinstance(item.get("tenureStatus"), dict) else "sin dato",
                stage=description(item.get("tenureStage")) or "sin dato",
                kind=description(item.get("tenureType")) or "sin dato",
            )
        )
        print(
            f"    solicitud={item.get('submissionDate_iso')} "
            f"inscripcion={item.get('registrationDate_iso')} "
            f"vencimiento={item.get('expiryDate_iso')} "
            f"cancelacion={item.get('cancellationDate_iso')}"
        )
        print(
            f"    titulares={item.get('clientOwnerInfoVOs') or item.get('titleHoldersCsv') or item.get('titleHolderCsv') or 'sin dato'}; "
            f"departamento={item.get('departmentCsv') or 'sin dato'}; "
            f"municipio={item.get('municipalityCsv') or 'sin dato'}"
        )

    print("\nPublicaciones de liberacion (SAR):")
    all_releases = report["areaReleases"]["exact"] + report["areaReleases"]["related"]
    if not all_releases:
        print("  No hay registros SAR para este expediente con rangos de fecha abiertos.")
    for item in all_releases:
        print(
            f"  - {item.get('tenureId')} | publicacion={item.get('publicationDate_iso')} "
            f"| firmeza={item.get('firmnessDate_iso')} | liberacion={item.get('releaseDate_iso')}"
        )
        print(
            f"    resolucion={item.get('resolution') or item.get('documentName')} | "
            f"constancia={item.get('constanciaEjecutoria')} | "
            f"documento={item.get('constanciaEjecutoriaDocumentName')}"
        )
    if history:
        print(f"\nHistorial: {history}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consulta y monitorea un titulo minero y su liberacion de area en AnnA."
    )
    parser.add_argument("titulo", help="Numero de expediente, por ejemplo ICQ-09083")
    parser.add_argument(
        "--intervalo",
        type=int,
        default=0,
        metavar="SEGUNDOS",
        help="Repetir indefinidamente cada N segundos (0 = una consulta)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprimir el reporte JSON completo",
    )
    parser.add_argument(
        "--sin-catalogos",
        action="store_true",
        help="No descargar catalogos de estados/modalidades",
    )
    parser.add_argument(
        "--sin-historial",
        action="store_true",
        help="No guardar consultas en historial_titulos/<titulo>.jsonl",
    )
    parser.add_argument(
        "--directorio-historial",
        type=Path,
        default=Path("historial_titulos"),
        help="Directorio de historial (por defecto: historial_titulos)",
    )
    parser.add_argument(
        "--salida",
        type=Path,
        help="Guardar/actualizar el ultimo reporte JSON en este archivo",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Timeout HTTP en segundos (por defecto: 45)",
    )
    parser.add_argument(
        "--sin-verificar-ssl",
        action="store_true",
        help="Desactivar validacion TLS solo si el equipo presenta errores de certificado",
    )
    args = parser.parse_args()
    if args.intervalo < 0:
        parser.error("--intervalo no puede ser negativo")
    if args.intervalo and args.intervalo < 30:
        parser.error("Use --intervalo de al menos 30 segundos para no sobrecargar AnnA")
    return args


def main() -> int:
    args = parse_args()
    title = clean_title(args.titulo)
    client = AnnaPublicClient(
        verify_ssl=not args.sin_verificar_ssl,
        timeout=args.timeout,
    )

    while True:
        try:
            report = build_report(client, title, include_catalogs=not args.sin_catalogos)
            changed = None
            history = None
            if not args.sin_historial:
                changed, history = append_history(report, args.directorio_historial)
            if args.salida:
                args.salida.parent.mkdir(parents=True, exist_ok=True)
                args.salida.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print_human(report, changed=changed, history=history)
        except (requests.RequestException, AnnaError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            if not args.intervalo:
                return 1

        if not args.intervalo:
            return 0
        print(f"\nProxima consulta en {args.intervalo} segundos. Ctrl+C para detener.")
        try:
            time.sleep(args.intervalo)
        except KeyboardInterrupt:
            print("\nMonitoreo detenido.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
