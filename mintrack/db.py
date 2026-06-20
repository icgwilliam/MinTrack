"""Capa de persistencia (SQLite) para el bot de Telegram.

Modelo de datos simple:

* ``solicitudes``: una solicitud creada por un usuario con los datos del wizard
  (empresa, contacto, teléfono, tipo de servicio) y un estado que avanza
  automáticamente conforme se suben documentos y pasa el tiempo.
* ``documentos``: archivos que el usuario envía, referenciados a una solicitud.

El estado de cada solicitud avanza por una máquina de estados interna:

    EN_REVISION -> EN_PROCESO -> CENTINELA_ACTIVO -> COMPLETADO

con transiciones automáticas (al subir el primer documento pasa a EN_PROCESO,
y hay una regla de tiempo para avanzar).
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

# Estados de una solicitud.
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
    ESTADO_EN_REVISION: "En revisión",
    ESTADO_EN_PROCESO: "En proceso de aplicación",
    ESTADO_CENTINELA: "Centinela activo",
    ESTADO_COMPLETADO: "Completado",
}

# Tiempos mínimos (en segundos) que una solicitud debe permanecer en cada estado
# antes de avanzar automáticamente al siguiente. Valores cortos para que el demo
# sea observable. Para producción, ajustar (p. ej. días).
ESTADO_DURACION = {
    ESTADO_EN_REVISION: 2 * 60,      # 2 min
    ESTADO_EN_PROCESO: 5 * 60,        # 5 min
    ESTADO_CENTINELA: 5 * 60,          # 5 min
    # COMPLETADO es terminal.
}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS solicitudes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    empresa TEXT NOT NULL,
    contacto TEXT NOT NULL,
    telefono TEXT NOT NULL,
    servicio TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'EN_REVISION',
    created_at REAL NOT NULL,
    estado_desde REAL NOT NULL,
    UNIQUE(user_id)   -- una solicitud activa por usuario; simplifica el flujo
);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solicitud_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    file_name TEXT,
    tipo TEXT NOT NULL,           -- 'pdf' | 'imagen' | 'shape' | 'otro'
    ruta TEXT,                    -- ruta local guardada (opcional)
    created_at REAL NOT NULL,
    FOREIGN KEY (solicitud_id) REFERENCES solicitudes(id)
);

CREATE INDEX IF NOT EXISTS idx_documentos_solicitud ON documentos(solicitud_id);
CREATE INDEX IF NOT EXISTS idx_documentos_user ON documentos(user_id);
"""


@dataclass
class Solicitud:
    id: int
    user_id: int
    empresa: str
    contacto: str
    telefono: str
    servicio: str
    estado: str
    created_at: float
    estado_desde: float

    @property
    def estado_label(self) -> str:
        return ESTADO_LABELS.get(self.estado, self.estado)


class Database:
    """Wrapper thread-safe sobre sqlite3 (con check_same_thread=False + lock)."""

    def __init__(self, path: str = DEFAULT_DB_PATH) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

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

    # ---- Solicitudes ------------------------------------------------------

    def crear_solicitud(
        self,
        user_id: int,
        empresa: str,
        contacto: str,
        telefono: str,
        servicio: str,
    ) -> Optional[Solicitud]:
        """Crea (o reemplaza) la solicitud activa del usuario."""
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM solicitudes WHERE user_id = ?", (user_id,)
            )
            cur.execute(
                "DELETE FROM documentos WHERE user_id = ?", (user_id,)
            )
            cur.execute(
                """INSERT INTO solicitudes
                   (user_id, empresa, contacto, telefono, servicio, estado,
                    created_at, estado_desde)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    empresa,
                    contacto,
                    telefono,
                    servicio,
                    ESTADO_EN_REVISION,
                    now,
                    now,
                ),
            )
            sid = cur.lastrowid
        return self.obtener_solicitud(user_id)

    def obtener_solicitud(self, user_id: int) -> Optional[Solicitud]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM solicitudes WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
        if not row:
            return None
        return Solicitud(**dict(row))

    def obtener_solicitud_por_id(self, solicitud_id: int) -> Optional[Solicitud]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM solicitudes WHERE id = ? LIMIT 1", (solicitud_id,)
            ).fetchone()
        if not row:
            return None
        return Solicitud(**dict(row))

    def _set_estado(self, solicitud: Solicitud, nuevo_estado: str) -> None:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE solicitudes SET estado = ?, estado_desde = ? WHERE id = ?",
                (nuevo_estado, now, solicitud.id),
            )

    def avanzar_estado(self, user_id: int) -> Optional[Solicitud]:
        """Avanza una solicitud al siguiente estado (manual/admin o reglas).

        Devuelve la solicitud actualizada o ``None`` si no existe.
        """
        sol = self.obtener_solicitud(user_id)
        if not sol:
            return None
        idx = ESTADOS_ORDEN.index(sol.estado) if sol.estado in ESTADOS_ORDEN else -1
        if idx < 0 or idx >= len(ESTADOS_ORDEN) - 1:
            return sol  # terminal o desconocido
        self._set_estado(sol, ESTADOS_ORDEN[idx + 1])
        return self.obtener_solicitud(user_id)

    def sincronizar_estado(self, user_id: int) -> Optional[Solicitud]:
        """Aplica reglas automáticas de avance de estado por tiempo/subida.

        Reglas:
        * EN_REVISION -> EN_PROCESO si ya tiene al menos 1 documento subido.
        * Cualquier estado no terminal -> siguiente si transcurrió la duración
          mínima definida en ``ESTADO_DURACION``.
        """
        sol = self.obtener_solicitud(user_id)
        if not sol:
            return None
        if sol.estado == ESTADO_COMPLETADO:
            return sol

        now = time.time()
        # Regla: primer documento avanza de EN_REVISION a EN_PROCESO.
        if sol.estado == ESTADO_EN_REVISION and self.contar_documentos(user_id) > 0:
            self._set_estado(sol, ESTADO_EN_PROCESO)
            sol = self.obtener_solicitud(user_id)

        # Regla: avance por tiempo.
        if sol and sol.estado in ESTADO_DURACION:
            if now - sol.estado_desde >= ESTADO_DURACION[sol.estado]:
                idx = ESTADOS_ORDEN.index(sol.estado)
                if idx < len(ESTADOS_ORDEN) - 1:
                    self._set_estado(sol, ESTADOS_ORDEN[idx + 1])
                    sol = self.obtener_solicitud(user_id)
        return sol

    # ---- Documentos ------------------------------------------------------

    def registrar_documento(
        self,
        user_id: int,
        file_id: str,
        file_name: Optional[str],
        tipo: str,
        ruta: Optional[str] = None,
    ) -> int:
        sol = self.obtener_solicitud(user_id)
        solicitud_id = sol.id if sol else 0
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO documentos
                   (solicitud_id, user_id, file_id, file_name, tipo, ruta, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (solicitud_id, user_id, file_id, file_name, tipo, ruta, now),
            )
            return cur.lastrowid

    def contar_documentos(self, user_id: int) -> int:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT COUNT(*) AS n FROM documentos WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def listar_documentos(self, user_id: int) -> list[sqlite3.Row]:
        with self._cursor() as cur:
            return cur.execute(
                "SELECT * FROM documentos WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()

    # ---- Utilidades -------------------------------------------------------

    @staticmethod
    def fmt_fecha(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
