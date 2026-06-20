"""Bot de Telegram para consultar títulos mineros de Colombia (ANM).

Comandos:
    /start        Saludo y resumen de uso.
    /help         Ayuda detallada.
    /exp <código> Consulta exacta por código de expediente (p. ej. TGU-14471).
    /buscar <txt> Búsqueda parcial por código de expediente.

El token del bot se lee de la variable de entorno ``TELEGRAM_BOT_TOKEN``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from .client import ANMClient, ANMError
from .models import TituloMinero

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("mintrack.bot")

MAX_MSG_LEN = 4000  # límite seguro bajo los 4096 chars de Telegram

LABELS = [
    ("codigo_exp", "Código expediente"),
    ("estado_exp", "Estado"),
    ("modalidade", "Modalidad"),
    ("etapa", "Etapa"),
    ("minerales", "Minerales"),
    ("municipios", "Municipios"),
    ("departamento", "Departamento"),
    ("solicitante", "Solicitante / Titular"),
    ("grupo_trab", "Grupo de trabajo"),
    ("area_ha", "Área (ha)"),
    ("fecha_insc", "Fecha inscripción"),
    ("fecha_term", "Fecha terminación"),
    ("tipo_explo", "Tipo de explotación"),
    ("capaminera", "Capa minera"),
    ("producto", "Producto"),
]


def _formatar_titulo(t: TituloMinero) -> str:
    data = t.to_dict()
    lines = [f"*{t.codigo_exp}*"]
    for key, label in LABELS:
        value = data.get(key)
        if value is None or value == "":
            continue
        lines.append(f"• {label}: {value}")
    if t.geometry:
        lines.append("• Geometría: incluida (polígono MAGNA-SIRGAS, SR 4686)")
    return "\n".join(lines)


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hola 👋 Soy MinTrack, un bot para consultar *títulos mineros vigentes de "
        "Colombia* a partir del código del expediente.\n\n"
        "Comandos:\n"
        "/exp <código> — consulta exacta (p. ej. /exp TGU-14471)\n"
        "/buscar <texto> — búsqueda parcial por código\n"
        "/help — ayuda detallada",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*MinTrack* — consulta de títulos mineros de la ANM\n\n"
        "*/exp <código>*: devuelve los datos del título cuyo código de expediente "
        "coincide exactamente. Formato del código: AAA-##### (ej. TGU-14471, "
        "TGV-08021, RIL-12181).\n\n"
        "*/buscar <texto>*: lista títulos cuyo código contiene el texto (útil si no "
        "recuerdas el código exacto).\n\n"
        "_Fuente:_ geoservicio público de títulos vigentes de la ANM "
        "(gisanm.anm.gov.co). Solo incluye títulos _vigentes_; los expedientes en "
        "trámite o archivados no aparecen.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _reply_error(update: Update, msg: str) -> None:
    await update.message.reply_text(f"⚠️ {msg}")


async def cmd_exp(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await _reply_error(update, "Uso: /exp <código> (p. ej. /exp TGU-14471)")
        return
    codigo = " ".join(ctx.args).strip()
    client: ANMClient = ctx.application.bot_data["anm_client"]
    try:
        titulos = await asyncio.to_thread(
            client.consultar_por_expediente, codigo, return_geometry=True
        )
    except ANMError as exc:
        await _reply_error(update, f"Error consultando la ANM: {exc}")
        return
    except ValueError as exc:
        await _reply_error(update, str(exc))
        return

    if not titulos:
        await _reply_error(update, f"No se encontró ningún título con el código '{codigo}'.")
        return

    for t in titulos:
        text = _formatar_titulo(t)
        if len(text) > MAX_MSG_LEN:
            text = text[: MAX_MSG_LEN - 20] + "\n…(truncado)"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_buscar(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await _reply_error(update, "Uso: /buscar <texto> (p. ej. /buscar TGU)")
        return
    texto = " ".join(ctx.args).strip()
    client: ANMClient = ctx.application.bot_data["anm_client"]
    try:
        titulos = await asyncio.to_thread(
            client.buscar_por_codigo, texto, return_geometry=False, limit=30
        )
    except ANMError as exc:
        await _reply_error(update, f"Error consultando la ANM: {exc}")
        return
    except ValueError as exc:
        await _reply_error(update, str(exc))
        return

    if not titulos:
        await _reply_error(update, f"No se encontraron títulos que coincidan con '{texto}'.")
        return

    lines = [f"Se encontraron {len(titulos)} título(s) para '{texto}':"]
    for t in titulos:
        lines.append(
            f"• {t.codigo_exp} — {t.departamento} / {t.municipios} — {t.etapa}"
        )
    text = "\n".join(lines)
    if len(text) > MAX_MSG_LEN:
        text = text[: MAX_MSG_LEN - 20] + "\n…(truncado)"
    await update.message.reply_text(text)


def build_application(token: str, client: Optional[ANMClient] = None) -> Application:
    app = (
        ApplicationBuilder()
        .token(token)
        .build()
    )
    app.bot_data["anm_client"] = client or ANMClient()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("exp", cmd_exp))
    app.add_handler(CommandHandler("buscar", cmd_buscar))
    return app


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Falta la variable de entorno TELEGRAM_BOT_TOKEN. "
            "Crea un bot con @BotFather y exporta su token."
        )
    app = build_application(token)
    logger.info("Iniciando MinTrack bot (long polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
