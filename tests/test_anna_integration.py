from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from mintrack import centinela
from mintrack.bot import MAX_FIELD_LEN, _formatar_titulo
from mintrack.client import ANMClient
from mintrack.db import Database, Snapshot
from mintrack.models import TituloMinero


class AnnaIntegrationTests(unittest.TestCase):
    def test_bot_prioritizes_sar_and_limits_long_fields(self) -> None:
        title = TituloMinero(
            codigo_exp="ARE-509209",
            minerales="X" * (MAX_FIELD_LEN + 100),
            extras={
                "release_analysis": {
                    "state": "LIBERACION_PROGRAMADA",
                    "message": "Existe fecha oficial.",
                    "releaseAtColombia": "2026-07-21T07:30:00-05:00",
                }
            },
        )

        text = _formatar_titulo(title)

        self.assertLess(text.index("Liberación de área"), text.index("Minerales:"))
        self.assertIn("2026-07-21T07:30:00-05:00", text)
        self.assertIn("... (resumido)", text)

    def test_report_is_adapted_to_existing_model(self) -> None:
        report = {
            "releaseAnalysis": {
                "state": "LIBERACION_PROGRAMADA",
                "signals": {"releaseDate": 1_800_000_000_000},
            },
            "titles": {
                "exact": [
                    {
                        "tenureId": "ABC-123",
                        "tenureStatus": {"code": "A", "description": "Activo"},
                        "tenureStage": {"code": "EXPT", "description": "Explotación"},
                        "tenureType": {"code": "CC", "description": "Concesión"},
                        "submissionDate": 1_700_000_000_000,
                    }
                ],
                "related": [],
            },
        }

        titles = ANMClient._titulos_desde_reporte(report, exactos=True)

        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0].codigo_exp, "ABC-123")
        self.assertEqual(titles[0].titulo_est, "Activo")
        self.assertEqual(
            titles[0].extras["release_analysis"]["state"],
            "LIBERACION_PROGRAMADA",
        )

    def test_centinela_notifies_sar_publication(self) -> None:
        title = TituloMinero(
            codigo_exp="ABC-123",
            extras={
                "release_analysis": {
                    "state": "ACTO_EN_FIRME_SIN_FECHA_LIBERACION",
                    "releaseAtColombia": None,
                    "signals": {"releaseDate": None},
                }
            },
        )
        previous = Snapshot(
            "ABC-123",
            None,
            "Activo",
            None,
            None,
            None,
            None,
            1.0,
            "SIN_PUBLICACION_SAR",
            None,
        )

        events = centinela.comparar(title, previous)

        self.assertEqual([event.tipo for event in events], ["liberacion_publicada"])

    def test_existing_database_gets_sar_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mintrack.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE snapshots (
                    codigo_exp TEXT PRIMARY KEY,
                    area_ha REAL,
                    titulo_est TEXT,
                    etapa TEXT,
                    modalidad TEXT,
                    fecha_de_e REAL,
                    fecha_de01 REAL,
                    visto_en REAL NOT NULL
                )"""
            )
            connection.commit()
            connection.close()
            db = Database(str(path))
            snapshot = Snapshot(
                "ABC-123",
                None,
                "Activo",
                None,
                None,
                None,
                None,
                1.0,
                "SIN_PUBLICACION_SAR",
                None,
            )
            db.guardar_snapshot(snapshot)

            stored = db.obtener_snapshot("ABC-123")
            db.close()

        self.assertIsNotNone(stored)
        self.assertEqual(stored.release_state, "SIN_PUBLICACION_SAR")


if __name__ == "__main__":
    unittest.main()
