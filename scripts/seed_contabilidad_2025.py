import argparse
import json

from api import create_app
from api.services.accounting_service import SeedService, DEFAULT_YEAR


def main():
    parser = argparse.ArgumentParser(description="Seed contabilidad 2025 en MongoDB")
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--force", action="store_true", help="Limpia e inserta nuevamente master data del año")
    parser.add_argument("--dry-run", action="store_true", help="Solo valida/cuenta registros sin escribir en DB")
    parser.add_argument(
        "--sync-departments",
        action="store_true",
        help="Sincroniza departamentos desde master_units después del seed",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        service = SeedService()
        result = service.seed(year=args.year, force=args.force, dry_run=args.dry_run)
        output = {"seed": result}

        if args.sync_departments and not args.dry_run:
            output["syncDepartments"] = service.sync_departments_from_units(year=args.year)

        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
