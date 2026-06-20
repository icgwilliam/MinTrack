"""Línea de comandos de MinTrack para consultar títulos mineros de la ANM."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .client import ANMClient, ANMError
from .geo import arcgis_feature_to_geojson, titulos_to_feature_collection

LABELS = {
    "codigo_exp": "Código expediente",
    "estado_exp": "Estado",
    "modalidade": "Modalidad",
    "etapa": "Etapa",
    "minerales": "Minerales",
    "municipios": "Municipios",
    "departamento": "Departamento",
    "solicitante": "Solicitante / Titular",
    "grupo_trab": "Grupo de trabajo",
    "area_ha": "Área (ha)",
    "fecha_insc": "Fecha inscripción",
    "fecha_term": "Fecha terminación",
    "tipo_explo": "Tipo de explotación",
    "capaminera": "Capa minera",
    "producto": "Producto",
    "fid": "FID",
    "shape_area": "Área geométrica (grados²)",
    "shape_length": "Longitud geométrica (grados)",
}


def _formatar_titulo(titulo, show_geom: bool) -> str:
    lines: list[str] = []
    lines.append(f"=== Título minero: {titulo.codigo_exp} ===")
    data = titulo.to_dict()
    for key, label in LABELS.items():
        value = data.get(key)
        if value is None or value == "":
            continue
        lines.append(f"{label}: {value}")
    if show_geom and titulo.geometry:
        lines.append("Geometría: incluida (polígono en MAGNA-SIRGAS, SR 4686)")
    return "\n".join(lines)


def cmd_consultar(args: argparse.Namespace) -> int:
    client = ANMClient()
    try:
        titulos = client.consultar_por_expediente(
            args.codigo, return_geometry=not args.no_geometry
        )
    except ANMError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not titulos:
        print(
            f"No se encontró ningún título con el código de expediente '{args.codigo}'.",
            file=sys.stderr,
        )
        return 1

    if args.format == "json":
        out = [t.to_dict() for t in titulos]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif args.format == "geojson":
        fc = titulos_to_feature_collection(titulos)
        print(json.dumps(fc, ensure_ascii=False, indent=2))
    else:
        for t in titulos:
            print(_formatar_titulo(t, show_geom=not args.no_geometry))
            print()
    return 0


def cmd_buscar(args: argparse.Namespace) -> int:
    client = ANMClient()
    try:
        titulos = client.buscar_por_codigo(args.texto, limit=args.limit)
    except ANMError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not titulos:
        print(
            f"No se encontraron títulos que coincidan con '{args.texto}'.",
            file=sys.stderr,
        )
        return 1

    if args.format == "json":
        print(json.dumps([t.to_dict() for t in titulos], ensure_ascii=False, indent=2))
    elif args.format == "geojson":
        print(json.dumps(titulos_to_feature_collection(titulos), ensure_ascii=False, indent=2))
    else:
        print(f"Se encontraron {len(titulos)} título(s):")
        for t in titulos:
            print(
                f"- {t.codigo_exp} | {t.departamento} | {t.municipios} | "
                f"{t.modalidade} | {t.etapa}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mintrack",
        description=(
            "Consulta títulos mineros vigentes de Colombia a partir del código "
            "de expediente, usando los geoservicios públicos de la ANM."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_consultar = sub.add_parser(
        "consultar",
        help="Consulta exacta por código de expediente (ej. TGU-14471).",
    )
    p_consultar.add_argument("codigo", help="Código de expediente (formato AAA-#####).")
    p_consultar.add_argument(
        "--format",
        choices=("text", "json", "geojson"),
        default="text",
        help="Formato de salida (por defecto: text).",
    )
    p_consultar.add_argument(
        "--no-geometry",
        action="store_true",
        help="No descargar la geometría del polígono del título.",
    )
    p_consultar.set_defaults(func=cmd_consultar)

    p_buscar = sub.add_parser(
        "buscar",
        help="Búsqueda parcial por código de expediente (LIKE).",
    )
    p_buscar.add_argument("texto", help="Texto a buscar dentro del código de expediente.")
    p_buscar.add_argument(
        "--format",
        choices=("text", "json", "geojson"),
        default="text",
        help="Formato de salida (por defecto: text).",
    )
    p_buscar.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Número máximo de resultados (por defecto: 50).",
    )
    p_buscar.set_defaults(func=cmd_buscar)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
