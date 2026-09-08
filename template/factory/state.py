"""Native state display. No factory labels, receipts or transitions."""
import sys
from consumer import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["status"]))
