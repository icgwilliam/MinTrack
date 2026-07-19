"""Lógica del 'centinela': detección de cambios en títulos mineros de la ANM.

Compara el estado actual de un expediente (desde AnnA Minería) contra el
último snapshot guardado en SQLite y produce una lista de eventos notificables:

* ``liberacion_publicada``: SAR publicó o cambió una fecha de liberación.
* ``cambio_estado``: cambió ``titulo_est`` (ej. Activo -> En proceso de
  liquidación).
* ``cambio_etapa``: cambió ``etapa`` (ej. Exploración -> Explotación).
* ``vencimiento_proximo``: la fecha de expiración está a <= N días.

La señal principal de liberación viene del endpoint público SAR consultado por
``existing_scripts/monitoreotitulo.py``. La reducción de área se conserva como
señal complementaria cuando ese dato esté disponible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .db import Database, Snapshot
from .models import TituloMinero

VENCIMIENTO_DIAS_AVISO = 30  # avisar si la expiración es en <= 30 días.
UMBRAL_LIBERACION_HA = 0.5  # cambio de área >= 0.5 ha para considerar liberación.


@dataclass
class EventoCentinela:
    tipo: str                 # liberacion_area | cambio_estado | cambio_etapa | vencimiento_proximo
    codigo_exp: str
    mensaje: str
    detalles: dict            # datos relevantes del evento


def _fecha_epoch_ms(t: Optional[datetime]) -> Optional[float]:
    if t is None:
        return None
    return t.timestamp() * 1000.0


def _fecha_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def comparar(titulo: TituloMinero, snapshot: Optional[Snapshot]) -> list[EventoCentinela]:
    """Compara el título actual contra el snapshot previo.

    Si ``snapshot`` es None (primera revisión), no se generan eventos de cambio
    (solo se guarda el estado inicial); pero sí puede avisar vencimiento próximo.
    """
    codigo = titulo.codigo_exp or titulo.tenure_id or ""
    eventos: list[EventoCentinela] = []
    analysis = titulo.extras.get("release_analysis") or {}
    signals = analysis.get("signals") or {}
    release_state = analysis.get("state")
    release_date = signals.get("releaseDate")

    if snapshot is None:
        # Primera observación: solo aviso de vencimiento próximo si aplica.
        prox = _vencimiento_proximo(titulo)
        if prox:
            eventos.append(prox)
        return eventos

    publication_states = {
        "PUBLICACION_SIN_FECHA_LIBERACION",
        "ACTO_EN_FIRME_SIN_FECHA_LIBERACION",
        "LIBERACION_PROGRAMADA",
        "LIBERACION_EFECTIVA",
    }
    if release_state in publication_states and (
        release_date != snapshot.release_date or release_state != snapshot.release_state
    ):
        release_at = analysis.get("releaseAtColombia") or "fecha no disponible"
        eventos.append(
            EventoCentinela(
                tipo="liberacion_publicada",
                codigo_exp=codigo,
                mensaje=(
                    f"Actualización oficial SAR para {codigo}: {release_at}. "
                    f"Estado SAR: {release_state}."
                ),
                detalles={
                    "estado_sar": release_state,
                    "fecha_liberacion": release_at,
                    "release_date": release_date,
                },
            )
        )

    # Liberación de área: reducción significativa.
    if (
        snapshot.area_ha is not None
        and titulo.area_ha is not None
        and titulo.area_ha < snapshot.area_ha - UMBRAL_LIBERACION_HA
    ):
        liberada = snapshot.area_ha - titulo.area_ha
        eventos.append(
            EventoCentinela(
                tipo="liberacion_area",
                codigo_exp=codigo,
                mensaje=(
                    f"🔻 Liberación de área detectada en {codigo}: el área pasó "
                    f"de {snapshot.area_ha:.2f} ha a {titulo.area_ha:.2f} ha "
                    f"(se liberaron ~{liberada:.2f} ha)."
                ),
                detalles={
                    "area_anterior": snapshot.area_ha,
                    "area_actual": titulo.area_ha,
                    "liberada_ha": round(liberada, 2),
                },
            )
        )
    elif (
        snapshot.area_ha is not None
        and titulo.area_ha is not None
        and titulo.area_ha > snapshot.area_ha + UMBRAL_LIBERACION_HA
    ):
        # Aumento de área (ampliación). Lo reportamos como novedad.
        delta = titulo.area_ha - snapshot.area_ha
        eventos.append(
            EventoCentinela(
                tipo="ampliacion_area",
                codigo_exp=codigo,
                mensaje=(
                    f"🔼 Ampliación de área en {codigo}: el área pasó de "
                    f"{snapshot.area_ha:.2f} ha a {titulo.area_ha:.2f} ha "
                    f"(+{delta:.2f} ha)."
                ),
                detalles={
                    "area_anterior": snapshot.area_ha,
                    "area_actual": titulo.area_ha,
                    "delta_ha": round(delta, 2),
                },
            )
        )

    # Cambio de estado del título.
    if snapshot.titulo_est and titulo.titulo_est and snapshot.titulo_est != titulo.titulo_est:
        eventos.append(
            EventoCentinela(
                tipo="cambio_estado",
                codigo_exp=codigo,
                mensaje=(
                    f"ℹ️ Cambio de estado en {codigo}: '{snapshot.titulo_est}' -> "
                    f"'{titulo.titulo_est}'."
                ),
                detalles={
                    "estado_anterior": snapshot.titulo_est,
                    "estado_actual": titulo.titulo_est,
                },
            )
        )

    # Cambio de etapa.
    if snapshot.etapa and titulo.etapa and snapshot.etapa != titulo.etapa:
        eventos.append(
            EventoCentinela(
                tipo="cambio_etapa",
                codigo_exp=codigo,
                mensaje=(
                    f"ℹ️ Cambio de etapa en {codigo}: '{snapshot.etapa}' -> "
                    f"'{titulo.etapa}'."
                ),
                detalles={
                    "etapa_anterior": snapshot.etapa,
                    "etapa_actual": titulo.etapa,
                },
            )
        )

    prox = _vencimiento_proximo(titulo)
    # Si la fecha de expiración cambió a una próxima, avisar.
    if prox and (snapshot.fecha_de01 != _fecha_epoch_ms(titulo.fecha_de01)):
        eventos.append(prox)

    return eventos


def _vencimiento_proximo(titulo: TituloMinero) -> Optional[EventoCentinela]:
    """Genera un evento si la expiración está a <= VENCIMIENTO_DIAS_AVISO días."""
    if titulo.fecha_de01 is None:
        return None
    ahora = datetime.now(timezone.utc)
    dias = (titulo.fecha_de01 - ahora).days
    if 0 <= dias <= VENCIMIENTO_DIAS_AVISO:
        codigo = titulo.codigo_exp or titulo.tenure_id or ""
        return EventoCentinela(
            tipo="vencimiento_proximo",
            codigo_exp=codigo,
            mensaje=(
                f"⏰ Vencimiento próximo en {codigo}: expira el "
                f"{titulo.fecha_de01.strftime('%Y-%m-%d')} (en {dias} día(s))."
            ),
            detalles={"fecha_expiracion": titulo.fecha_de01.isoformat(), "dias": dias},
        )
    return None


def actualizar_snapshot(db: Database, titulo: TituloMinero) -> Snapshot:
    """Crea/actualiza el snapshot a partir de un título."""
    analysis = titulo.extras.get("release_analysis") or {}
    signals = analysis.get("signals") or {}
    snap = Snapshot(
        codigo_exp=titulo.codigo_exp or titulo.tenure_id or "",
        area_ha=titulo.area_ha,
        titulo_est=titulo.titulo_est,
        etapa=titulo.etapa,
        modalidad=titulo.modalidad,
        fecha_de_e=_fecha_epoch_ms(titulo.fecha_de_e),
        fecha_de01=_fecha_epoch_ms(titulo.fecha_de01),
        visto_en=datetime.now(timezone.utc).timestamp(),
        release_state=analysis.get("state"),
        release_date=signals.get("releaseDate"),
    )
    db.guardar_snapshot(snap)
    return snap
