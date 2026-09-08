"""Export legacy runtime settings as data, without executing any command."""
import argparse
import json
from pathlib import Path


def export(config: dict) -> dict:
    return {"scenarios": "harness/END-TO-END.md", "holdout": ".factory/holdout/HOLDOUT.md",
            "environment": {key: config[key] for key in
                            ("driver", "http", "cli", "library", "browser") if key in config},
            "legacy_timeouts": {"e2e_timeout_s": config.get("e2e_timeout_s"),
                                "agent_timeout_s": config.get("agent", {}).get("timeout_s")},
            "requirements": {"fresh_environment_per": ["runtime", "holdout", "retry", "mutation"],
                             "fresh_database": True, "assertion_evidence_required": True},
            "integration_required": "Map this data to the pinned runtime workflow's declared inputs. "
                                    "Timeouts are observations for migration, not factory retry policy."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).with_name("harness.config.json"))
    args = parser.parse_args()
    print(json.dumps(export(json.loads(args.config.read_text(encoding="utf-8"))), indent=2))
