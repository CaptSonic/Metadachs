"""python -m metadachs --config /config/config.toml serve|scan|check|jobs|retry"""

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from .config import load
from .service import Service


def main():
    parser = argparse.ArgumentParser(description="Metadachs NFO-Dienst")
    parser.add_argument("--config", type=Path, default=Path("/config/config.toml"))
    parser.add_argument("command", choices=("serve", "scan", "check", "jobs", "retry"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    try:
        config = load(args.config)
        if args.command == "check":
            missing = [lib.name for lib in config.libraries if not lib.path.is_dir()]
            if missing:
                raise ValueError("Medien-Mounts fehlen: " + ", ".join(missing))
            print(f"Konfiguration OK; {len(config.libraries)} Mediatheken; dry_run={config.dry_run}")
            return
        service = Service(config, recover=args.command in ("serve", "scan"), config_path=args.config)
        if args.command == "serve":
            def stop(signum, frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop)
            try:
                service.serve()
            except KeyboardInterrupt:
                pass
            return
        try:
            if args.command == "scan":
                service.scan(once=True)
                while service.process_one():
                    pass
            elif args.command == "retry":
                service.store.retry()
            print(json.dumps(service.store.report(), indent=2, ensure_ascii=False))
        finally:
            service.store.close()
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f"Fehler: {exc}\n")


if __name__ == "__main__":
    main()