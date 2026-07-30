"""Bot de Telegram MinTrack con menú (inline keyboard).

Menú principal: Servicios, Mis procesos y Consultar título minero.

- La consulta usa ``existing_scripts/monitoreotitulo.py`` contra AnnA y SAR.
- Las solicitudes ("procesos"), documentos y estados se persisten en SQLite.
  Un usuario puede tener varios procesos activos a la vez (uno por servicio
  contratado); cada acción (subir documentos, subir soporte de pago) se hace
  sobre un proceso concreto desde "📊 Mis procesos".
- "Iniciar solicitud" es un wizard de 3 pasos (empresa/persona, identificación,
  teléfono) que siempre parte de un servicio ya elegido en su ficha.
- Cada proceso queda "En revisión" hasta que un administrador confirma el pago
  (ver panel admin, comando ``/admin`` con PIN). Los estados siguientes sí
  avanzan automáticamente por tiempo.
- ``/sandbox`` (solo admin) activa un modo de pruebas que usa una base de
  datos separada y no envía notificaciones reales.

El token del bot se lee de la variable de entorno ``TELEGRAM_BOT_TOKEN``.
El PIN del panel admin se lee de ``MINTRACK_ADMIN_PIN``.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import secrets
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .client import ANMClient, ANMError
from .db import Database
from .models import TituloMinero
from . import centinela as C
from . import menu as M
from . import servicios as S

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mintrack.bot")

MAX_MSG_LEN = 4000
MAX_FIELD_LEN = 700

# --- Campos del título minero (igual que antes) ---------------------------

TITULO_LABELS = [
    ("codigo_exp", "Código expediente"),
    ("titulo_est", "Estado"),
    ("etapa", "Etapa"),
    ("modalidad", "Modalidad"),
    ("clasificac", "Clasificación de minería"),
    ("minerales", "Minerales"),
    ("minerales_", "Minerales inactivos"),
    ("departamen", "Departamento"),
    ("municipios", "Municipios"),
    ("area_ha", "Área (ha)"),
    ("centroid_c", "Centroide (lon, lat)"),
    ("solicitant", "Solicitantes / Titulares"),
    ("par", "PAR / Grupo de trabajo"),
    ("fecha_de_s", "Fecha de solicitud"),
    ("fecha_de_e", "Fecha de expedición"),
    ("fecha_de_a", "Fecha de aniversario"),
    ("fecha_de01", "Fecha de expiración"),
    ("publicado_", "Publicado en RUCOM"),
    ("tipo_termi", "Tipo de terminación"),
]
TITULO_DATE_KEYS = {"fecha_de_s", "fecha_de_e", "fecha_de_a", "fecha_de01"}


def _fmt_fecha(value):
    if not value:
        return value
    from datetime import datetime
    try:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return value


def _formatar_titulo(t: TituloMinero) -> str:
    data = t.to_dict()
    head = t.codigo_exp or t.tenure_id or "(sin código)"
    lines = [f"=== {head} ==="]
    analysis = t.extras.get("release_analysis") or {}
    if analysis:
        lines.append("\n=== Liberación de área (SAR) ===")
        lines.append(f"• Estado: {analysis.get('state', 'Sin dato')}")
        lines.append(f"• Interpretación: {analysis.get('message', 'Sin dato')}")
        if analysis.get("releaseAtColombia"):
            lines.append(f"• Fecha oficial: {analysis['releaseAtColombia']}")
    for key, label in TITULO_LABELS:
        value = data.get(key)
        if value is None or value == "":
            continue
        if key in TITULO_DATE_KEYS:
            value = _fmt_fecha(value)
            if not value:
                continue
        value = str(value)
        if len(value) > MAX_FIELD_LEN:
            value = value[: MAX_FIELD_LEN - 14] + "... (resumido)"
        lines.append(f"• {label}: {value}")
    if t.geometry:
        lines.append("• Geometría: incluida (polígono)")
    return "\n".join(lines)


# --- Helpers de respuesta --------------------------------------------------

def _con_banner(ctx: ContextTypes.DEFAULT_TYPE, texto: str) -> str:
    """Antepone un aviso cuando la sesión está en modo prueba (/sandbox)."""
    if ctx.user_data.get("sandbox"):
        return "🧪 *MODO PRUEBA* (nada de esto genera registros reales)\n\n" + texto
    return texto


async def _editar_menu(query, ctx: ContextTypes.DEFAULT_TYPE, texto: str, kb: InlineKeyboardMarkup) -> None:
    """Edita el mensaje del callback mostrando texto + keyboard."""
    texto = _con_banner(ctx, texto)
    try:
        await query.edit_message_text(texto, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        # Si el contenido es idéntico o el mensaje es muy viejo, envía nuevo.
        await query.message.reply_text(texto, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


def _menu_kb(ctx: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    """Menú principal, mostrando 'Consultar título minero' solo al admin."""
    return M.menu_principal_kb(es_admin=bool(ctx.user_data.get("is_admin")))


def _get_db(ctx: ContextTypes.DEFAULT_TYPE) -> Database:
    if ctx.user_data.get("sandbox"):
        return ctx.application.bot_data["db_sandbox"]
    return ctx.application.bot_data["db"]


def _get_client(ctx: ContextTypes.DEFAULT_TYPE) -> ANMClient:
    return ctx.application.bot_data["anm_client"]


def _parse_id(data: str, prefix: str) -> Optional[int]:
    try:
        return int(data[len(prefix):])
    except ValueError:
        return None


# --- /start y /menu -------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.clear()
    args = ctx.args or []
    if args and args[0].startswith("inv_"):
        await _procesar_invitacion(update, ctx, args[0])
    await update.message.reply_text(
        M.TEXTO_BIENVENIDA, reply_markup=_menu_kb(ctx)
    )


async def _procesar_invitacion(update: Update, ctx: ContextTypes.DEFAULT_TYPE, token: str) -> None:
    """Marca como aceptado el link de invitación con el que llegó /start, y
    avisa al admin que lo generó."""
    db = _get_db(ctx)
    inv = db.aceptar_invitacion(token, update.effective_user.id)
    if not inv or inv.estado != "ACEPTADA" or inv.aceptado_por != update.effective_user.id:
        return  # token inexistente o ya usado por otra persona
    try:
        await ctx.application.bot.send_message(
            chat_id=inv.creado_por,
            text=f"✅ Tu invitación a {inv.telefono} fue aceptada. Ya puede usar el bot.",
        )
    except Exception as exc:
        logger.warning("No se pudo notificar la invitación aceptada a %s: %s", inv.creado_por, exc)


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.clear()
    await update.message.reply_text(
        M.TEXTO_MENU, reply_markup=_menu_kb(ctx)
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*MinTrack* — menú de servicios mineros y consulta de títulos.\n\n"
        "/start — menú principal\n/menu — mostrar el menú en cualquier momento\n"
        "/admin — panel de administrador (pide PIN)\n"
        "/sandbox — modo de pruebas, solo tras autenticarte en /admin\n"
        "Usa los botones para navegar.",
        parse_mode=ParseMode.MARKDOWN,
    )


# --- /admin y /sandbox ------------------------------------------------------

FLAG_ADMIN_PIN = "esperando_admin_pin"
FLAG_ADMIN_INVITAR_TEL = "esperando_telefono_invitacion"


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.user_data.get("is_admin"):
        await _enviar_admin_menu(update, ctx)
        return
    pin = os.environ.get("MINTRACK_ADMIN_PIN")
    if not pin:
        await update.message.reply_text(
            "⚠️ El panel admin no está configurado. Define la variable de "
            "entorno MINTRACK_ADMIN_PIN."
        )
        return
    ctx.user_data[FLAG_ADMIN_PIN] = True
    await update.message.reply_text("🔒 Escribe el PIN de administrador:")


async def cmd_sandbox(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.user_data.get("is_admin"):
        await update.message.reply_text(
            "🔒 Necesitas autenticarte como admin primero. Usa /admin."
        )
        return
    if ctx.user_data.get("sandbox"):
        ctx.user_data["sandbox"] = False
        await update.message.reply_text(
            "✅ Volviste al modo real.", reply_markup=_menu_kb(ctx)
        )
    else:
        ctx.user_data["sandbox"] = True
        ctx.user_data.pop("doc_solicitud_id", None)
        ctx.user_data.pop("pago_solicitud_id", None)
        await update.message.reply_text(
            "🧪 *Modo prueba activado.*\n\nTodo lo que hagas ahora (solicitudes, "
            "documentos, pagos) se guarda en una base de datos separada y no "
            "genera notificaciones reales a nadie. Envía /sandbox de nuevo para "
            "volver al modo real.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=_menu_kb(ctx),
        )


async def _enviar_admin_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    db = _get_db(ctx)
    solicitudes = db.listar_todas_solicitudes()
    if solicitudes:
        texto = f"🔑 *Panel admin*\n\n{len(solicitudes)} proceso(s) registrados. Selecciona uno:"
    else:
        texto = "🔑 *Panel admin*\n\nTodavía no hay procesos registrados."
    kb = M.admin_procesos_kb(solicitudes)
    if update.callback_query:
        await _editar_menu(update.callback_query, ctx, texto, kb)
    else:
        await update.message.reply_text(
            _con_banner(ctx, texto), parse_mode=ParseMode.MARKDOWN, reply_markup=kb
        )


async def _admin_ver_proceso(update: Update, ctx: ContextTypes.DEFAULT_TYPE, solicitud_id: int) -> None:
    query = update.callback_query
    if not ctx.user_data.get("is_admin"):
        await query.answer("No autorizado.", show_alert=True)
        return
    db = _get_db(ctx)
    sol = db.obtener_solicitud_por_id(solicitud_id)
    if not sol:
        await _editar_menu(query, ctx, "⚠️ Proceso no encontrado.", M.admin_procesos_kb(db.listar_todas_solicitudes()))
        return
    n_docs = db.contar_documentos(sol.id)
    await _editar_menu(query, ctx, M.texto_proceso(sol, n_docs), M.admin_proceso_kb(sol))


async def _admin_confirmar_pago(update: Update, ctx: ContextTypes.DEFAULT_TYPE, solicitud_id: int) -> None:
    query = update.callback_query
    if not ctx.user_data.get("is_admin"):
        await query.answer("No autorizado.", show_alert=True)
        return
    db = _get_db(ctx)
    sol = db.confirmar_pago(solicitud_id)
    if not sol:
        await _editar_menu(query, ctx, "⚠️ Proceso no encontrado.", M.admin_procesos_kb(db.listar_todas_solicitudes()))
        return
    n_docs = db.contar_documentos(sol.id)
    await _editar_menu(query, ctx, "✅ Pago confirmado.\n\n" + M.texto_proceso(sol, n_docs), M.admin_proceso_kb(sol))
    try:
        await ctx.application.bot.send_message(
            chat_id=sol.user_id,
            text=(
                f"✅ Confirmamos el pago de tu proceso #{sol.id}. "
                f"Estado actual: *{sol.estado_label}*."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        logger.warning("No se pudo notificar la confirmación de pago a %s: %s", sol.user_id, exc)


async def _admin_cambiar_estado(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, solicitud_id: int, avanzar: bool
) -> None:
    query = update.callback_query
    if not ctx.user_data.get("is_admin"):
        await query.answer("No autorizado.", show_alert=True)
        return
    db = _get_db(ctx)
    sol = db.avanzar_estado(solicitud_id) if avanzar else db.retroceder_estado(solicitud_id)
    if not sol:
        await _editar_menu(query, ctx, "⚠️ Proceso no encontrado.", M.admin_procesos_kb(db.listar_todas_solicitudes()))
        return
    n_docs = db.contar_documentos(sol.id)
    await _editar_menu(query, ctx, M.texto_proceso(sol, n_docs), M.admin_proceso_kb(sol))
    try:
        await ctx.application.bot.send_message(
            chat_id=sol.user_id,
            text=(
                f"📊 Actualización de tu proceso #{sol.id} "
                f"({S.nombres_csv(sol.servicio)}):\nEstado: *{sol.estado_label}*."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        logger.warning("No se pudo notificar el cambio de estado a %s: %s", sol.user_id, exc)


async def _admin_reenviar_documentos(update: Update, ctx: ContextTypes.DEFAULT_TYPE, solicitud_id: int) -> None:
    query = update.callback_query
    if not ctx.user_data.get("is_admin"):
        await query.answer("No autorizado.", show_alert=True)
        return
    db = _get_db(ctx)
    docs = db.listar_documentos(solicitud_id)
    await query.answer(f"Reenviando {len(docs)} documento(s)…")
    admin_chat_id = update.effective_user.id
    if not docs:
        await ctx.application.bot.send_message(chat_id=admin_chat_id, text="No hay documentos para este proceso.")
        return
    for d in docs:
        try:
            if d["tipo"] == "imagen":
                await ctx.application.bot.send_photo(
                    chat_id=admin_chat_id, photo=d["file_id"], caption=d["file_name"] or ""
                )
            else:
                await ctx.application.bot.send_document(
                    chat_id=admin_chat_id, document=d["file_id"], caption=d["file_name"] or ""
                )
        except Exception as exc:
            logger.warning("No se pudo reenviar el documento %s: %s", d["file_name"], exc)


async def _admin_iniciar_invitacion(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Pide el teléfono de la persona a invitar (el link se genera al recibirlo)."""
    query = update.callback_query
    if not ctx.user_data.get("is_admin"):
        await query.answer("No autorizado.", show_alert=True)
        return
    ctx.user_data[FLAG_ADMIN_INVITAR_TEL] = True
    await _editar_menu(
        query, ctx,
        "➕ *Invitar cliente*\n\nEscribe el número de celular (Colombia) de la "
        "persona que quieres invitar. Te daré un link para que se lo envíes "
        "por WhatsApp o SMS (el bot no puede escribirle directo si nunca lo "
        "ha contactado).",
        M.admin_volver_kb(),
    )


