"""Display native state once; never infer lifecycle from a log deadline."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "factory"))
from consumer import main
raise SystemExit(main(["status", *sys.argv[1:]]))
