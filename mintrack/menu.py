"""Definición de menús (inline keyboards) y textos del bot de Telegram.

Estructura del menú principal (5 opciones) más la consulta de títulos mineros:

    1. Servicios
    2. Precios
    3. Iniciar solicitud
    4. Subir documentos
    5. Estado de proceso
    6. Consultar título minero   <- mantiene la funcionalidad original

Cada botón usa un ``callback_data`` prefijado para enrutamiento en el bot.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# --- Callback data (prefijos para enrutamiento) ---------------------------

CB_MENU = "menu"
CB_SERVICIOS = "srv"
CB_SERVICIOS_APLICACION = "srv_aplic"
CB_SERVICIOS_CENTINELA = "srv_centi"
CB_SERVICIOS_MAS = "srv_mas"
CB_PRECIOS = "pre"
CB_PRECIOS_MAS = "pre_mas"
CB_INICIAR = "ini"
CB_SUBIR = "sub"
CB_ESTADO = "est"
CB_CONSULTAR = "con"          # consultar título minero
CB_VOLVER = "back"
CB_CANCELAR = "cancel"


# --- Servicios y precios (datos de negocio) ------------------------------

SERVICIOS_NOMBRES = {
    "aplicacion": "Aplicación Minera",
    "centinela": "Centinela (monitoreo)",
}


SERVICIOS = {
    "aplicacion": {
        "nombre": "Aplicación Minera",
        "resumen": (
            "Presentación y trámite de solicitudes mineras ante la ANM. "
            "Gestión integral del expediente."
        ),
        "detalle": (
            "• Radicación de solicitud de contrato de concesión.\n"
            "• Levantamiento y validación de coordenadas del área.\n"
            "• Elaboración del objeto técnico minero (OTM).\n"
            "• Seguimiento del expediente hasta inscripción."
        ),
        "precio": "$4.980.000 por área",
    },
    "centinela": {
        "nombre": "Centinela (monitoreo)",
        "resumen": (
            "Monitoreo permanente de tus títulos y áreas mineras: alertas, "
            "vencimientos y novedades."
        ),
        "detalle": (
            "• Monitoreo 24/7 del estado del título en ANNA.\n"
            "• Alertas de vencimientos, aniversarios y novedades.\n"
            "• Reportes periódicos del expediente.\n"
            "• Detección de solapamientos o intrusiones."
        ),
        "precio": "$2.790.000 inicial + $1.320.000 diario",
    },
}


# --- Keyboards ------------------------------------------------------------

def menu_principal_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📌 Servicios", callback_data=CB_SERVICIOS)],
            [InlineKeyboardButton("💰 Precios", callback_data=CB_PRECIOS)],
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
    return _con_volver(
        [
            [InlineKeyboardButton("Aplicación Minera", callback_data=CB_SERVICIOS_APLICACION)],
            [InlineKeyboardButton("Centinela (monitoreo)", callback_data=CB_SERVICIOS_CENTINELA)],
        ]
    )


def servicios_detalle_kb(servicio: str) -> InlineKeyboardMarkup:
    return _con_volver(
        [
            [
                InlineKeyboardButton("🔍 Ver más", callback_data=CB_SERVICIOS_MAS),
                InlineKeyboardButton("🚀 Iniciar solicitud", callback_data=CB_INICIAR),
            ],
        ]
    )


def precios_kb() -> InlineKeyboardMarkup:
    return _con_volver(
        [
            [InlineKeyboardButton("¿Qué incluye cada servicio?", callback_data=CB_PRECIOS_MAS)],
            [InlineKeyboardButton("🚀 Iniciar solicitud", callback_data=CB_INICIAR)],
        ]
    )


def estado_kb() -> InlineKeyboardMarkup:
    return _con_volver([])


def consultar_kb() -> InlineKeyboardMarkup:
    return _con_volver([])


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
    "Elige un servicio para ver el resumen:"
)

TEXTO_PRECIOS = (
    "💰 *Precios*\n\n"
    "• *Aplicación Minera:* $4.980.000 por área\n"
    "• *Centinela (monitoreo):* $2.790.000 inicial + $1.320.000 diario\n"
)


def texto_servicio_resumen(key: str) -> str:
    s = SERVICIOS[key]
    return f"📌 *{s['nombre']}*\n\n{s['resumen']}\n\n💰 {s['precio']}"


def texto_servicio_detalle(key: str) -> str:
    s = SERVICIOS[key]
    return f"📌 *{s['nombre']}* — detalle\n\n{s['detalle']}\n\n💰 {s['precio']}"


TEXTO_PRECIOS_MAS = (
    "💰 *Precios — qué incluye*\n\n"
    "• *Aplicación Minera ($4.980.000 por área):* radicación, coordenadas, OTM "
    "y seguimiento del expediente hasta inscripción.\n\n"
    "• *Centinela ($2.790.000 inicial + $1.320.000 diario):* monitoreo 24/7, "
    "alertas de vencimientos/novedades y reportes periódicos.\n"
)


# Estados del wizard de "Iniciar solicitud" (ConversationHandler).
W_EMPRESA, W_CONTACTO, W_TELEFONO, W_SERVICIO, W_CONFIRMACION = range(5)

W_SERVICIO_TECLAS = {
    "1": "aplicacion",
    "2": "centinela",
}
