import { emit, jsonEnv } from "../../.shared/utils.ts";
import { knownFailures, readLedger } from "./ledger.ts";

const holdouts = jsonEnv<{ holdouts?: { enabled?: boolean; path?: string } }>("config", {}).holdouts;
emit({ issues: holdouts?.enabled && holdouts.path ? knownFailures(readLedger(holdouts.path)) : {} });