async def _admin_ver_invitaciones(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not ctx.user_data.get("is_admin"):
        await query.answer("No autorizado.", show_alert=True)
        return
    db = _get_db(ctx)
    invitaciones = db.listar_invitaciones(update.effective_user.id)
    await _editar_menu(query, ctx, M.texto_invitaciones(invitaciones), M.admin_volver_kb())


async def _admin_salir(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data["is_admin"] = False
    ctx.user_data["sandbox"] = False
    ctx.user_data.pop(FLAG_ADMIN_INVITAR_TEL, None)
    query = update.callback_query
    await _editar_menu(query, ctx, "🔚 Saliste del panel admin.\n\n" + M.TEXTO_MENU, _menu_kb(ctx))


# --- Router del menú principal (callbacks) --------------------------------

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    ctx.user_data.pop("servicio_visto", None)
    ctx.user_data.pop("doc_solicitud_id", None)
    ctx.user_data.pop("pago_solicitud_id", None)
    ctx.user_data.pop(FLAG_ADMIN_INVITAR_TEL, None)

    if data == M.CB_MENU or data == M.CB_VOLVER:
        await _editar_menu(query, ctx, M.TEXTO_MENU, _menu_kb(ctx))
    elif data == M.CB_SERVICIOS:
        await _editar_menu(query, ctx, M.TEXTO_SERVICIOS, M.servicios_kb())
    elif data.startswith(M.CB_PRECIO_PREFIX):
        key = data[len(M.CB_PRECIO_PREFIX):]
        if key in S.SERVICIOS:
            ctx.user_data["servicio_visto"] = key
            await _editar_menu(query, ctx, M.texto_precio_servicio(key), M.precio_kb(key))
    elif data.startswith(M.CB_SERVICIO_PREFIX):
        key = data[len(M.CB_SERVICIO_PREFIX):]
        if key in S.SERVICIOS:
            ctx.user_data["servicio_visto"] = key
            await _editar_menu(query, ctx, M.texto_servicio(key), M.servicio_kb(key))
    elif data == M.CB_ESTADO:
        await _mostrar_procesos(update, ctx)
    elif data.startswith(M.CB_PROCESO_PREFIX):
        sid = _parse_id(data, M.CB_PROCESO_PREFIX)
        if sid is not None:
            await _mostrar_proceso(update, ctx, sid)
    elif data.startswith(M.CB_DOC_PREFIX):
        sid = _parse_id(data, M.CB_DOC_PREFIX)
        if sid is not None:
            await _iniciar_subir_documentos(update, ctx, sid)
    elif data.startswith(M.CB_PAGO_PREFIX):
        sid = _parse_id(data, M.CB_PAGO_PREFIX)
        if sid is not None:
            await _iniciar_subir_pago(update, ctx, sid)
    elif data == M.CB_CONSULTAR:
        if not ctx.user_data.get("is_admin"):
            await query.answer("No autorizado.", show_alert=True)
        else:
            await _iniciar_consulta_titulo(update, ctx)
    elif data.startswith(M.CB_ADMIN_VER_PREFIX):
        sid = _parse_id(data, M.CB_ADMIN_VER_PREFIX)
        if sid is not None:
            await _admin_ver_proceso(update, ctx, sid)
    elif data.startswith(M.CB_ADMIN_PAGO_OK_PREFIX):
        sid = _parse_id(data, M.CB_ADMIN_PAGO_OK_PREFIX)
        if sid is not None:
            await _admin_confirmar_pago(update, ctx, sid)
    elif data.startswith(M.CB_ADMIN_AVANZAR_PREFIX):
        sid = _parse_id(data, M.CB_ADMIN_AVANZAR_PREFIX)
        if sid is not None:
            await _admin_cambiar_estado(update, ctx, sid, avanzar=True)
    elif data.startswith(M.CB_ADMIN_RETROCEDER_PREFIX):
        sid = _parse_id(data, M.CB_ADMIN_RETROCEDER_PREFIX)
        if sid is not None:
            await _admin_cambiar_estado(update, ctx, sid, avanzar=False)
    elif data.startswith(M.CB_ADMIN_DOCS_PREFIX):
        sid = _parse_id(data, M.CB_ADMIN_DOCS_PREFIX)
        if sid is not None:
            await _admin_reenviar_documentos(update, ctx, sid)
    elif data == M.CB_ADMIN_INVITAR:
        await _admin_iniciar_invitacion(update, ctx)
    elif data == M.CB_ADMIN_INVITACIONES:
        await _admin_ver_invitaciones(update, ctx)
    elif data == M.CB_ADMIN_MENU:
        await _enviar_admin_menu(update, ctx)
    elif data == M.CB_ADMIN_SALIR:
        await _admin_salir(update, ctx)
    # CB_INICIAR_PREFIX y CB_CANCELAR se manejan en el ConversationHandler.


# --- Mis procesos -----------------------------------------------------------

async def _mostrar_procesos(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    solicitudes = db.listar_solicitudes(update.effective_user.id)
    if not solicitudes:
        await _editar_menu(
            query, ctx,
            "📊 *Mis procesos*\n\nNo tienes procesos activos. Ve a *📌 Servicios* "
            "para contratar uno.",
            M.estado_kb(),
        )
        return
    await _editar_menu(
        query, ctx,
        "📊 *Mis procesos*\n\nSelecciona uno para ver el detalle:",
        M.procesos_kb(solicitudes),
    )


async def _mostrar_proceso(update: Update, ctx: ContextTypes.DEFAULT_TYPE, solicitud_id: int) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    sol = db.obtener_solicitud_por_id(solicitud_id)
    if not sol or sol.user_id != update.effective_user.id:
        await _editar_menu(query, ctx, "⚠️ Proceso no encontrado.", M.estado_kb())
        return
    sol = db.sincronizar_estado(sol.id) or sol
    n_docs = db.contar_documentos(sol.id)
    await _editar_menu(query, ctx, M.texto_proceso(sol, n_docs), M.proceso_kb(sol.id))


async def _iniciar_subir_documentos(update: Update, ctx: ContextTypes.DEFAULT_TYPE, solicitud_id: int) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    sol = db.obtener_solicitud_por_id(solicitud_id)
    if not sol or sol.user_id != update.effective_user.id:
        await _editar_menu(query, ctx, "⚠️ Proceso no encontrado.", M.estado_kb())
        return
    ctx.user_data["doc_solicitud_id"] = solicitud_id
    await _editar_menu(
        query, ctx,
        f"📄 *Subir documentos* — Proceso #{solicitud_id}\n\nEnvía ahora los "
        "archivos (PDF, imágenes o shapefiles) en este chat. Confirmaré cada "
        "uno.\n\nCuando termines, vuelve a *📊 Mis procesos* para ver el resumen.",
        M.proceso_kb(solicitud_id),
    )


async def _iniciar_subir_pago(update: Update, ctx: ContextTypes.DEFAULT_TYPE, solicitud_id: int) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    sol = db.obtener_solicitud_por_id(solicitud_id)
    if not sol or sol.user_id != update.effective_user.id:
        await _editar_menu(query, ctx, "⚠️ Proceso no encontrado.", M.estado_kb())
        return
    ctx.user_data["pago_solicitud_id"] = solicitud_id
    await _editar_menu(
        query, ctx,
        f"💳 *Subir soporte de pago* — Proceso #{solicitud_id}\n\nEnvía el "
        "comprobante de pago (PDF o imagen). Confirmaremos tu pago y el "
        "proceso avanzará de estado.",
        M.proceso_kb(solicitud_id),
    )


# --- Consulta de título minero (desde el menú) -----------------------------

# El usuario entra a "Consultar título minero", se le pide el código por chat.
# Guardamos un flag en user_data para capturar el próximo mensaje de texto.
FLAG_CONSULTA = "esperando_codigo_titulo"


async def _iniciar_consulta_titulo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ctx.user_data[FLAG_CONSULTA] = True
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data=M.CB_VOLVER)]]
    )
    await _editar_menu(
        query, ctx,
        "⛏️ *Consultar título minero*\n\nEscribe el código de expediente "
        "(formato AAA-#####, ej. ICQ-09083):",
        kb,
    )


