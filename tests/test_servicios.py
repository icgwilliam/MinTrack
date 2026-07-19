"""Pruebas del catálogo de servicios BR-001 y su integración con el bot."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault(
    "MINTRACK_DB_PATH",
    os.path.join(tempfile.gettempdir(), "mintrack_test_servicios.db"),
)

from mintrack import servicios as S
from mintrack import menu as M
from mintrack.db import Database


class CatalogoServiciosTests(unittest.TestCase):
    def test_br001_cuatro_servicios_independientes(self) -> None:
        self.assertEqual(
            list(S.SERVICIOS),
            [S.ALISTAMIENTO, S.MONITOREO, S.RADICACION, S.PAQUETE_INTEGRAL],
        )

    def test_br002_paquete_integral_incluye_los_tres(self) -> None:
        paquete = S.SERVICIOS[S.PAQUETE_INTEGRAL]
        self.assertEqual(
            paquete.incluye, (S.ALISTAMIENTO, S.MONITOREO, S.RADICACION)
        )
        self.assertIn("preferencial", paquete.precio)

    def test_tarifas_documentadas(self) -> None:
        self.assertEqual(S.SERVICIOS[S.ALISTAMIENTO].precio, "$1.000.000")
        self.assertEqual(S.SERVICIOS[S.MONITOREO].precio, "$2.000.000 por área / año")
        self.assertEqual(S.SERVICIOS[S.RADICACION].precio, "$20.000.000")

    def test_aliases_heredados(self) -> None:
        self.assertEqual(S.resolver("aplicacion"), S.RADICACION)
        self.assertEqual(S.resolver("centinela"), S.MONITOREO)
        self.assertEqual(S.nombre("centinela"), "Monitoreo automatizado")
        self.assertEqual(S.nombre("desconocido"), "desconocido")

    def test_parsear_seleccion_individual(self) -> None:
        self.assertEqual(S.parsear_seleccion("2"), [S.MONITOREO])
        self.assertEqual(S.parsear_seleccion("monitoreo"), [S.MONITOREO])

    def test_parsear_seleccion_combinada(self) -> None:
        self.assertEqual(
            S.parsear_seleccion("1,3"), [S.ALISTAMIENTO, S.RADICACION]
        )
        self.assertEqual(
            S.parsear_seleccion("1, 2, 3"),
            [S.ALISTAMIENTO, S.MONITOREO, S.RADICACION],
        )

    def test_parsear_seleccion_sin_duplicados(self) -> None:
        self.assertEqual(S.parsear_seleccion("1,1,2"), [S.ALISTAMIENTO, S.MONITOREO])

    def test_parsear_seleccion_paquete_es_excluyente(self) -> None:
        self.assertEqual(S.parsear_seleccion("4"), [S.PAQUETE_INTEGRAL])
        with self.assertRaises(ValueError):
            S.parsear_seleccion("1,4")

    def test_parsear_seleccion_invalida(self) -> None:
        for invalida in ("", "0", "5", "xyz"):
            with self.assertRaises(ValueError, msg=invalida):
                S.parsear_seleccion(invalida)

    def test_nombres_csv(self) -> None:
        self.assertEqual(
            S.nombres_csv("monitoreo,radicacion"),
            "Monitoreo automatizado, Radicación automatizada",
        )
        self.assertEqual(S.nombres_csv(""), "")


class MenuServiciosTests(unittest.TestCase):
    def test_teclado_servicios_dinamico(self) -> None:
        kb = M.servicios_kb()
        botones = [b for fila in kb.inline_keyboard for b in fila]
        callbacks = [b.callback_data for b in botones]
        for codigo in S.SERVICIOS:
            self.assertIn(f"{M.CB_SERVICIO_PREFIX}{codigo}", callbacks)
        self.assertIn(M.CB_VOLVER, callbacks)

    def test_textos_precios_desde_catalogo(self) -> None:
        for servicio in S.SERVICIOS.values():
            self.assertIn(servicio.nombre, M.TEXTO_PRECIOS)
            self.assertIn(servicio.precio, M.TEXTO_PRECIOS)
            self.assertIn(servicio.nombre, M.TEXTO_PRECIOS_MAS)

    def test_texto_wizard_lista_opciones(self) -> None:
        texto = M.texto_wizard_servicios()
        for servicio in S.SERVICIOS.values():
            self.assertIn(servicio.nombre, texto)
        self.assertIn("1,3", texto)

    def test_resumen_y_detalle(self) -> None:
        resumen = M.texto_servicio_resumen(S.MONITOREO)
        self.assertIn("Monitoreo automatizado", resumen)
        detalle = M.texto_servicio_detalle(S.ALISTAMIENTO)
        self.assertIn("BR-011", detalle)


class SolicitudMultiServicioTests(unittest.TestCase):
    def test_solicitud_guarda_varios_servicios(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(
                1, "Empresa", "Contacto", "+57 300", "monitoreo,radicacion"
            )
            self.assertIsNotNone(sol)
            self.assertEqual(sol.servicios, ["monitoreo", "radicacion"])
            self.assertEqual(
                S.nombres_csv(sol.servicio),
                "Monitoreo automatizado, Radicación automatizada",
            )
            db.close()

    def test_solicitud_heredada_con_codigos_antiguos(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(1, "Empresa", "Contacto", "+57 300", "centinela")
            self.assertIsNotNone(sol)
            self.assertEqual(S.nombres_csv(sol.servicio), "Monitoreo automatizado")
            db.close()


if __name__ == "__main__":
    unittest.main()
