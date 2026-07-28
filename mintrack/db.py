"""Capa de persistencia (SQLite) para el bot de Telegram.

Modelo de datos:

* ``solicitudes``: procesos creados por un usuario con los datos del wizard
  (empresa/persona, identificación, teléfono, servicio(s)). Un usuario puede
  tener varios procesos activos a la vez (uno por servicio contratado); ya no
  se sobrescriben entre sí.
* ``documentos``: archivos que el usuario envía, referenciados a un proceso
  concreto (incluye el tipo especial ``pago`` para el soporte de pago).
* ``admins``: usuarios que se autenticaron con el PIN de administrador, para
  poder notificarles eventos (p. ej. un nuevo soporte de pago por revisar).

El estado de cada proceso avanza por una máquina de estados interna:

    EN_REVISION -> EN_PROCESO -> CENTINELA_ACTIVO -> COMPLETADO

``EN_REVISION`` representa "pago pendiente de confirmar" y solo avanza cuando
un administrador confirma el pago (o lo avanza manualmente). Los estados
posteriores sí avanzan automáticamente por tiempo (ver ``ESTADO_DURACION``).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_DB_PATH = os.environ.get(
    "MINTRACK_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "mintrack.db"),
)

# Estados de un proceso.
ESTADO_EN_REVISION = "EN_REVISION"
ESTADO_EN_PROCESO = "EN_PROCESO"
ESTADO_CENTINELA = "CENTINELA_ACTIVO"
ESTADO_COMPLETADO = "COMPLETADO"

ESTADOS_ORDEN = [
    ESTADO_EN_REVISION,
    ESTADO_EN_PROCESO,
    ESTADO_CENTINELA,
    ESTADO_COMPLETADO,
]

ESTADO_LABELS = {
    ESTADO_EN_REVISION: "En revisión (pago pendiente)",
    ESTADO_EN_PROCESO: "En proceso de aplicación",
    ESTADO_CENTINELA: "Centinela activo",
    ESTADO_COMPLETADO: "Completado",
}

# Tiempos mínimos (en segundos) que un proceso debe permanecer en cada estado
# antes de avanzar automáticamente al siguiente. EN_REVISION no tiene regla de
# tiempo: solo avanza cuando el admin confirma el pago (ver `confirmar_pago`).
# Valores cortos para que el demo sea observable; para producción, ajustar.
ESTADO_DURACION = {
    ESTADO_EN_PROCESO: 5 * 60,     # 5 min
    ESTADO_CENTINELA: 5 * 60,       # 5 min
    # COMPLETADO es terminal.
}

# Estado del soporte de pago de un proceso.
PAGO_PENDIENTE = "PENDIENTE"
PAGO_EN_REVISION = "EN_REVISION"
PAGO_CONFIRMADO = "CONFIRMADO"

PAGO_LABELS = {
    PAGO_PENDIENTE: "Pendiente de enviar",
    PAGO_EN_REVISION: "Comprobante enviado, por confirmar",
    PAGO_CONFIRMADO: "Confirmado",
}

# Estado de una invitación de cliente generada desde el panel admin.
INVITACION_PENDIENTE = "PENDIENTE"
INVITACION_ACEPTADA = "ACEPTADA"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS solicitudes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    empresa TEXT NOT NULL,
    identificacion TEXT NOT NULL,
    telefono TEXT NOT NULL,
    servicio TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'EN_REVISION',
    pago_estado TEXT NOT NULL DEFAULT 'PENDIENTE',
    created_at REAL NOT NULL,
    estado_desde REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_solicitudes_user ON solicitudes(user_id);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solicitud_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    file_name TEXT,
    tipo TEXT NOT NULL,           -- 'pdf' | 'imagen' | 'shape' | 'pago' | 'otro'
    ruta TEXT,                    -- ruta local guardada (opcional)
    created_at REAL NOT NULL,
    FOREIGN KEY (solicitud_id) REFERENCES solicitudes(id)
);

CREATE INDEX IF NOT EXISTS idx_documentos_solicitud ON documentos(solicitud_id);
CREATE INDEX IF NOT EXISTS idx_documentos_user ON documentos(user_id);

-- Usuarios que se autenticaron alguna vez con el PIN de administrador
-- (para poder notificarles eventos, p. ej. un pago por confirmar).
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    authenticated_at REAL NOT NULL
);

-- Invitaciones generadas desde el panel admin: un link personal
-- (t.me/<bot>?start=<token>) que el admin comparte por WhatsApp/SMS, ya que
-- Telegram no permite que el bot le escriba primero a un número que nunca lo
-- ha contactado.
CREATE TABLE IF NOT EXISTS invitaciones (
    token TEXT PRIMARY KEY,
    telefono TEXT NOT NULL,
    creado_por INTEGER NOT NULL,
    created_at REAL NOT NULL,
    estado TEXT NOT NULL DEFAULT 'PENDIENTE',   -- PENDIENTE | ACEPTADA
    aceptado_por INTEGER,
    aceptado_at REAL
);

CREATE INDEX IF NOT EXISTS idx_invitaciones_creado_por ON invitaciones(creado_por);

-- Suscripciones de usuarios a expedientes para monitoreo (centinela).
CREATE TABLE IF NOT EXISTS suscripciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    codigo_exp TEXT NOT NULL,
    activa INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    UNIQUE(user_id, codigo_exp)   -- una suscripción activa por (usuario, exp)
);

CREATE INDEX IF NOT EXISTS idx_suscripciones_activas ON suscripciones(codigo_exp) WHERE activa = 1;

-- Snapshot del último estado conocido de un expediente (para detectar cambios).
-- Un expediente puede tener varios snapshots; se guarda el más reciente por
-- codigo_exp. Se usa una fila por codigo_exp (upsert).
CREATE TABLE IF NOT EXISTS snapshots (
    codigo_exp TEXT PRIMARY KEY,
    area_ha REAL,
    titulo_est TEXT,
    etapa TEXT,
    modalidad TEXT,
    fecha_de_e REAL,            -- fecha de expedición (epoch)
    fecha_de01 REAL,            -- fecha de expiración (epoch)
    release_state TEXT,         -- estado calculado desde la publicación SAR
    release_date REAL,          -- fecha oficial de liberación SAR (epoch ms)
    visto_en REAL NOT NULL      -- timestamp de la última revisión
);

CREATE INDEX IF NOT EXISTS idx_suscripciones_user ON suscripciones(user_id);
"""