async def on_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura PIN de admin, código de título o, en cualquier otro caso,
    reenvía el menú principal."""
    if ctx.user_data.get(FLAG_ADMIN_PIN):
        ctx.user_data[FLAG_ADMIN_PIN] = False
        pin_ingresado = (update.message.text or "").strip()
        pin_real = os.environ.get("MINTRACK_ADMIN_PIN") or ""
        if pin_real and hmac.compare_digest(pin_ingresado, pin_real):
            ctx.user_data["is_admin"] = True
            _get_db(ctx).registrar_admin(update.effective_user.id)
            await update.message.reply_text("✅ Acceso admin concedido.")
            await _enviar_admin_menu(update, ctx)
        else:
            await update.message.reply_text(
                "❌ PIN incorrecto.", reply_markup=_menu_kb(ctx)
            )
        return

    if ctx.user_data.get(FLAG_ADMIN_INVITAR_TEL):
        ctx.user_data[FLAG_ADMIN_INVITAR_TEL] = False
        if not ctx.user_data.get("is_admin"):
            await update.message.reply_text(
                "🔒 Sesión admin expirada. Usa /admin.", reply_markup=_menu_kb(ctx)
            )
            return
        tel = _telefono_normalizado(update.message.text or "")
        if not tel:
            await update.message.reply_text(
                "Número inválido. Escribe un celular colombiano de 10 dígitos "
                "que empiece en 3 (puedes incluir +57, espacios o guiones).",
                reply_markup=M.admin_volver_kb(),
            )
            return
        db = _get_db(ctx)
        token = f"inv_{secrets.token_urlsafe(8)}"
        db.crear_invitacion(token=token, telefono=tel, creado_por=update.effective_user.id)
        link = f"https://t.me/{ctx.bot.username}?start={token}"
        # Sin parse_mode a propósito: el token puede traer "_", que en
        # Markdown abre cursiva y, si no cierra, corrompe el link entero
        # (el invitado ve "nombre de usuario no encontrado" al abrirlo).
        await update.message.reply_text(
            f"✅ Invitación creada para {tel}.\n\nCompártele este link por "
            f"WhatsApp o SMS:\n{link}\n\nCuando la persona lo abra y pulse "
            "Iniciar, quedará registrada como aceptada y te avisaré aquí.",
            reply_markup=M.admin_volver_kb(),
        )
        return

    if ctx.user_data.get(FLAG_CONSULTA):
        ctx.user_data[FLAG_CONSULTA] = False
        codigo = (update.message.text or "").strip().upper()
        if not codigo:
            await update.message.reply_text(
                "Código vacío. Inténtalo de nuevo o usa /menu.",
                reply_markup=_menu_kb(ctx),
            )
            return
        await _consultar_titulo(update, ctx, codigo)
        return

    # En cualquier otro caso, reenvía el menú.
    await update.message.reply_text(M.TEXTO_MENU, reply_markup=_menu_kb(ctx))


async def _consultar_titulo(update: Update, ctx: ContextTypes.DEFAULT_TYPE, codigo: str) -> None:
    client = _get_client(ctx)
    try:
        titulos = await asyncio.to_thread(
            client.consultar_por_expediente, codigo, return_geometry=True
        )
    except ANMError as exc:
        await update.message.reply_text(f"⚠️ Error consultando la ANM: {exc}")
        return
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    if not titulos:
        await update.message.reply_text(
            f"No se encontró ningún título con el código '{codigo}'.",
            reply_markup=_menu_kb(ctx),
        )
        return

    for t in titulos:
        text = _formatar_titulo(t)
        if len(text) > MAX_MSG_LEN:
            text = text[: MAX_MSG_LEN - 20] + "\n…(truncado)"
        await update.message.reply_text(text)
    await update.message.reply_text(
        "¿Algo más? Selecciona una opción:", reply_markup=_menu_kb(ctx)
    )


# --- Subir documentos / soporte de pago (por proceso) -----------------------

DOC_DIR = os.environ.get(
    "MINTRACK_DOC_DIR",
    str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".") + "/data/docs",
)


async def on_documento(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Recibe archivos/imágenes y los registra contra el proceso seleccionado
    (vía 📄 Subir documentos o 💳 Subir soporte de pago en 'Mis procesos')."""
    db = _get_db(ctx)
    user_id = update.effective_user.id

    pago = bool(ctx.user_data.get("pago_solicitud_id"))
    solicitud_id = ctx.user_data.get("pago_solicitud_id") or ctx.user_data.get("doc_solicitud_id")
    if not solicitud_id:
        await update.message.reply_text(
            _con_banner(
                ctx,
                "⚠️ Primero selecciona un proceso: ve a *📊 Mis procesos*, elige "
                "uno y pulsa *📄 Subir documentos* o *💳 Subir soporte de pago*.",
            ),
            parse_mode=ParseMode.MARKDOWN, reply_markup=_menu_kb(ctx),
        )
        return

    sol = db.obtener_solicitud_por_id(solicitud_id)
    if not sol or sol.user_id != user_id:
        ctx.user_data.pop("doc_solicitud_id", None)
        ctx.user_data.pop("pago_solicitud_id", None)
        await update.message.reply_text(
            _con_banner(ctx, "⚠️ Ese proceso ya no está disponible."),
            parse_mode=ParseMode.MARKDOWN, reply_markup=_menu_kb(ctx),
        )
        return

    doc = update.message.document
    photo = update.message.photo
    file_obj = None
    file_name = None
    tipo = "otro"

    if doc:
        file_obj = doc
        file_name = doc.file_name or "documento"
        low = file_name.lower()
        if low.endswith(".pdf"):
            tipo = "pdf"
        elif low.endswith((".shp", ".shx", ".dbf", ".prj", ".zip")):
            tipo = "shape"
        elif re.search(r"\.(jpg|jpeg|png|gif|bmp|tif|tiff|webp)$", low):
            tipo = "imagen"
    elif photo:
        # photo es lista de tamaños; tomar el más grande.
        file_obj = photo[-1]
        file_name = f"imagen_{file_obj.file_unique_id}.jpg"
        tipo = "imagen"
    else:
        await update.message.reply_text("⚠️ Envía un archivo (PDF, imagen o shape).")
        return

    if pago:
        tipo = "pago"

    try:
        tg_file = await file_obj.get_file()
        os.makedirs(DOC_DIR, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file_name or "doc")
        ruta = os.path.join(DOC_DIR, f"{sol.id}_{safe_name}")
        await tg_file.download_to_drive(ruta)
    except Exception as exc:  # network / telegram error
        logger.warning("No se pudo descargar el archivo: %s", exc)
        ruta = None

    db.registrar_documento(
        solicitud_id=sol.id,
        user_id=user_id,
        file_id=file_obj.file_id,
        file_name=file_name,
        tipo=tipo,
        ruta=ruta,
    )

    if pago:
        db.marcar_pago_en_revision(sol.id)
        for admin_id in db.admins_conocidos():
            if admin_id == user_id:
                continue
            try:
                await ctx.application.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"💳 Nuevo soporte de pago para el proceso #{sol.id} "
                        f"({S.nombres_csv(sol.servicio)}). Usa /admin para confirmarlo."
                    ),
                )
            except Exception as exc:
                logger.warning("No se pudo notificar el pago al admin %s: %s", admin_id, exc)
        await update.message.reply_text(
            _con_banner(
                ctx,
                f"✅ Comprobante recibido para el proceso #{sol.id}. Quedará "
                "confirmado cuando lo revisemos.",
            ),
            parse_mode=ParseMode.MARKDOWN, reply_markup=M.proceso_kb(sol.id),
        )
    else:
        db.sincronizar_estado(sol.id)
        await update.message.reply_text(
            _con_banner(
                ctx,
                f"✅ Documento recibido: *{file_name}* ({tipo}).\n"
                f"Total subido: {db.contar_documentos(sol.id)}.",
            ),
            parse_mode=ParseMode.MARKDOWN, reply_markup=M.proceso_kb(sol.id),
        )


