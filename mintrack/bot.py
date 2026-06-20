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

# (clave_en_modelo, etiqueta legible). Las fechas se formatean legiblemente.
LABELS = [
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

DATE_KEYS = {"fecha_de_s", "fecha_de_e", "fecha_de_a", "fecha_de01"}


def _fmt_fecha(value):
    """Convierte un datetime/ISO a 'YYYY-MM-DD' para mostrar en Telegram."""
    if not value:
        return value
    try:
        from datetime import datetime
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        # ISO string desde to_dict()
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return value


def _formatar_titulo(t: TituloMinero) -> str:
    data = t.to_dict()
    head = t.codigo_exp or t.tenure_id or "(sin código)"
    lines = [f"=== {head} ==="]
    for key, label in LABELS:
        value = data.get(key)
        if value is None or value == "":
            continue
        if key in DATE_KEYS:
            value = _fmt_fecha(value)
            if not value:
                continue
        lines.append(f"• {label}: {value}")
    if t.geometry:
        lines.append("• Geometría: incluida (polígono; usa /exp ... --format geojson para coords)")
    return "\n".join(lines)


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hola 👋 Soy MinTrack, un bot para consultar *títulos mineros de "
        "Colombia* a partir del código del expediente (fuente ANNA Minería / ANM).\n\n"
        "Comandos:\n"
        "/exp <código> — consulta exacta (p. ej. /exp ICQ-09083)\n"
        "/buscar <texto> — búsqueda parcial por código\n"
        "/help — ayuda detallada",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*MinTrack* — consulta de títulos mineros de la ANM (Colombia)\n\n"
        "*/exp <código>*: devuelve los datos del título cuyo código de expediente "
        "coincide exactamente. Formato del código: AAA-##### (ej. ICQ-09083, "
        "TGU-14471, RIL-12181).\n\n"
        "*/buscar <texto>*: lista títulos cuyo código contiene el texto (útil si no "
        "recuerdas el código exacto).\n\n"
        "Datos mostrados: estado, etapa, modalidad, clasificación, minerales, "
        "departamento, municipios, área, centroide, titulares, fechas de "
        "solicitud/expedición/aniversario/expiración y más.\n\n"
        "_Fuente:_ geoservicio público de la ANM (gisanm.anm.gov.co), misma capa que "
        "alimenta el visor ANNA Minería. Incluye títulos vigentes y otros estados.",
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
        await update.message.reply_text(text)


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
            f"• {t.codigo_exp or t.tenure_id} — {t.departamen} / {t.municipios} — "
            f"{t.titulo_est} | {t.etapa}"
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
