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
from mintrack import bot as B
from mintrack.db import Database, ESTADO_EN_REVISION, ESTADO_EN_PROCESO, PAGO_PENDIENTE, PAGO_EN_REVISION, PAGO_CONFIRMADO


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
            texto = M.texto_precio_servicio(servicio.codigo)
            self.assertIn(servicio.nombre, texto)
            self.assertIn(servicio.precio, texto)
            self.assertIn(servicio.siguiente_paso, texto)

    def test_ficha_servicio_sin_referencias_internas(self) -> None:
        for codigo in S.SERVICIOS:
            ficha = M.texto_servicio(codigo)
            self.assertNotIn("BR-", ficha)

    def test_teclado_servicio_tiene_ver_precio_e_iniciar(self) -> None:
        kb = M.servicio_kb(S.MONITOREO)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn(f"{M.CB_PRECIO_PREFIX}{S.MONITOREO}", callbacks)
        self.assertIn(f"{M.CB_INICIAR_PREFIX}{S.MONITOREO}", callbacks)
        self.assertIn(M.CB_VOLVER, callbacks)

    def test_menu_principal_solo_tres_botones(self) -> None:
        kb = M.menu_principal_kb()
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertEqual(callbacks, [M.CB_SERVICIOS, M.CB_ESTADO, M.CB_CONSULTAR])
        self.assertNotIn("ini", callbacks)
        self.assertNotIn("sub", callbacks)
        self.assertNotIn("cnt", callbacks)

    def test_procesos_kb_un_boton_por_proceso(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            s1 = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.ALISTAMIENTO)
            s2 = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            kb = M.procesos_kb(db.listar_solicitudes(1))
            callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
            self.assertIn(f"{M.CB_PROCESO_PREFIX}{s1.id}", callbacks)
            self.assertIn(f"{M.CB_PROCESO_PREFIX}{s2.id}", callbacks)
            db.close()

    def test_proceso_kb_tiene_documentos_y_pago(self) -> None:
        kb = M.proceso_kb(7)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn(f"{M.CB_DOC_PREFIX}7", callbacks)
        self.assertIn(f"{M.CB_PAGO_PREFIX}7", callbacks)

    def test_admin_proceso_kb_confirmar_pago_solo_si_pendiente(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            kb_pendiente = M.admin_proceso_kb(sol)
            callbacks_pendiente = [b.callback_data for row in kb_pendiente.inline_keyboard for b in row]
            self.assertIn(f"{M.CB_ADMIN_PAGO_OK_PREFIX}{sol.id}", callbacks_pendiente)

            sol_confirmado = db.confirmar_pago(sol.id)
            kb_confirmado = M.admin_proceso_kb(sol_confirmado)
            callbacks_confirmado = [b.callback_data for row in kb_confirmado.inline_keyboard for b in row]
            self.assertNotIn(f"{M.CB_ADMIN_PAGO_OK_PREFIX}{sol.id}", callbacks_confirmado)
            db.close()


class ProcesosMultiplesTests(unittest.TestCase):
    """Un usuario puede tener varios procesos activos a la vez (uno por servicio)."""

    def test_crear_varios_procesos_no_se_sobrescriben(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.ALISTAMIENTO)
            db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            procesos = db.listar_solicitudes(1)
            self.assertEqual(len(procesos), 2)
            self.assertEqual({p.servicio for p in procesos}, {S.ALISTAMIENTO, S.MONITOREO})
            db.close()

    def test_listar_solicitudes_filtra_por_usuario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            db.crear_solicitud(1, "Empresa A", "900.111.111-1", "3001112233", S.ALISTAMIENTO)
            db.crear_solicitud(2, "Empresa B", "900.222.222-2", "3009998877", S.MONITOREO)
            self.assertEqual(len(db.listar_solicitudes(1)), 1)
            self.assertEqual(len(db.listar_solicitudes(2)), 1)
            db.close()

    def test_documentos_quedan_asociados_al_proceso_correcto(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            s1 = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.ALISTAMIENTO)
            s2 = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            db.registrar_documento(s1.id, 1, "file1", "a.pdf", "pdf")
            self.assertEqual(db.contar_documentos(s1.id), 1)
            self.assertEqual(db.contar_documentos(s2.id), 0)
            db.close()


class PagoYEstadoTests(unittest.TestCase):
    """El proceso queda 'En revisión' hasta que el admin confirma el pago."""

    def test_proceso_nace_en_revision_con_pago_pendiente(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            self.assertEqual(sol.estado, ESTADO_EN_REVISION)
            self.assertEqual(sol.pago_estado, PAGO_PENDIENTE)
            db.close()

    def test_sincronizar_estado_no_avanza_en_revision_por_tiempo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            with db._cursor() as cur:
                cur.execute("UPDATE solicitudes SET estado_desde = 0 WHERE id = ?", (sol.id,))
            sincronizada = db.sincronizar_estado(sol.id)
            self.assertEqual(sincronizada.estado, ESTADO_EN_REVISION)
            db.close()

    def test_marcar_pago_en_revision_al_subir_comprobante(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            actualizado = db.marcar_pago_en_revision(sol.id)
            self.assertEqual(actualizado.pago_estado, PAGO_EN_REVISION)
            self.assertEqual(actualizado.estado, ESTADO_EN_REVISION)
            db.close()

    def test_confirmar_pago_avanza_el_estado(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            db.marcar_pago_en_revision(sol.id)
            confirmado = db.confirmar_pago(sol.id)
            self.assertEqual(confirmado.pago_estado, PAGO_CONFIRMADO)
            self.assertEqual(confirmado.estado, ESTADO_EN_PROCESO)
            db.close()

    def test_avanzar_y_retroceder_estado_manualmente(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", S.MONITOREO)
            db.confirmar_pago(sol.id)  # EN_PROCESO
            avanzado = db.avanzar_estado(sol.id)
            self.assertEqual(avanzado.estado, "CENTINELA_ACTIVO")
            retrocedido = db.retroceder_estado(sol.id)
            self.assertEqual(retrocedido.estado, ESTADO_EN_PROCESO)
            db.close()


class AdminsTests(unittest.TestCase):
    def test_registrar_y_listar_admins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            db.registrar_admin(42)
            db.registrar_admin(42)  # idempotente
            self.assertEqual(db.admins_conocidos(), [42])
            db.close()


class TelefonoColombiaTests(unittest.TestCase):
    def test_acepta_celular_simple(self) -> None:
        self.assertEqual(B._telefono_normalizado("3001234567"), "3001234567")

    def test_acepta_con_prefijo_y_separadores(self) -> None:
        self.assertEqual(B._telefono_normalizado("+57 300 123 4567"), "3001234567")
        self.assertEqual(B._telefono_normalizado("57-300-123-4567"), "3001234567")

    def test_rechaza_formato_invalido(self) -> None:
        for invalido in ("12345", "3001234567890", "6011234567", "abc"):
            self.assertIsNone(B._telefono_normalizado(invalido))


class SolicitudMultiServicioTests(unittest.TestCase):
    def test_solicitud_guarda_servicio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "test.db"))
            sol = db.crear_solicitud(
                1, "Empresa", "900.123.456-7", "3001112233", "monitoreo,radicacion"
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
            sol = db.crear_solicitud(1, "Empresa", "900.123.456-7", "3001112233", "centinela")
            self.assertIsNotNone(sol)
            self.assertEqual(S.nombres_csv(sol.servicio), "Monitoreo automatizado")
            db.close()


class FlujoSolicitudTests(unittest.TestCase):
    def test_mensaje_cierre_por_servicio(self) -> None:
        self.assertIn("Subir documentos", B._mensaje_cierre([S.ALISTAMIENTO]))
        self.assertIn("código del área", B._mensaje_cierre([S.MONITOREO]))
        self.assertIn("credenciales", B._mensaje_cierre([S.RADICACION]))
        paquete = B._mensaje_cierre([S.PAQUETE_INTEGRAL])
        self.assertIn("Paquete Integral", paquete)
        self.assertIn("Subir documentos", paquete)

    def test_mensaje_cierre_combinado(self) -> None:
        texto = B._mensaje_cierre([S.ALISTAMIENTO, S.MONITOREO])
        self.assertIn("Subir documentos", texto)
        self.assertIn("código del área", texto)


if __name__ == "__main__":
    unittest.main()
