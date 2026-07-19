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

from . import servicios as S

# --- Callback data (prefijos para enrutamiento) ---------------------------

CB_MENU = "menu"
CB_SERVICIOS = "srv"
CB_SERVICIO_PREFIX = "srv_"   # srv_<codigo> muestra el resumen del servicio
CB_SERVICIOS_MAS = "srv_mas"
CB_PRECIOS = "pre"
CB_PRECIOS_MAS = "pre_mas"
CB_INICIAR = "ini"
CB_SUBIR = "sub"
CB_ESTADO = "est"
CB_CONSULTAR = "con"          # consultar título minero
CB_CENTINELA = "cnt"          # suscribirse / mis suscripciones (menú centinela)
CB_MIS_SUBS = "cnt_mis"
CB_VOLVER = "back"
CB_CANCELAR = "cancel"

# Prefijos para cancelar una suscripción concreta desde "mis suscripciones".
CB_DESUSCRIBIR_PREFIX = "cnt_del_"


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
            [InlineKeyboardButton("🔔 Centinela (suscribir)", callback_data=CB_CENTINELA)],
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


def centinela_kb() -> InlineKeyboardMarkup:
    return _con_volver(
        [
            [InlineKeyboardButton("📋 Mis suscripciones", callback_data=CB_MIS_SUBS)],
        ]
    )


def desuscribir_kb(codigo_exp: str) -> InlineKeyboardMarkup:
    """Keyboard con un botón para cancelar la suscripción a un expediente."""
    return _con_volver(
        [[InlineKeyboardButton(f"🔕 Cancelar suscripción a {codigo_exp}",
                               callback_data=f"{CB_DESUSCRIBIR_PREFIX}{codigo_exp}")]]
    )


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
    "o en conjunto. Elige uno para ver el resumen:"
)

TEXTO_PRECIOS = "💰 *Precios*\n\n" + "\n".join(
    f"• *{s.nombre}:* {s.precio}" for s in S.SERVICIOS.values()
) + "\n"


def texto_servicio_resumen(key: str) -> str:
    s = S.SERVICIOS[key]
    return f"📌 *{s.nombre}*\n\n{s.resumen}\n\n💰 {s.precio}"


def texto_servicio_detalle(key: str) -> str:
    s = S.SERVICIOS[key]
    return f"📌 *{s.nombre}* — detalle\n\n{s.detalle}\n\n💰 {s.precio}"


TEXTO_PRECIOS_MAS = "💰 *Precios — qué incluye*\n\n" + "\n\n".join(
    f"• *{s.nombre} ({s.precio}):* {s.resumen}" for s in S.SERVICIOS.values()
)

TEXTO_CENTINELA = (
    "🔔 *Centinela*\n\n"
    "Suscríbete a un código de expediente para recibir notificaciones "
    "automáticas cuando:\n"
    "• Se libere parte del área del título (reducción de ha).\n"
    "• Cambie el estado del título (ej. Activo → En liquidación).\n"
    "• Cambie la etapa (ej. Exploración → Explotación).\n"
    "• Se acerque el vencimiento (<=30 días).\n\n"
    "Para suscribirte escribe el código del expediente "
    "(formato AAA-#####, ej. ICQ-09083)."
)


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