@dataclass
class Solicitud:
    id: int
    user_id: int
    empresa: str
    identificacion: str
    telefono: str
    servicio: str          # CSV de códigos del catálogo BR-001 (ej. "monitoreo,radicacion")
    estado: str
    pago_estado: str
    created_at: float
    estado_desde: float

    @property
    def estado_label(self) -> str:
        return ESTADO_LABELS.get(self.estado, self.estado)

    @property
    def pago_estado_label(self) -> str:
        return PAGO_LABELS.get(self.pago_estado, self.pago_estado)

    @property
    def servicios(self) -> list[str]:
        """Códigos de servicio contratados (uno o varios, según BR-001)."""
        return [s.strip() for s in self.servicio.split(",") if s.strip()]


@dataclass
class Invitacion:
    token: str
    telefono: str
    creado_por: int
    created_at: float
    estado: str
    aceptado_por: Optional[int]
    aceptado_at: Optional[float]

    @property
    def estado_label(self) -> str:
        return "✅ Aceptada" if self.estado == INVITACION_ACEPTADA else "⏳ Pendiente"


@dataclass
class Suscripcion:
    id: int
    user_id: int
    codigo_exp: str
    activa: int
    created_at: float


@dataclass
class Snapshot:
    codigo_exp: str
    area_ha: Optional[float]
    titulo_est: Optional[str]
    etapa: Optional[str]
    modalidad: Optional[str]
    fecha_de_e: Optional[float]
    fecha_de01: Optional[float]
    visto_en: float
    release_state: Optional[str] = None
    release_date: Optional[float] = None


