import argparse
import logging

import uvicorn

from clouder.config import Settings
from clouder.db import Database
from clouder.models import Tenant
from clouder.security import issue_key
from clouder.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudER operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    bootstrap = sub.add_parser("bootstrap", help="Create a tenant and one-time owner API key")
    bootstrap.add_argument("--name", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    if args.command == "serve":
        uvicorn.run(
            "clouder.app:create_app", factory=True, host=args.host, port=args.port, access_log=False
        )
        return
    db = Database(settings)
    if args.command == "migrate":
        db.migrate()
        print("Schema is at version 1")
    elif args.command == "bootstrap":
        if not 1 <= len(args.name) <= 80:
            parser.error("Tenant name must contain 1–80 characters")
        with db.transaction() as session:
            tenant = Tenant(name=args.name)
            session.add(tenant)
            session.flush()
            _, token = issue_key(
                session, tenant.id, "bootstrap-owner", "owner", settings.api_key_days
            )
        print(f"Tenant: {tenant.id}\nOwner API key (shown once): {token}")
    else:
        Worker(db, settings).run()


if __name__ == "__main__":
    main()
