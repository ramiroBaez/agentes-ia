"""The deployment command line: what you only have to do once.

A deployment needs three things the API cannot provide: creating the first
API key (the one thing done once, and it decides who gets in), checking the
status, and purging old data. Everything else is done over HTTP.

    python -m cli key --name "Ops" --scopes admin:all --plan pro
    python -m cli status
    python -m cli purge --days 30
"""

from __future__ import annotations

import argparse
import sys

from persistence import Database
from security import KNOWN_SCOPES, KeyManager
from settings import settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli",
        description="Aurora Shop Ops: administración de la base.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    key = sub.add_parser("key", help="create an API key and show it only once")
    key.add_argument("--name", required=True, help="who the key is issued to")
    key.add_argument(
        "--scopes",
        default="chat:write",
        help=f"comma-separated, from: {', '.join(KNOWN_SCOPES)}",
    )
    key.add_argument("--plan", default="free", help="free or pro")

    sub.add_parser("status", help="how many things are in the database")

    purge = sub.add_parser("purge", help="delete old traces and conversations")
    purge.add_argument("--days", type=int, default=None, help="defaults to the configured value")

    return parser


def _scopes(text: str) -> list[str]:
    """An unknown scope is a typo, not a missing permission."""
    requested = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [s for s in requested if s not in KNOWN_SCOPES]
    if unknown:
        raise SystemExit(
            f"scope desconocido: {', '.join(unknown)}. "
            f"Opciones: {', '.join(KNOWN_SCOPES)}"
        )
    return requested


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database(settings.DB_PATH)
    db.init()
    try:
        if args.command == "key":
            register = KeyManager(db).generate_key(
                name=args.name,
                scopes=_scopes(args.scopes),
                plan=args.plan,
            )
            print(register["key"])
            print(
                "Copiá esta clave: es lo único que se muestra una vez. "
                "Es la que se pega en la pantalla de entrada.",
                file=sys.stderr,
            )
            return 0

        if args.command == "status":
            clients = len(db.list_clients())
            traces = len(db.get_traces(limit=200))
            pending = len(db.list_approvals(status="pending"))
            print(f"base:     {settings.DB_PATH}")
            print(f"clientes: {clients}")
            print(f"trazas:   {traces} (las últimas 200)")
            print(f"a la espera de una persona: {pending}")
            return 0

        if args.command == "purge":
            deleted = db.purge_old(args.days)
            for table, how_many in deleted.items():
                print(f"{table}: {how_many}")
            return 0
    finally:
        db.close()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