# --- Wizard: Iniciar solicitud (ConversationHandler) ----------------------
#
# Siempre se entra desde la ficha de un servicio (ini_<codigo>): el servicio
# ya está elegido, así que el wizard solo pide 3 datos (empresa/persona,
# identificación, teléfono). No existe combinación de servicios desde aquí:
# el Paquete Integral cubre ese caso, y cada servicio queda como un proceso
# independiente en "Mis procesos".

_TEL_RE = re.compile(r"^3\d{9}$")


def _telefono_normalizado(texto: str) -> Optional[str]:
    """Valida un celular colombiano (10 dígitos, empieza en 3).

    Acepta espacios, puntos, guiones y el prefijo +57/57. Devuelve el número
    normalizado (solo dígitos) o None si no es válido.
    """
    digits = re.sub(r"[^\d]", "", texto or "")
    if digits.startswith("57") and len(digits) == 12:
        digits = digits[2:]
    return digits if _TEL_RE.match(digits) else None


async def wizard_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Entrada al wizard desde la ficha de un servicio (ini_<codigo>)."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    codigo = data[len(M.CB_INICIAR_PREFIX):] if data.startswith(M.CB_INICIAR_PREFIX) else ""
    if codigo not in S.SERVICIOS:
        await query.edit_message_text(
            _con_banner(ctx, "⚠️ Servicio no válido. Vuelve a *📌 Servicios* e inténtalo de nuevo."),
            parse_mode=ParseMode.MARKDOWN, reply_markup=_menu_kb(ctx),
        )
        return ConversationHandler.END

    ctx.user_data["w_servicio"] = codigo
    prompt = (
        "🚀 *Iniciar solicitud*\n\nPaso 1/3 — Escribe el *nombre de la empresa "
        "o persona natural*:"
    )
    await query.edit_message_text(
        _con_banner(ctx, prompt), parse_mode=ParseMode.MARKDOWN, reply_markup=M.cancelar_kb(),
    )
    return M.W_EMPRESA


