"""Catálogo de servicios de la plataforma (BR-001, BR-002).

Fuente única de verdad de los servicios ofrecidos:

- ``alistamiento``: Alistamiento documental.
- ``monitoreo``: Monitoreo automatizado.
- ``radicacion``: Radicación automatizada.
- ``paquete_integral``: Paquete Integral MINTRACK (incluye los tres anteriores,
  con tarifa preferencial según BR-002).

Los servicios pueden contratarse de manera individual o en conjunto, y la
oferta puede ampliarse en el futuro: para agregar un servicio basta con añadir
una entrada a :data:`SERVICIOS`; los menús, precios y el wizard de contratación
se generan dinámicamente desde este catálogo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Servicio:
    """Definición de un servicio contratable."""

    codigo: str
    nombre: str
    resumen: str
    detalle: str
    precio: str
    incluye: tuple[str, ...] = ()


ALISTAMIENTO = "alistamiento"
MONITOREO = "monitoreo"
RADICACION = "radicacion"
PAQUETE_INTEGRAL = "paquete_integral"

SERVICIOS: dict[str, Servicio] = {
    ALISTAMIENTO: Servicio(
        codigo=ALISTAMIENTO,
        nombre="Alistamiento documental",
        resumen=(
            "Revisión formal y organización de la documentación requerida "
            "para tu solicitud minera."
        ),
        detalle=(
            "Incluye (BR-011):\n"
            "• Verificar que los documentos existan.\n"
            "• Verificar vigencia.\n"
            "• Revisar firmas.\n"
            "• Revisar formatos.\n"
            "• Organizar la documentación.\n\n"
            "No incluye conceptos jurídicos, financieros o técnicos, ni "
            "evaluación de viabilidad minera."
        ),
        precio="$1.000.000",
    ),
    MONITOREO: Servicio(
        codigo=MONITOREO,
        nombre="Monitoreo automatizado",
        resumen=(
            "Vigilancia automática de un área en ANNA Minería para detectar "
            "su publicación para liberación."
        ),
        detalle=(
            "• Consultas automáticas cada 6 horas en ANNA Minería (BR-003).\n"
            "• Alertas por liberación de áreas, cambios de estado y novedades.\n"
            "• Notificación al cliente y al administrador por Telegram.\n"
            "• Reportes periódicos configurables (novedades, semanal, "
            "quincenal o mensual)."
        ),
        precio="$2.000.000 por área / año",
    ),
    RADICACION: Servicio(
        codigo=RADICACION,
        nombre="Radicación automatizada",
        resumen=(
            "Radicación automática de la solicitud en ANNA Minería cuando el "
            "área sea programada para liberación."
        ),
        detalle=(
            "Se ejecuta automáticamente cuando (BR-012):\n"
            "• El servicio está contratado y el pago validado.\n"
            "• La documentación está completa y vigente.\n"
            "• El usuario de ANNA Minería está activo.\n"
            "• Existe al menos un PIN disponible.\n\n"
            "Si ANNA presenta fallas, el sistema reintenta automáticamente "
            "mientras el área continúe disponible (BR-013)."
        ),
        precio="$20.000.000",
    ),
    PAQUETE_INTEGRAL: Servicio(
        codigo=PAQUETE_INTEGRAL,
        nombre="Paquete Integral MINTRACK",
        resumen=(
            "Alistamiento documental + Monitoreo automatizado + Radicación "
            "automatizada, con tarifa preferencial."
        ),
        detalle=(
            "Incluye (BR-002):\n"
            "• Alistamiento documental.\n"
            "• Monitoreo automatizado.\n"
            "• Radicación automatizada.\n\n"
            "Tarifa preferencial frente a la contratación individual."
        ),
        precio="$20.000.000 (tarifa preferencial)",
        incluye=(ALISTAMIENTO, MONITOREO, RADICACION),
    ),
}

# Códigos heredados de versiones anteriores del bot. Se conservan para que las
# solicitudes ya registradas en la base de datos sigan mostrándose bien.
ALIASES = {
    "aplicacion": RADICACION,
    "centinela": MONITOREO,
}

# Teclas del wizard: "1" -> alistamiento, "2" -> monitoreo, etc.
TECLAS = {str(i + 1): codigo for i, codigo in enumerate(SERVICIOS)}


def resolver(codigo: str) -> str:
    """Resuelve aliases heredados al código vigente del servicio."""
    return ALIASES.get(codigo, codigo)


def nombre(codigo: str) -> str:
    """Nombre legible del servicio (resuelve aliases y códigos desconocidos)."""
    servicio = SERVICIOS.get(resolver(codigo))
    return servicio.nombre if servicio else codigo


def dividir_csv(texto: str | None) -> list[str]:
    """Convierte 'monitoreo,radicacion' en ['monitoreo', 'radicacion']."""
    if not texto:
        return []
    return [parte.strip() for parte in texto.split(",") if parte.strip()]


def nombres(codigos: list[str]) -> str:
    """Nombres legibles de una lista de códigos, unidos por coma."""
    return ", ".join(nombre(codigo) for codigo in codigos)


def nombres_csv(texto: str | None) -> str:
    """Nombres legibles de una lista CSV de códigos (como la guarda la BD)."""
    return nombres(dividir_csv(texto))


def parsear_seleccion(texto: str) -> list[str]:
    """Interpreta la respuesta del wizard de contratación.

    Acepta números de la lista ("2", "1,3") o códigos directos
    ("monitoreo"). Devuelve los códigos seleccionados sin duplicados y en
    orden. El Paquete Integral no admite combinarse (ya incluye los demás).
    """
    partes = [p for p in re.split(r"[,\s]+", (texto or "").strip()) if p]
    if not partes:
        raise ValueError("Debes seleccionar al menos un servicio.")

    seleccion: list[str] = []
    for parte in partes:
        codigo = TECLAS.get(parte, resolver(parte.lower()))
        if codigo not in SERVICIOS:
            raise ValueError(
                f"'{parte}' no es una opción válida. Responde con los números "
                "de la lista, por ejemplo: 2 o 1,3."
            )
        if codigo not in seleccion:
            seleccion.append(codigo)

    if PAQUETE_INTEGRAL in seleccion and len(seleccion) > 1:
        raise ValueError(
            "El Paquete Integral ya incluye Alistamiento, Monitoreo y "
            "Radicación: selecciónalo solo (opción 4)."
        )
    return seleccion


def texto_opciones() -> str:
    """Lista numerada de servicios con tarifa, para el wizard de contratación."""
    lineas = []
    for tecla, codigo in TECLAS.items():
        servicio = SERVICIOS[codigo]
        lineas.append(f"{tecla}️⃣ {servicio.nombre} — {servicio.precio}")
    return "\n".join(lineas)
