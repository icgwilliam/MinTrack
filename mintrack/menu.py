"""Definición de menús (inline keyboards) y textos del bot de Telegram.

Estructura del menú principal:

    1. Servicios
    2. Mis procesos
    3. Consultar título minero

"Iniciar solicitud" ya no es un botón del menú principal: se llega a él desde
la ficha de un servicio concreto (BR-001). "Subir documentos" y "Subir
soporte de pago" ya no son botones del menú principal tampoco: se llega a
ellos desde el detalle de un proceso en "Mis procesos", porque un usuario
puede tener varios procesos a la vez y cada acción aplica a uno concreto.

El panel de administrador (PIN por chat, ver ``bot.py``) usa sus propios
teclados, con el prefijo ``adm``/``av``/``ap``/``aa``/``ar``.

Cada botón usa un ``callback_data`` prefijado para enrutamiento en el bot.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import db as D
from . import servicios as S

# --- Callback data (prefijos para enrutamiento) ---------------------------

CB_MENU = "menu"
CB_SERVICIOS = "srv"
CB_SERVICIO_PREFIX = "srv_"    # srv_<codigo> muestra la ficha del servicio
CB_PRECIO_PREFIX = "pre_"      # pre_<codigo> muestra el precio de un servicio
CB_INICIAR_PREFIX = "ini_"     # ini_<codigo> inicia solicitud con ese servicio
CB_ESTADO = "est"              # "Mis procesos": lista los procesos del usuario
CB_PROCESO_PREFIX = "proc_"    # proc_<id> muestra el detalle de un proceso
CB_DOC_PREFIX = "doc_"         # doc_<id> inicia la subida de documentos de ese proceso
CB_PAGO_PREFIX = "pay_"        # pay_<id> inicia la subida del soporte de pago
CB_CONSULTAR = "con"           # consultar título minero
CB_VOLVER = "back"
CB_CANCELAR = "cancel"

# Prefijos para cancelar una suscripción concreta desde "mis suscripciones".
CB_DESUSCRIBIR_PREFIX = "cnt_del_"

# --- Panel admin ------------------------------------------------------------

CB_ADMIN_MENU = "adm"                  # volver al listado de procesos (admin)
CB_ADMIN_SALIR = "adx"                 # salir del panel admin
CB_ADMIN_VER_PREFIX = "av_"            # av_<id> ver detalle de un proceso
CB_ADMIN_PAGO_OK_PREFIX = "ap_"        # ap_<id> confirmar pago
CB_ADMIN_AVANZAR_PREFIX = "aa_"        # aa_<id> avanzar estado
CB_ADMIN_RETROCEDER_PREFIX = "ar_"     # ar_<id> retroceder estado
CB_ADMIN_DOCS_PREFIX = "ad_"           # ad_<id> reenviar documentos al admin
CB_ADMIN_INVITAR = "adi"               # iniciar el flujo de invitar un cliente
CB_ADMIN_INVITACIONES = "adn"          # ver las invitaciones enviadas


# --- Keyboards ------------------------------------------------------------

def menu_principal_kb(es_admin: bool = False) -> InlineKeyboardMarkup:
    """Menú principal. 'Consultar título minero' es solo para el admin: para
    un cliente compite con el servicio de Monitoreo automatizado."""
    filas = [
        [InlineKeyboardButton("📌 Servicios", callback_data=CB_SERVICIOS)],
        [InlineKeyboardButton("📊 Mis procesos", callback_data=CB_ESTADO)],
    ]
    if es_admin:
        filas.append(
            [InlineKeyboardButton("⛏️ Consultar título minero", callback_data=CB_CONSULTAR)]
        )
    return InlineKeyboardMarkup(filas)


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


def procesos_kb(solicitudes: list[D.Solicitud]) -> InlineKeyboardMarkup:
    """Un botón por proceso del usuario, para elegir cuál ver en detalle."""
    return _con_volver(
        [
            [
                InlineKeyboardButton(
                    f"#{s.id} · {S.nombres_csv(s.servicio)} — {s.estado_label}",
                    callback_data=f"{CB_PROCESO_PREFIX}{s.id}",
                )
            ]
            for s in solicitudes
        ]
    )


def proceso_kb(solicitud_id: int) -> InlineKeyboardMarkup:
    """Detalle de un proceso: subir documentos o el soporte de pago."""
    return _con_volver(
        [
            [InlineKeyboardButton("📄 Subir documentos", callback_data=f"{CB_DOC_PREFIX}{solicitud_id}")],
            [InlineKeyboardButton("💳 Subir soporte de pago", callback_data=f"{CB_PAGO_PREFIX}{solicitud_id}")],
        ]
    )


def consultar_kb() -> InlineKeyboardMarkup:
    return _con_volver([])


def desuscribir_kb(codigo_exp: str) -> str:
    """Callback para cancelar la suscripción a un expediente (uso interno)."""
    return f"{CB_DESUSCRIBIR_PREFIX}{codigo_exp}"


def cancelar_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancelar", callback_data=CB_CANCELAR)]]
    )


# --- Panel admin: keyboards -------------------------------------------------

def admin_procesos_kb(solicitudes: list[D.Solicitud]) -> InlineKeyboardMarkup:
    filas = [
        [InlineKeyboardButton("➕ Invitar cliente", callback_data=CB_ADMIN_INVITAR)],
        [InlineKeyboardButton("📨 Mis invitaciones", callback_data=CB_ADMIN_INVITACIONES)],
    ]
    filas += [
        [
            InlineKeyboardButton(
                f"#{s.id} · {S.nombres_csv(s.servicio)} — {s.estado_label}",
                callback_data=f"{CB_ADMIN_VER_PREFIX}{s.id}",
            )
        ]
        for s in solicitudes
    ]
    filas.append([InlineKeyboardButton("🔚 Salir del panel admin", callback_data=CB_ADMIN_SALIR)])
    return InlineKeyboardMarkup(filas)


def admin_volver_kb() -> InlineKeyboardMarkup:
    """Teclado simple para pantallas admin sin acciones propias (esperar un
    dato por chat, ver el listado de invitaciones)."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Volver al listado", callback_data=CB_ADMIN_MENU)],
            [InlineKeyboardButton("🔚 Salir del panel admin", callback_data=CB_ADMIN_SALIR)],
        ]
    )


