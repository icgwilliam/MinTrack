"""Bot de Telegram MinTrack con menú (inline keyboard).

Menú principal: Servicios, Precios, Iniciar solicitud, Subir documentos,
Estado de proceso y Consultar título minero.

- La consulta usa ``existing_scripts/monitoreotitulo.py`` contra AnnA y SAR.
- Las solicitudes, documentos y estados se persisten en SQLite (``mintrack.db``).
- "Iniciar solicitud" es un wizard paso a paso (ConversationHandler).
- El estado de proceso avanza automáticamente (reglas de tiempo + subida de docs).

El token del bot se lee de la variable de entorno ``TELEGRAM_BOT_TOKEN``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
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
from .db import Database, ESTADO_COMPLETADO
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

async def _editar_menu(query, texto: str, kb: InlineKeyboardMarkup) -> None:
    """Edita el mensaje del callback mostrando texto + keyboard."""
    try:
        await query.edit_message_text(texto, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        # Si el contenido es idéntico o el mensaje es muy viejo, envía nuevo.
        await query.message.reply_text(texto, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


def _get_db(ctx: ContextTypes.DEFAULT_TYPE) -> Database:
    return ctx.application.bot_data["db"]


def _get_client(ctx: ContextTypes.DEFAULT_TYPE) -> ANMClient:
    return ctx.application.bot_data["anm_client"]


# --- /start y /menu -------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.clear()
    await update.message.reply_text(
        M.TEXTO_BIENVENIDA, reply_markup=M.menu_principal_kb()
    )


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.clear()
    await update.message.reply_text(
        M.TEXTO_MENU, reply_markup=M.menu_principal_kb()
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*MinTrack* — menú de servicios mineros y consulta de títulos.\n\n"
        "/start — menú principal\n/menu — mostrar el menú en cualquier momento\n"
        "Usa los botones para navegar.",
        parse_mode=ParseMode.MARKDOWN,
    )


# --- Router del menú principal (callbacks) --------------------------------

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    ctx.user_data.pop("servicio_visto", None)

    if data == M.CB_MENU or data == M.CB_VOLVER:
        await _editar_menu(query, M.TEXTO_MENU, M.menu_principal_kb())
    elif data == M.CB_SERVICIOS:
        await _editar_menu(query, M.TEXTO_SERVICIOS, M.servicios_kb())
    elif data == M.CB_SERVICIOS_MAS:
        key = ctx.user_data.get("servicio_visto") or next(iter(S.SERVICIOS))
        await _editar_menu(query, M.texto_servicio_detalle(key), M.servicios_detalle_kb(key))
    elif data.startswith(M.CB_SERVICIO_PREFIX):
        key = data[len(M.CB_SERVICIO_PREFIX):]
        if key in S.SERVICIOS:
            ctx.user_data["servicio_visto"] = key
            await _editar_menu(
                query, M.texto_servicio_resumen(key), M.servicios_detalle_kb(key)
            )
    elif data == M.CB_PRECIOS:
        await _editar_menu(query, M.TEXTO_PRECIOS, M.precios_kb())
    elif data == M.CB_PRECIOS_MAS:
        await _editar_menu(query, M.TEXTO_PRECIOS_MAS, M.precios_kb())
    elif data == M.CB_ESTADO:
        await _mostrar_estado(update, ctx)
    elif data == M.CB_CONSULTAR:
        await _iniciar_consulta_titulo(update, ctx)
    elif data == M.CB_SUBIR:
        await _mostrar_subir_documentos(update, ctx)
    elif data == M.CB_CENTINELA:
        await _iniciar_suscripcion(update, ctx)
    elif data == M.CB_MIS_SUBS:
        await _mostrar_suscripciones(update, ctx)
    elif data.startswith(M.CB_DESUSCRIBIR_PREFIX):
        codigo = data[len(M.CB_DESUSCRIBIR_PREFIX):]
        await _desuscribir(update, ctx, codigo)
    # CB_INICIAR y CB_CANCELAR se manejan en el ConversationHandler.


# --- Estado de proceso ----------------------------------------------------

async def _mostrar_estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    sol = db.obtener_solicitud(update.effective_user.id)
    if not sol:
        await _editar_menu(
            query,
            "📊 *Estado de proceso*\n\nNo tienes solicitudes activas. "
            "Usa *🚀 Iniciar solicitud* para crear una.",
            M.estado_kb(),
        )
        return
    # Sincroniza avance automático por tiempo.
    sol = db.sincronizar_estado(update.effective_user.id)
    n_docs = db.contar_documentos(update.effective_user.id)
    texto = (
        "📊 *Estado de proceso*\n\n"
        f"• Solicitud #: {sol.id}\n"
        f"• Servicio(s): {S.nombres_csv(sol.servicio)}\n"
        f"• Empresa: {sol.empresa}\n"
        f"• Contacto: {sol.contacto}\n"
        f"• Estado: *{sol.estado_label}*\n"
        f"• Documentos subidos: {n_docs}\n"
    )
    await _editar_menu(query, texto, M.estado_kb())


# --- Consulta de título minero (desde el menú) -----------------------------

# El usuario entra a "Consultar título minero", se le pide el código por chat.
# Guardamos un flag en user_data para capturar el próximo mensaje de texto.
FLAG_CONSULTA = "esperando_codigo_titulo"
FLAG_SUSCRIPCION = "esperando_codigo_suscripcion"


async def _iniciar_consulta_titulo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ctx.user_data[FLAG_CONSULTA] = True
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Volver al menú", callback_data=M.CB_VOLVER)]]
    )
    await _editar_menu(
        query,
        "⛏️ *Consultar título minero*\n\nEscribe el código de expediente "
        "(formato AAA-#####, ej. ICQ-09083):",
        kb,
    )


async def on_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura mensajes de texto fuera del wizard.

    Si el usuario está esperando para escribir el código de un título, lo
    consulta; si está esperando para suscribirse, lo hace; si no, muestra el menú.
    """
    if ctx.user_data.get(FLAG_CONSULTA):
        ctx.user_data[FLAG_CONSULTA] = False
        codigo = (update.message.text or "").strip().upper()
        if not codigo:
            await update.message.reply_text(
                "Código vacío. Inténtalo de nuevo o usa /menu.",
                reply_markup=M.menu_principal_kb(),
            )
            return
        await _consultar_titulo(update, ctx, codigo)
        return

    if ctx.user_data.get(FLAG_SUSCRIPCION):
        ctx.user_data[FLAG_SUSCRIPCION] = False
        codigo = (update.message.text or "").strip().upper()
        if not codigo:
            await update.message.reply_text(
                "Código vacío. Inténtalo de nuevo o usa /menu.",
                reply_markup=M.menu_principal_kb(),
            )
            return
        await _suscribir(update, ctx, codigo)
        return

    # En cualquier otro caso, reenvía el menú.
    await update.message.reply_text(M.TEXTO_MENU, reply_markup=M.menu_principal_kb())


