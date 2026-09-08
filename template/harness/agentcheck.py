"""Retired harness agent launcher. Runtime agents belong to Archon nodes."""

class AgentCheckFailed(RuntimeError):
    pass

def run_rung(*args, **kwargs):
    raise AgentCheckFailed("agentcheck is retired. Invoke archon-verify-runtime through "
                           "factory run with scenario and environment inputs. "
                           "See factory/MIGRATION.md; this shim executes nothing.")

if __name__ == "__main__":
    import sys
    print("agentcheck is retired. Use factory run archon-verify-runtime; see factory/MIGRATION.md.", file=sys.stderr)
    raise SystemExit(2)
