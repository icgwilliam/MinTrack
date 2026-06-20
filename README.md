# MinTrack

Sistema para consultar los datos de un **título minero vigente en Colombia** a
partir del **código del expediente**, usando los geoservicios públicos de la
**Agencia Nacional de Minería (ANM)**.

## ¿Cómo funciona?

MinTrack consulta el FeatureServer público de títulos mineros que publica la ANM
sobre ArcGIS Enterprise:

```
https://gisanm.anm.gov.co/server/rest/services/Hosted/Titulos_mineros/FeatureServer/0
```

La capa `titulos_vigentes` expone, por cada título, sus atributos (código del
expediente, estado, modalidad, etapa, minerales, área en hectáreas, fechas,
municipios, departamento, solicitante, grupo de trabajo, etc.) y su geometría
(polígono en MAGNA-SIRGAS, SR 4686). El servicio soporta consultas SQL por el
campo `codigo_exp` **sin necesidad de autenticación**.

> **Nota sobre el alcance**: esta capa contiene los **títulos vigentes**. Los
> expedientes en trámite, archivalizados o archivados podrían no aparecer aquí;
> para esos casos es necesario recurrir al visor SIGM (`annamineria.anm.gov.co`),
> que requiere inicio de sesión.

## Instalación

```bash
pip install -r requirements.txt
pip install -e .
```

## Uso

### Consulta exacta por código de expediente

```bash
mintrack consultar TGU-14471
```

Salida en texto con todos los datos disponibles:

```
=== Título minero: TGU-14471 ===
Código expediente: TGU-14471
Modalidad: CONTRATO DE CONCESIÓN (L 685)
Etapa: Exploración
Minerales: ARENAS
Municipios: SAN ESTANISLAO
Departamento: BOLÍVAR
Grupo de trabajo: PAR CARTAGENA
Área (ha): 54.50316621
Geometría: incluida (polígono en MAGNA-SIRGAS, SR 4686)
```

### Formatos de salida

```bash
mintrack consultar TGU-14471 --format json      # JSON con todos los atributos
mintrack consultar TGU-14471 --format geojson   # GeoJSON Feature (con polígono)
mintrack consultar TGU-14471 --no-geometry      # sin descargar la geometría
```

### Búsqueda parcial por código

Útil cuando no se recuerda el código exacto:

```bash
mintrack buscar TGU --limit 20
```

## Formato del código de expediente

Los códigos usan el patrón `AAA-#####`, donde el prefijo indica la regional
(p. ej. `TGU` = Cartagena, `TGV` = Medellín, `RIL` = ...). Ejemplos reales:
`TGU-14471`, `TGV-08021`, `RIL-12181`.

## Uso como librería

```python
from mintrack.client import ANMClient

client = ANMClient()
titulos = client.consultar_por_expediente("TGU-14471")
print(titulos[0].to_dict())
```

## Bot de Telegram

MinTrack incluye un bot de Telegram que permite consultar títulos mineros
directamente desde el chat.

### Comandos

```
/start            Saludo y resumen de uso.
/help             Ayuda detallada.
/exp <código>     Consulta exacta por código de expediente. Ej: /exp TGU-14471
/buscar <texto>   Búsqueda parcial por código. Ej: /buscar TGU
```

### Crear el bot y obtener el token (con @BotFather)

1. Abre Telegram y busca **@BotFather** (verificado oficial).
2. Envía `/newbot`.
3. BotFather te pide un **nombre** para el bot (lo que ven los usuarios, p. ej.
   `MinTrack Colombia`).
4. Te pide un **username** que termine en `bot`, p. ej. `mintrack_colombia_bot`.
5. BotFather responde con un **token** con el formato `123456789:ABCdef...`.
   **Cópialo**: es el `TELEGRAM_BOT_TOKEN`.
6. (Opcional) `/setprivacy` → `Disable`, así el bot lee mensajes en grupos
   (solo necesita responder a comandos por defecto).

### Probar el bot en local

```bash
pip install -e .
# En Windows PowerShell:
$env:TELEGRAM_BOT_TOKEN = "PEGA-TU-TOKEN-AQUÍ"
# En Linux/macOS:
export TELEGRAM_BOT_TOKEN=PEGA-TU-TOKEN-AQUÍ
python -m mintrack.bot
```

Luego abre tu bot en Telegram y envía `/start` y `/exp TGU-14471`.

## Despliegue en GitHub (correr 24/7)

El repo incluye dos workflows en `.github/workflows/`:

- **`ci.yml`**: corre en cada push/PR. Verifica sintaxis, imports y la ayuda del
  CLI. No necesita el token.
- **`run-bot.yml`**: corre el bot de Telegram en un runner de GitHub Actions con
  long-polling, reiniciándose cada 15 minutos vía `cron`.

### Pasos para desplegar el bot en GitHub Actions

1. Sube el proyecto a un repositorio en GitHub.
2. Ve a **Settings → Secrets and variables → Actions → New repository secret**.
3. Nombre: `TELEGRAM_BOT_TOKEN`. Valor: el token que te dio @BotFather.
4. Ve a la pestaña **Actions**, selecciona el workflow **Run MinTrack Telegram
   Bot** y pulsa **Run workflow** para iniciarlo de inmediato (no esperes al
   cron). Verás el log en vivo.
5. Habla con tu bot en Telegram.

### ⚠️ Limitaciones del plan gratuito de GitHub Actions

GitHub Actions **no está diseñado para servicios 24/7**. El plan gratuito
impone:

- Cada job dura **máximo ~6 horas**; GitHub lo cancela al superar el límite (por
  eso el workflow define `timeout-minutes: 350` y el cron lo reinicia cada
  15 min, con `concurrency` para no duplicar instancias).
- **2.000 minutos/mes** de cuota gratuita en cuentas personales.
- GitHub **puede pausar** workflows en repositorios sin actividad (>60 días sin
  commits).

Esto significa que **habrá ventanas sin servicio** y que no es una solución
estable de producción. Es útil para pruebas o para mantener el bot activo de
forma ocasional. Para un **24/7 real**, considera alguna de estas alternativas
(todas con tier gratuito o muy económico):

- **GitHub Actions self-hosted runner** en tu propia PC/servidor (el runner
  corre en tu máquina y el workflow lo usa; así no consumes minutos de la
  nube y el bot corre en tu equipo).
- **Railway / Render / Fly.io**: despliegue de un proceso Python persistente
  (`python -m mintrack.bot`) que se reinicia automáticamente. Más estable que
  Actions.
- Cualquier VPS barato (Hetzner, DigitalOcean) con `systemd` o `tmux`.

El código del bot es el mismo en todos los casos: solo cambia cómo se mantiene
vivo el proceso.

## Estructura

```
mintrack/
├── __init__.py
├── client.py     # Cliente REST de la ANM (FeatureServer)
├── models.py     # Modelo TituloMinero
├── geo.py        # Conversión ArcGIS -> GeoJSON
├── cli.py        # CLI (mintrack consultar / buscar)
└── bot.py        # Bot de Telegram (mintrack-bot / python -m mintrack.bot)
.github/workflows/
├── ci.yml        # CI: sintaxis + imports + CLI help
└── run-bot.yml   # Despliegue del bot en Actions (cron cada 15 min)
```

## Avisos

- Este proyecto consume un servicio público de la ANM. Úsalo de forma
  responsable y evita consultas masivas innecesarias.
- Los datos devueltos reflejan el estado del servicio de la ANM en el momento de
  la consulta; no se almacenan localmente.
- El token de Telegram es un secreto: nunca lo subas al repo. Usa el secret de
  GitHub Actions o una variable de entorno local.