class Database:
    """Wrapper thread-safe sobre sqlite3 (con check_same_thread=False + lock)."""

    def __init__(self, path: str = DEFAULT_DB_PATH) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate_snapshots()
        self._migrate_solicitudes()
        self._conn.commit()

    def _migrate_snapshots(self) -> None:
        """Añade señales SAR a bases creadas por versiones anteriores."""
        columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(snapshots)").fetchall()
        }
        for name, kind in (("release_state", "TEXT"), ("release_date", "REAL")):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE snapshots ADD COLUMN {name} {kind}")

    def _migrate_solicitudes(self) -> None:
        """Migra bases antiguas: quita el límite de 1 solicitud por usuario y
        reemplaza 'contacto' por 'identificacion' + 'pago_estado'.
        """
        columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(solicitudes)").fetchall()
        }
        if "identificacion" in columns:
            return
        self._conn.executescript(
            """
            ALTER TABLE solicitudes RENAME TO solicitudes_old;
            CREATE TABLE solicitudes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                empresa TEXT NOT NULL,
                identificacion TEXT NOT NULL,
                telefono TEXT NOT NULL,
                servicio TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'EN_REVISION',
                pago_estado TEXT NOT NULL DEFAULT 'PENDIENTE',
                created_at REAL NOT NULL,
                estado_desde REAL NOT NULL
            );
            INSERT INTO solicitudes
                (id, user_id, empresa, identificacion, telefono, servicio,
                 estado, pago_estado, created_at, estado_desde)
            SELECT id, user_id, empresa, '', telefono, servicio, estado,
                   CASE WHEN estado = 'EN_REVISION' THEN 'PENDIENTE' ELSE 'CONFIRMADO' END,
                   created_at, estado_desde
            FROM solicitudes_old;
            DROP TABLE solicitudes_old;
            CREATE INDEX IF NOT EXISTS idx_solicitudes_user ON solicitudes(user_id);
            """
        )

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- Solicitudes (procesos) --------------------------------------------

    def crear_solicitud(
        self,
        user_id: int,
        empresa: str,
        identificacion: str,
        telefono: str,
        servicio: str,
    ) -> Optional[Solicitud]:
        """Crea un nuevo proceso para el usuario (no reemplaza los anteriores:
        un usuario puede tener varios procesos activos a la vez)."""
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO solicitudes
                   (user_id, empresa, identificacion, telefono, servicio, estado,
                    pago_estado, created_at, estado_desde)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    empresa,
                    identificacion,
                    telefono,
                    servicio,
                    ESTADO_EN_REVISION,
                    PAGO_PENDIENTE,
                    now,
                    now,
                ),
            )
            sid = cur.lastrowid
        return self.obtener_solicitud_por_id(sid)

    def obtener_solicitud_por_id(self, solicitud_id: int) -> Optional[Solicitud]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM solicitudes WHERE id = ? LIMIT 1", (solicitud_id,)
            ).fetchone()
        if not row:
            return None
        return Solicitud(**dict(row))

    def listar_solicitudes(self, user_id: int) -> list[Solicitud]:
        """Todos los procesos de un usuario (más reciente primero)."""
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM solicitudes WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [Solicitud(**dict(r)) for r in rows]

    def listar_todas_solicitudes(self) -> list[Solicitud]:
        """Todos los procesos de todos los usuarios (para el panel admin)."""
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM solicitudes ORDER BY created_at DESC"
            ).fetchall()
        return [Solicitud(**dict(r)) for r in rows]

    def listar_solicitudes_activas(self) -> list[Solicitud]:
        """Procesos que aún no han llegado al estado terminal (para el scheduler)."""
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM solicitudes WHERE estado != ? ORDER BY created_at",
                (ESTADO_COMPLETADO,),
            ).fetchall()
        return [Solicitud(**dict(r)) for r in rows]

    def _set_estado(self, solicitud_id: int, nuevo_estado: str) -> None:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE solicitudes SET estado = ?, estado_desde = ? WHERE id = ?",
                (nuevo_estado, now, solicitud_id),
            )

    def avanzar_estado(self, solicitud_id: int) -> Optional[Solicitud]:
        """Avanza un proceso al siguiente estado (uso admin)."""
        sol = self.obtener_solicitud_por_id(solicitud_id)
        if not sol:
            return None
        idx = ESTADOS_ORDEN.index(sol.estado) if sol.estado in ESTADOS_ORDEN else -1
        if idx < 0 or idx >= len(ESTADOS_ORDEN) - 1:
            return sol  # terminal o desconocido
        self._set_estado(solicitud_id, ESTADOS_ORDEN[idx + 1])
        return self.obtener_solicitud_por_id(solicitud_id)

    def retroceder_estado(self, solicitud_id: int) -> Optional[Solicitud]:
        """Retrocede un proceso al estado anterior (uso admin)."""
        sol = self.obtener_solicitud_por_id(solicitud_id)
        if not sol:
            return None
        idx = ESTADOS_ORDEN.index(sol.estado) if sol.estado in ESTADOS_ORDEN else -1
        if idx <= 0:
            return sol  # ya está en el primer estado o es desconocido
        self._set_estado(solicitud_id, ESTADOS_ORDEN[idx - 1])
        return self.obtener_solicitud_por_id(solicitud_id)

    def confirmar_pago(self, solicitud_id: int) -> Optional[Solicitud]:
        """Marca el pago como confirmado y avanza el proceso si seguía en
        EN_REVISION esperando esa confirmación (uso admin)."""
        sol = self.obtener_solicitud_por_id(solicitud_id)
        if not sol:
            return None
        with self._cursor() as cur:
            cur.execute(
                "UPDATE solicitudes SET pago_estado = ? WHERE id = ?",
                (PAGO_CONFIRMADO, solicitud_id),
            )
        if sol.estado == ESTADO_EN_REVISION:
            self._set_estado(solicitud_id, ESTADO_EN_PROCESO)
        return self.obtener_solicitud_por_id(solicitud_id)

    def marcar_pago_en_revision(self, solicitud_id: int) -> Optional[Solicitud]:
        """El usuario subió un comprobante: queda pendiente de confirmar."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE solicitudes SET pago_estado = ? WHERE id = ? AND pago_estado = ?",
                (PAGO_EN_REVISION, solicitud_id, PAGO_PENDIENTE),
            )
        return self.obtener_solicitud_por_id(solicitud_id)

    def sincronizar_estado(self, solicitud_id: int) -> Optional[Solicitud]:
        """Aplica el avance automático por tiempo (no aplica a EN_REVISION,
        que solo avanza cuando el admin confirma el pago)."""
        sol = self.obtener_solicitud_por_id(solicitud_id)
        if not sol or sol.estado == ESTADO_COMPLETADO:
            return sol

        now = time.time()
        if sol.estado in ESTADO_DURACION and now - sol.estado_desde >= ESTADO_DURACION[sol.estado]:
            idx = ESTADOS_ORDEN.index(sol.estado)
            if idx < len(ESTADOS_ORDEN) - 1:
                self._set_estado(solicitud_id, ESTADOS_ORDEN[idx + 1])
                sol = self.obtener_solicitud_por_id(solicitud_id)
        return sol

    # ---- Documentos ------------------------------------------------------

    def registrar_documento(
        self,
        solicitud_id: int,
        user_id: int,
        file_id: str,
        file_name: Optional[str],
        tipo: str,
        ruta: Optional[str] = None,
    ) -> int:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO documentos
                   (solicitud_id, user_id, file_id, file_name, tipo, ruta, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (solicitud_id, user_id, file_id, file_name, tipo, ruta, now),
            )
            return cur.lastrowid

    def contar_documentos(self, solicitud_id: int) -> int:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT COUNT(*) AS n FROM documentos WHERE solicitud_id = ?",
                (solicitud_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def listar_documentos(self, solicitud_id: int) -> list[sqlite3.Row]:
        with self._cursor() as cur:
            return cur.execute(
                "SELECT * FROM documentos WHERE solicitud_id = ? ORDER BY created_at",
                (solicitud_id,),
            ).fetchall()

    # ---- Admins ------------------------------------------------------------

    def registrar_admin(self, user_id: int) -> None:
        """Recuerda que este usuario se autenticó como admin (para notificarle)."""
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO admins (user_id, authenticated_at) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET authenticated_at = excluded.authenticated_at""",
                (user_id, now),
            )

    def admins_conocidos(self) -> list[int]:
        with self._cursor() as cur:
            rows = cur.execute("SELECT user_id FROM admins").fetchall()
        return [int(r["user_id"]) for r in rows]

    # ---- Invitaciones -------------------------------------------------------

    def crear_invitacion(self, token: str, telefono: str, creado_por: int) -> Invitacion:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO invitaciones
                   (token, telefono, creado_por, created_at, estado)
                   VALUES (?, ?, ?, ?, ?)""",
                (token, telefono, creado_por, now, INVITACION_PENDIENTE),
            )
        return self.obtener_invitacion(token)

    def obtener_invitacion(self, token: str) -> Optional[Invitacion]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM invitaciones WHERE token = ? LIMIT 1", (token,)
            ).fetchone()
        if not row:
            return None
        return Invitacion(**dict(row))

    def aceptar_invitacion(self, token: str, user_id: int) -> Optional[Invitacion]:
        """Marca la invitación como aceptada por user_id (solo si seguía
        pendiente; una invitación ya usada no se reasigna)."""
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """UPDATE invitaciones SET estado = ?, aceptado_por = ?, aceptado_at = ?
                   WHERE token = ? AND estado = ?""",
                (INVITACION_ACEPTADA, user_id, now, token, INVITACION_PENDIENTE),
            )
        return self.obtener_invitacion(token)

    def listar_invitaciones(self, creado_por: int) -> list[Invitacion]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM invitaciones WHERE creado_por = ? ORDER BY created_at DESC",
                (creado_por,),
            ).fetchall()
        return [Invitacion(**dict(r)) for r in rows]

    # ---- Suscripciones (centinela) ---------------------------------------

    def suscribir(self, user_id: int, codigo_exp: str) -> bool:
        """Crea o reactiva la suscripción de un usuario a un expediente.

        Devuelve True si se creó (nueva) o False si ya existía (reactivada).
        """
        codigo_exp = (codigo_exp or "").strip()
        now = time.time()
        with self._cursor() as cur:
            existing = cur.execute(
                "SELECT id, activa FROM suscripciones WHERE user_id=? AND codigo_exp=?",
                (user_id, codigo_exp),
            ).fetchone()
            if existing:
                if existing["activa"] == 1:
                    return False
                cur.execute(
                    "UPDATE suscripciones SET activa=1 WHERE id=?",
                    (existing["id"],),
                )
                return False
            cur.execute(
                "INSERT INTO suscripciones (user_id, codigo_exp, activa, created_at) "
                "VALUES (?, ?, 1, ?)",
                (user_id, codigo_exp, now),
            )
            return True

    def desuscribir(self, user_id: int, codigo_exp: str) -> bool:
        """Desactiva la suscripción. Devuelve True si existía y estaba activa."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE suscripciones SET activa=0 WHERE user_id=? AND codigo_exp=? AND activa=1",
                (user_id, codigo_exp),
            )
            return cur.rowcount > 0

    def listar_suscripciones(self, user_id: int) -> list[Suscripcion]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM suscripciones WHERE user_id=? AND activa=1 "
                "ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return [Suscripcion(**dict(r)) for r in rows]

    def suscripciones_activas_por_exp(self) -> dict[str, list[int]]:
        """Devuelve {codigo_exp: [user_ids]} de todas las suscripciones activas."""
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT user_id, codigo_exp FROM suscripciones WHERE activa=1"
            ).fetchall()
        out: dict[str, list[int]] = {}
        for r in rows:
            out.setdefault(r["codigo_exp"], []).append(r["user_id"])
        return out

    # ---- Snapshots (detección de cambios en la ANM) -----------------------

    def obtener_snapshot(self, codigo_exp: str) -> Optional[Snapshot]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM snapshots WHERE codigo_exp=? LIMIT 1",
                (codigo_exp,),
            ).fetchone()
        if not row:
            return None
        return Snapshot(**dict(row))

    def guardar_snapshot(self, snap: Snapshot) -> None:
        """Upsert del snapshot de un expediente."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO snapshots
                   (codigo_exp, area_ha, titulo_est, etapa, modalidad,
                    fecha_de_e, fecha_de01, visto_en, release_state, release_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(codigo_exp) DO UPDATE SET
                     area_ha=excluded.area_ha, titulo_est=excluded.titulo_est,
                      etapa=excluded.etapa, modalidad=excluded.modalidad,
                      fecha_de_e=excluded.fecha_de_e, fecha_de01=excluded.fecha_de01,
                      release_state=excluded.release_state,
                      release_date=excluded.release_date,
                      visto_en=excluded.visto_en""",
                (
                    snap.codigo_exp, snap.area_ha, snap.titulo_est, snap.etapa,
                    snap.modalidad, snap.fecha_de_e, snap.fecha_de01, snap.visto_en,
                    snap.release_state, snap.release_date,
                ),
            )

    # ---- Utilidades -------------------------------------------------------

    @staticmethod
    def fmt_fecha(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
