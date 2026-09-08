"""Retired compatibility entrypoint. No workflow or provider is executed."""
from consumer import refuse


def main(argv=None):
    return refuse('arm')


if __name__ == "__main__":
    raise SystemExit(main())
