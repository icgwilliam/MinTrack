"""Lógica del 'centinela': detección de cambios en títulos mineros de la ANM.

Compara el estado actual de un expediente (desde ``Título_Vigente``) contra el
último snapshot guardado en SQLite y produce una lista de eventos notificables:

* ``liberacion_area``: el área (ha) disminuyó -> se liberó parte del título.
* ``cambio_estado``: cambió ``titulo_est`` (ej. Activo -> En proceso de
  liquidación).
* ``cambio_etapa``: cambió ``etapa`` (ej. Exploración -> Explotación).
* ``vencimiento_proximo``: la fecha de expiración está a <= N días.

No existe un endpoint dedicado de "liberaciones de área" en la ANM (es un
concepto a nivel de aplicación dentro de ANNA Minería). Aquí reconstruimos las
liberaciones detectando reducciones del campo ``area_ha`` entre revisiones.
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

    if snapshot is None:
        # Primera observación: solo aviso de vencimiento próximo si aplica.
        prox = _vencimiento_proximo(titulo)
        if prox:
            eventos.append(prox)
        return eventos

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
    snap = Snapshot(
        codigo_exp=titulo.codigo_exp or titulo.tenure_id or "",
        area_ha=titulo.area_ha,
        titulo_est=titulo.titulo_est,
        etapa=titulo.etapa,
        modalidad=titulo.modalidad,
        fecha_de_e=_fecha_epoch_ms(titulo.fecha_de_e),
        fecha_de01=_fecha_epoch_ms(titulo.fecha_de01),
        visto_en=datetime.now(timezone.utc).timestamp(),
    )
    db.guardar_snapshot(snap)
    return snap