# --- Centinela (suscripciones a expedientes) -----------------------------

async def _iniciar_suscripcion(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ctx.user_data[FLAG_SUSCRIPCION] = True
    await _editar_menu(query, M.TEXTO_CENTINELA, M.centinela_kb())


async def _suscribir(update: Update, ctx: ContextTypes.DEFAULT_TYPE, codigo: str) -> None:
    client = _get_client(ctx)
    db = _get_db(ctx)
    user_id = update.effective_user.id

    # Verificar que el expediente existe antes de suscribir.
    try:
        titulos = await asyncio.to_thread(
            client.consultar_por_expediente, codigo, return_geometry=False
        )
    except ANMError as exc:
        await update.message.reply_text(f"⚠️ Error consultando la ANM: {exc}")
        return

    if not titulos:
        await update.message.reply_text(
            f"No se encontró el título '{codigo}'. No se pudo suscribir.",
            reply_markup=M.menu_principal_kb(),
        )
        return

    creado = db.suscribir(user_id, codigo)
    # Guarda snapshot inicial si no existe.
    if db.obtener_snapshot(codigo) is None:
        C.actualizar_snapshot(db, titulos[0])

    if creado:
        msg = (
            f"✅ Te suscribiste a *{codigo}*.\n\nNotificaré automáticamente "
            "publicaciones SAR, estado, etapa y vencimientos próximos."
        )
    else:
        msg = f"ℹ️ Ya estabas suscrito a *{codigo}*. Suscripción reactivada."
    await update.message.reply_text(
        msg, parse_mode=ParseMode.MARKDOWN, reply_markup=M.menu_principal_kb()
    )


async def _mostrar_suscripciones(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    subs = db.listar_suscripciones(update.effective_user.id)
    if not subs:
        await _editar_menu(
            query,
            "🔔 *Centinela*\n\nNo tienes suscripciones activas.\n\n"
            "Pulsa *🔔 Centinela* para suscribirte a un expediente.",
            M.centinela_kb(),
        )
        return
    lines = ["🔔 *Tus suscripciones activas:*"]
    rows: list[list[InlineKeyboardButton]] = []
    for s in subs:
        snap = db.obtener_snapshot(s.codigo_exp)
        estado = snap.titulo_est if snap else "—"
        area = f"{snap.area_ha:.2f} ha" if snap and snap.area_ha is not None else "—"
        lines.append(f"• {s.codigo_exp} — {estado} — {area}")
        rows.append(
            [InlineKeyboardButton(f"🔕 Cancelar {s.codigo_exp}",
                                  callback_data=f"{M.CB_DESUSCRIBIR_PREFIX}{s.codigo_exp}")]
        )
    kb = M._con_volver(rows)
    await _editar_menu(query, "\n".join(lines), kb)


async def _desuscribir(update: Update, ctx: ContextTypes.DEFAULT_TYPE, codigo: str) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    ok = db.desuscribir(update.effective_user.id, codigo)
    if ok:
        await query.answer(f"Suscripción a {codigo} cancelada.", show_alert=True)
    else:
        await query.answer(f"No tenías suscripción activa a {codigo}.", show_alert=True)
    await _mostrar_suscripciones(update, ctx)


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
            reply_markup=M.menu_principal_kb(),
        )
        return

    for t in titulos:
        text = _formatar_titulo(t)
        if len(text) > MAX_MSG_LEN:
            text = text[: MAX_MSG_LEN - 20] + "\n…(truncado)"
        await update.message.reply_text(text)
    await update.message.reply_text(
        "¿Algo más? Selecciona una opción:", reply_markup=M.menu_principal_kb()
    )


# --- Subir documentos -----------------------------------------------------

DOC_DIR = os.environ.get(
    "MINTRACK_DOC_DIR",
    str(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or ".") + "/data/docs",
)


async def _mostrar_subir_documentos(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    db = _get_db(ctx)
    sol = db.obtener_solicitud(update.effective_user.id)
    if not sol:
        await _editar_menu(
            query,
            "📄 *Subir documentos*\n\nNo tienes una solicitud activa. "
            "Primero usa *🚀 Iniciar solicitud*.",
            M.servicios_kb(),
        )
        return
    ctx.user_data["esperando_documento"] = True
    await _editar_menu(
        query,
        "📄 *Subir documentos*\n\nEnvía ahora los archivos (PDF, imágenes o "
        "shapefiles) en este chat. Confirmaré cada uno.\n\nCuando termines, "
        "pulsa *📊 Estado de proceso* en el menú.",
        M.estado_kb(),
    )


async def on_documento(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Recibe archivos/imágenes y los registra/guarda."""
    db = _get_db(ctx)
    user_id = update.effective_user.id

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

    if not db.obtener_solicitud(user_id):
        await update.message.reply_text(
            "⚠️ Primero crea una solicitud con 🚀 Iniciar solicitud.",
            reply_markup=M.menu_principal_kb(),
        )
        return

    try:
        tg_file = await file_obj.get_file()
        os.makedirs(DOC_DIR, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file_name or "doc")
        ruta = os.path.join(DOC_DIR, f"{user_id}_{safe_name}")
        await tg_file.download_to_drive(ruta)
    except Exception as exc:  # network / telegram error
        logger.warning("No se pudo descargar el archivo: %s", exc)
        ruta = None

    db.registrar_documento(
        user_id=user_id,
        file_id=file_obj.file_id,
        file_name=file_name,
        tipo=tipo,
        ruta=ruta,
    )
    # Si estaba en EN_REVISION y ya tiene documentos, avanzar a EN_PROCESO.
    db.sincronizar_estado(user_id)
    await update.message.reply_text(
        f"✅ Documento recibido: *{file_name}* ({tipo}).\n"
        f"Total subido: {db.contar_documentos(user_id)}.",
        parse_mode=ParseMode.MARKDOWN,
    )


# --- Wizard: Iniciar solicitud (ConversationHandler) ----------------------

PROMPTS = {
    M.W_EMPRESA: "🚀 *Iniciar solicitud*\n\nPaso 1/4 — Escribe el *nombre de la empresa*:",
    M.W_CONTACTO: "Paso 2/4 — Escribe el *nombre del contacto*:",
    M.W_TELEFONO: "Paso 3/4 — Escribe el *número de teléfono*:",
    M.W_SERVICIO: M.texto_wizard_servicios(),
}

_TEL_RE = re.compile(r"^[+]?[\d\s().-]{6,}$")


async def wizard_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Entrada al wizard desde callback 'Iniciar solicitud' o comando."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            PROMPTS[M.W_EMPRESA], parse_mode=ParseMode.MARKDOWN,
            reply_markup=M.cancelar_kb(),
        )
    else:
        await update.message.reply_text(
            PROMPTS[M.W_EMPRESA], parse_mode=ParseMode.MARKDOWN,
            reply_markup=M.cancelar_kb(),
        )
    return M.W_EMPRESA


async def wizard_empresa(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["w_empresa"] = (update.message.text or "").strip()
    if not ctx.user_data["w_empresa"]:
        await update.message.reply_text("El nombre de la empresa no puede estar vacío.")
        return M.W_EMPRESA
    await update.message.reply_text(
        PROMPTS[M.W_CONTACTO], parse_mode=ParseMode.MARKDOWN, reply_markup=M.cancelar_kb()
    )
    return M.W_CONTACTO


async def wizard_contacto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["w_contacto"] = (update.message.text or "").strip()
    if not ctx.user_data["w_contacto"]:
        await update.message.reply_text("El nombre del contacto no puede estar vacío.")
        return M.W_CONTACTO
    await update.message.reply_text(
        PROMPTS[M.W_TELEFONO], parse_mode=ParseMode.MARKDOWN, reply_markup=M.cancelar_kb()
    )
    return M.W_TELEFONO


async def wizard_telefono(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    tel = (update.message.text or "").strip()
    if not _TEL_RE.match(tel):
        await update.message.reply_text(
            "Teléfono inválido. Escribe un número con dígitos (puede incluir +, espacios, guiones)."
        )
        return M.W_TELEFONO
    ctx.user_data["w_telefono"] = tel
    await update.message.reply_text(
        PROMPTS[M.W_SERVICIO], parse_mode=ParseMode.MARKDOWN, reply_markup=M.cancelar_kb()
    )
    return M.W_SERVICIO


async def wizard_servicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    resp = (update.message.text or "").strip()
    try:
        seleccion = S.parsear_seleccion(resp)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return M.W_SERVICIO
    servicio_csv = ",".join(seleccion)
    ctx.user_data["w_servicio"] = servicio_csv

    db = _get_db(ctx)
    user_id = update.effective_user.id
    db.crear_solicitud(
        user_id=user_id,
        empresa=ctx.user_data["w_empresa"],
        contacto=ctx.user_data["w_contacto"],
        telefono=ctx.user_data["w_telefono"],
        servicio=servicio_csv,
    )
    nombres = S.nombres(seleccion)
    await update.message.reply_text(
        "✅ *Solicitud creada*\n\n"
        f"• Empresa: {ctx.user_data['w_empresa']}\n"
        f"• Contacto: {ctx.user_data['w_contacto']}\n"
        f"• Teléfono: {ctx.user_data['w_telefono']}\n"
        f"• Servicio(s): {nombres}\n\n"
        "Estado inicial: *En revisión*.\n\n"
        "Ahora puedes *📄 Subir documentos* para avanzar tu proceso.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=M.menu_principal_kb(),
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def wizard_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    ctx.user_data.clear()
    await (update.callback_query or update.message).reply_text(
        "❌ Solicitud cancelada.", reply_markup=M.menu_principal_kb()
    )
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
    """Avanza estados de solicitudes internas por tiempo y notifica al usuario."""
    app = ctx.application
    db: Database = app.bot_data["db"]

    for user_id in db.listar_user_ids_con_solicitud():
        sol = db.obtener_solicitud(user_id)
        if not sol or sol.estado == ESTADO_COMPLETADO:
            continue
        estado_antes = sol.estado
        sol_actualizada = db.sincronizar_estado(user_id)
        if not sol_actualizada or sol_actualizada.estado == estado_antes:
            continue
        try:
            await app.bot.send_message(
                chat_id=user_id,
                text=(
                    f"📊 Actualización de tu solicitud #{sol_actualizada.id} "
                    f"({S.nombres_csv(sol_actualizada.servicio)}):\n"
                    f"Estado: *{sol_actualizada.estado_label}*.\n"
                    f"Vigente desde: {Database.fmt_fecha(sol_actualizada.estado_desde)}."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            logger.warning("No se pudo notificar avance a %s: %s", user_id, exc)


# --- Construcción de la aplicación ---------------------------------------

def build_application(
    token: str,
    client: Optional[ANMClient] = None,
    db: Optional[Database] = None,
) -> Application:
    app = ApplicationBuilder().token(token).build()
    app.bot_data["anm_client"] = client or ANMClient()
    app.bot_data["db"] = db or Database()

    # Comandos.
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))

    # Wizard de "Iniciar solicitud".
    wizard = ConversationHandler(
        entry_points=[CallbackQueryHandler(wizard_start, pattern=f"^{M.CB_INICIAR}$")],
        states={
            M.W_EMPRESA: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_empresa)],
            M.W_CONTACTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_contacto)],
            M.W_TELEFONO: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_telefono)],
            M.W_SERVICIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, wizard_servicio)],
        },
        fallbacks=[
            CallbackQueryHandler(wizard_cancelar, pattern=f"^({M.CB_CANCELAR}|{M.CB_VOLVER}|{M.CB_MENU})$")
        ],
        allow_reentry=True,
    )
    app.add_handler(wizard)

    # Callbacks del menú (excepto CB_INICIAR que entra al wizard y CB_CANCELAR).
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

    # Texto libre (captura código de título o reenvía menú). Debe ir al final.
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