async def wizard_empresa(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["w_empresa"] = (update.message.text or "").strip()
    if not ctx.user_data["w_empresa"]:
        await update.message.reply_text("El nombre no puede estar vacío. Escríbelo de nuevo:")
        return M.W_EMPRESA
    await update.message.reply_text(
        _con_banner(ctx, "Paso 2/3 — Escribe tu *número de identificación* (cédula o NIT):"),
        parse_mode=ParseMode.MARKDOWN, reply_markup=M.cancelar_kb(),
    )
    return M.W_IDENTIFICACION


async def wizard_identificacion(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["w_identificacion"] = (update.message.text or "").strip()
    if not ctx.user_data["w_identificacion"]:
        await update.message.reply_text("La identificación no puede estar vacía. Escríbela de nuevo:")
        return M.W_IDENTIFICACION
    await update.message.reply_text(
        _con_banner(
            ctx,
            "Paso 3/3 — Escribe tu *número de celular* (Colombia), "
            "ej. 3001234567 o +57 300 123 4567:",
        ),
        parse_mode=ParseMode.MARKDOWN, reply_markup=M.cancelar_kb(),
    )
    return M.W_TELEFONO


async def wizard_telefono(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tel = _telefono_normalizado(update.message.text or "")
    if not tel:
        await update.message.reply_text(
            "Número inválido. Escribe un celular colombiano de 10 dígitos que "
            "empiece en 3 (puedes incluir +57, espacios o guiones)."
        )
        return M.W_TELEFONO
    ctx.user_data["w_telefono"] = tel
    return await _finalizar_solicitud(update, ctx)


async def _finalizar_solicitud(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Crea el proceso y entrega el mensaje de cierre según el servicio."""
    codigo = ctx.user_data.get("w_servicio") or ""
    if codigo not in S.SERVICIOS:
        await update.message.reply_text("No se pudo determinar el servicio. Inténtalo de nuevo.")
        ctx.user_data.pop("w_servicio", None)
        return ConversationHandler.END

    db = _get_db(ctx)
    user_id = update.effective_user.id
    sol = db.crear_solicitud(
        user_id=user_id,
        empresa=ctx.user_data["w_empresa"],
        identificacion=ctx.user_data["w_identificacion"],
        telefono=ctx.user_data["w_telefono"],
        servicio=codigo,
    )
    cierre = _mensaje_cierre([codigo])
    texto = (
        f"✅ *Solicitud creada* (Proceso #{sol.id})\n\n"
        f"• Servicio: {S.nombre(codigo)}\n"
        f"• Empresa/Persona: {sol.empresa}\n"
        f"• Identificación: {sol.identificacion}\n"
        f"• Teléfono: {sol.telefono}\n\n"
        f"Estado inicial: *{sol.estado_label}*.\n"
        "Tu proceso queda en revisión hasta que confirmemos tu pago. Sube el "
        "soporte de pago con el botón de abajo; también puedes volver a este "
        "proceso más tarde desde *📊 Mis procesos*.\n\n"
        f"{cierre}"
    )
    await update.message.reply_text(
        _con_banner(ctx, texto), parse_mode=ParseMode.MARKDOWN, reply_markup=M.proceso_kb(sol.id),
    )
    for clave in ("w_servicio", "w_empresa", "w_identificacion", "w_telefono"):
        ctx.user_data.pop(clave, None)
    return ConversationHandler.END


def _mensaje_cierre(seleccion: list[str]) -> str:
    """Detalle adicional del siguiente paso según el/los servicio(s) contratado(s)."""
    if S.PAQUETE_INTEGRAL in seleccion:
        return (
            "Como contrataste el *Paquete Integral*, cuando el pago quede "
            "confirmado el siguiente paso es *📄 Subir documentos*. Luego te "
            "pediremos el área a monitorear y las credenciales de ANNA Minería "
            "para activar el servicio completo."
        )
    partes = ["Cuando el pago quede confirmado, siguiente paso:"]
    if S.ALISTAMIENTO in seleccion:
        partes.append("• Alistamiento: *📄 Subir documentos* para la revisión formal.")
    if S.MONITOREO in seleccion:
        partes.append("• Monitoreo: te pediremos el *código del área* a vigilar.")
    if S.RADICACION in seleccion:
        partes.append(
            "• Radicación: *📄 Subir documentos* y luego entregar credenciales "
            "de ANNA Minería."
        )
    return "\n".join(partes)


async def wizard_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    for clave in ("w_servicio", "w_empresa", "w_identificacion", "w_telefono"):
        ctx.user_data.pop(clave, None)
    texto = _con_banner(ctx, "❌ Solicitud cancelada.")
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(texto, parse_mode=ParseMode.MARKDOWN, reply_markup=_menu_kb(ctx))
    else:
        await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN, reply_markup=_menu_kb(ctx))
    return ConversationHandler.END


# --- Scheduler: notificaciones proactivas --------------------------------

async def job_revisar_suscripciones(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Revisa todas las suscripciones activas, compara con snapshots y avisa."""
    app = ctx.application
    db: Database = app.bot_data["db"]
    client: ANMClient = app.bot_data["anm_client"]

    subs_por_exp = db.suscripciones_activas_por_exp()
    if not subs_por_exp:
        return

    logger.info("Centinela: revisando %d expediente(s) suscritos.", len(subs_por_exp))

    for codigo, user_ids in subs_por_exp.items():
        try:
            titulos = await asyncio.to_thread(
                client.consultar_por_expediente, codigo, return_geometry=False
            )
        except ANMError as exc:
            logger.warning("Centinela: error consultando %s: %s", codigo, exc)
            continue

        if not titulos:
            continue

        t = titulos[0]
        snap_previo = db.obtener_snapshot(codigo)
        eventos = C.comparar(t, snap_previo)
        C.actualizar_snapshot(db, t)

        if not eventos:
            continue

        for ev in eventos:
            for uid in user_ids:
                try:
                    await app.bot.send_message(chat_id=uid, text=ev.mensaje)
                except Exception as exc:  # user bloqueó el bot, etc.
                    logger.warning("Centinela: no se pudo notificar a %s: %s", uid, exc)


async def job_avanzar_solicitudes(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Avanza estados de procesos internos por tiempo y notifica al usuario."""
    app = ctx.application
    db: Database = app.bot_data["db"]

    for sol in db.listar_solicitudes_activas():
        estado_antes = sol.estado
        sol_actualizada = db.sincronizar_estado(sol.id)
        if not sol_actualizada or sol_actualizada.estado == estado_antes:
            continue
        try:
            await app.bot.send_message(
                chat_id=sol_actualizada.user_id,
                text=(
                    f"📊 Actualización de tu proceso #{sol_actualizada.id} "
                    f"({S.nombres_csv(sol_actualizada.servicio)}):\n"
                    f"Estado: *{sol_actualizada.estado_label}*.\n"
                    f"Vigente desde: {Database.fmt_fecha(sol_actualizada.estado_desde)}."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            logger.warning("No se pudo notificar avance a %s: %s", sol_actualizada.user_id, exc)


# --- Construcción de la aplicación ---------------------------------------

def build_application(
    token: str,
    client: Optional[ANMClient] = None,
    db: Optional[Database] = None,
) -> Application:
    app = ApplicationBuilder().token(token).build()
    db_real = db or Database()
    app.bot_data["anm_client"] = client or ANMClient()
    app.bot_data["db"] = db_real

    sandbox_path = os.environ.get("MINTRACK_SANDBOX_DB_PATH")
    if not sandbox_path:
        root, ext = os.path.splitext(db_real.path)
        sandbox_path = f"{root}_sandbox{ext or '.db'}"
    app.bot_data["db_sandbox"] = Database(sandbox_path)

    # Comandos.
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("sandbox", cmd_sandbox))

    # Wizard de "Iniciar solicitud" (siempre parte de un servicio elegido).
    wizard = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(wizard_start, pattern=f"^{M.CB_INICIAR_PREFIX}"),
        ],
        states={
            M.W_EMPRESA: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_empresa)],
            M.W_IDENTIFICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_identificacion)],
            M.W_TELEFONO: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_telefono)],
        },
        fallbacks=[
            CallbackQueryHandler(wizard_cancelar, pattern=f"^({M.CB_CANCELAR}|{M.CB_VOLVER}|{M.CB_MENU})$")
        ],
        allow_reentry=True,
    )
    app.add_handler(wizard)

    # Callbacks del menú (excepto CB_INICIAR_PREFIX que entra al wizard y CB_CANCELAR).
    app.add_handler(CallbackQueryHandler(on_callback))

    # Documentos: PDF, imágenes, shapefiles y zip.
    shape_ext = (
        filters.Document.FileExtension(".shp")
        | filters.Document.FileExtension(".shx")
        | filters.Document.FileExtension(".dbf")
        | filters.Document.FileExtension(".prj")
    )
    app.add_handler(
        MessageHandler(
            (filters.Document.PDF
             | filters.Document.IMAGE
             | filters.Document.ZIP
             | shape_ext
             | filters.PHOTO),
            on_documento,
        )
    )

    # Texto libre (PIN admin, código de título, o reenvía menú). Debe ir al final.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_texto))

    # --- Scheduler (jobs periódicos del centinela) ------------------------
    # Revisa las suscripciones activas contra la ANM y notifica cambios.
    # La frecuencia se controla con la variable de entorno MINTRACK_CENTINELA_MIN
    # (por defecto 30 min). En GitHub Actions el job puede tardar en arrancar;
    # esto es lo mejor posible dentro de esas restricciones.
    intervalo = max(int(os.environ.get("MINTRACK_CENTINELA_MIN", "30")), 5)
    jq = app.job_queue
    if jq is not None:
        jq.run_repeating(job_revisar_suscripciones, interval=intervalo * 60, first=60)
        jq.run_repeating(job_avanzar_solicitudes, interval=60, first=30)

    return app


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Falta la variable de entorno TELEGRAM_BOT_TOKEN. "
            "Crea un bot con @BotFather y exporta su token."
        )
    app = build_application(token)
    logger.info("Iniciando MinTrack bot con menú (long polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