def admin_proceso_kb(solicitud: D.Solicitud) -> InlineKeyboardMarkup:
    filas: list[list[InlineKeyboardButton]] = []
    if solicitud.pago_estado != D.PAGO_CONFIRMADO:
        filas.append(
            [InlineKeyboardButton("✅ Confirmar pago", callback_data=f"{CB_ADMIN_PAGO_OK_PREFIX}{solicitud.id}")]
        )
    fila_estado = []
    if solicitud.estado != D.ESTADOS_ORDEN[0]:
        fila_estado.append(
            InlineKeyboardButton("⏮️ Retroceder estado", callback_data=f"{CB_ADMIN_RETROCEDER_PREFIX}{solicitud.id}")
        )
    if solicitud.estado != D.ESTADOS_ORDEN[-1]:
        fila_estado.append(
            InlineKeyboardButton("⏭️ Avanzar estado", callback_data=f"{CB_ADMIN_AVANZAR_PREFIX}{solicitud.id}")
        )
    if fila_estado:
        filas.append(fila_estado)
    filas.append(
        [InlineKeyboardButton("📥 Reenviar documentos", callback_data=f"{CB_ADMIN_DOCS_PREFIX}{solicitud.id}")]
    )
    filas.append([InlineKeyboardButton("⬅️ Volver al listado", callback_data=CB_ADMIN_MENU)])
    filas.append([InlineKeyboardButton("🔚 Salir del panel admin", callback_data=CB_ADMIN_SALIR)])
    return InlineKeyboardMarkup(filas)


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


def texto_invitaciones(invitaciones: list[D.Invitacion]) -> str:
    """Listado de invitaciones que un admin ha generado (para su propio seguimiento)."""
    if not invitaciones:
        return "📨 *Mis invitaciones*\n\nTodavía no has enviado ninguna."
    lineas = ["📨 *Mis invitaciones*\n"]
    for inv in invitaciones:
        lineas.append(f"• {inv.telefono} — {inv.estado_label}")
    return "\n".join(lineas)


def texto_proceso(solicitud: D.Solicitud, n_docs: int) -> str:
    """Detalle de un proceso concreto (para 'Mis procesos')."""
    return (
        f"📊 *Proceso #{solicitud.id}*\n\n"
        f"• Servicio(s): {S.nombres_csv(solicitud.servicio)}\n"
        f"• Empresa/Persona: {solicitud.empresa}\n"
        f"• Identificación: {solicitud.identificacion}\n"
        f"• Teléfono: {solicitud.telefono}\n"
        f"• Estado: *{solicitud.estado_label}*\n"
        f"• Pago: *{solicitud.pago_estado_label}*\n"
        f"• Documentos subidos: {n_docs}\n"
    )


# Estados del wizard de "Iniciar solicitud" (ConversationHandler).
# El servicio siempre llega preseleccionado desde la ficha (ini_<codigo>).
W_EMPRESA, W_IDENTIFICACION, W_TELEFONO = range(3)
