import argparse
import sys

from tenderflow.services.errors import ServiceError
from tenderflow.services.identity import bootstrap_administrator


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision the first administrator and emit its one-time activation token"
    )
    parser.add_argument("--login", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    try:
        result = bootstrap_administrator(
            login=args.login, display_name=args.display_name
        )
    except ServiceError as error:
        print(error.code, file=sys.stderr)
        raise SystemExit(1) from None
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
