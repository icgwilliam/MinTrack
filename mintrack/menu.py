"""Definición de menús (inline keyboards) y textos del bot de Telegram.

Estructura del menú principal:

    1. Servicios
    2. Iniciar solicitud
    3. Subir documentos
    4. Estado de proceso
    5. Consultar título minero

Cada botón usa un ``callback_data`` prefijado para enrutamiento en el bot.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import servicios as S

# --- Callback data (prefijos para enrutamiento) ---------------------------

CB_MENU = "menu"
CB_SERVICIOS = "srv"
CB_SERVICIO_PREFIX = "srv_"    # srv_<codigo> muestra la ficha del servicio
CB_PRECIO_PREFIX = "pre_"      # pre_<codigo> muestra el precio de un servicio
CB_INICIAR = "ini"
CB_INICIAR_PREFIX = "ini_"     # ini_<codigo> inicia solicitud con ese servicio
CB_SUBIR = "sub"
CB_ESTADO = "est"
CB_CONSULTAR = "con"           # consultar título minero
CB_VOLVER = "back"
CB_CANCELAR = "cancel"

# Prefijos para cancelar una suscripción concreta desde "mis suscripciones".
CB_DESUSCRIBIR_PREFIX = "cnt_del_"


# --- Keyboards ------------------------------------------------------------

def menu_principal_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📌 Servicios", callback_data=CB_SERVICIOS)],
            [InlineKeyboardButton("🚀 Iniciar solicitud", callback_data=CB_INICIAR)],
            [InlineKeyboardButton("📄 Subir documentos", callback_data=CB_SUBIR)],
            [InlineKeyboardButton("📊 Estado de proceso", callback_data=CB_ESTADO)],
            [InlineKeyboardButton("⛏️ Consultar título minero", callback_data=CB_CONSULTAR)],
        ]
    )


def _con_volver(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    rows.append([InlineKeyboardButton("⬅️ Volver al menú", callback_data=CB_VOLVER)])
    return InlineKeyboardMarkup(rows)


def servicios_kb() -> InlineKeyboardMarkup:
    """Un botón por servicio del catálogo BR-001 (se amplía solo al catálogo)."""
    return _con_volver(
        [
            [InlineKeyboardButton(s.nombre, callback_data=f"{CB_SERVICIO_PREFIX}{s.codigo}")]
            for s in S.SERVICIOS.values()
        ]
    )


def servicio_kb(codigo: str) -> InlineKeyboardMarkup:
    """Ficha del servicio: ver precio e iniciar solicitud con ese servicio."""
    return _con_volver(
        [
            [
                InlineKeyboardButton("💰 Ver precio", callback_data=f"{CB_PRECIO_PREFIX}{codigo}"),
                InlineKeyboardButton("🚀 Iniciar solicitud", callback_data=f"{CB_INICIAR_PREFIX}{codigo}"),
            ],
        ]
    )


def precio_kb(codigo: str) -> InlineKeyboardMarkup:
    return _con_volver(
        [
            [InlineKeyboardButton("🚀 Iniciar solicitud", callback_data=f"{CB_INICIAR_PREFIX}{codigo}")],
        ]
    )


def estado_kb() -> InlineKeyboardMarkup:
    return _con_volver([])


def consultar_kb() -> InlineKeyboardMarkup:
    return _con_volver([])


def desuscribir_kb(codigo_exp: str) -> str:
    """Callback para cancelar la suscripción a un expediente (uso interno)."""
    return f"{CB_DESUSCRIBIR_PREFIX}{codigo_exp}"


def cancelar_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancelar", callback_data=CB_CANCELAR)]]
    )


# --- Textos ---------------------------------------------------------------

TEXTO_BIENVENIDA = (
    "👋 Bienvenido a MinTrack\n\n"
    "Consultoría y trámite minero en Colombia. Selecciona una opción del menú:"
)

TEXTO_MENU = (
    "Menú principal — selecciona una opción:"
)

TEXTO_SERVICIOS = (
    "📌 *Servicios*\n\n"
    "Cuatro servicios independientes que puedes contratar de manera individual "
    "o en conjunto. Elige uno para ver la información completa:"
)


def texto_servicio(codigo: str) -> str:
    """Ficha completa del servicio (resumen + detalle), sin precio."""
    s = S.SERVICIOS[codigo]
    return f"📌 *{s.nombre}*\n\n{s.resumen}\n\n{s.detalle}"


def texto_precio_servicio(codigo: str) -> str:
    """Tarifa del servicio con el próximo paso del flujo de contratación."""
    s = S.SERVICIOS[codigo]
    return f"💰 *{s.nombre}*\n\nTarifa: *{s.precio}*\n\n{s.siguiente_paso}"


# Estados del wizard de "Iniciar solicitud" (ConversationHandler).
W_EMPRESA, W_CONTACTO, W_TELEFONO, W_SERVICIO, W_CONFIRMACION = range(5)


def texto_wizard_servicios() -> str:
    """Paso del wizard: selección individual o combinada de servicios."""
    return (
        "Paso 4/4 — Selecciona el *servicio o los servicios* a contratar:\n\n"
        f"{S.texto_opciones()}\n\n"
        "Responde con un número (ej. 2) o varios separados por coma "
        "(ej. 1,3). El Paquete Integral se selecciona solo (4)."
    )
