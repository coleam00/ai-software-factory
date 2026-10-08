# Ownership decision

The shared Archon SDLC pack owns agent execution, stage composition, retry policy,
approval gates and forge mutations. Factory installs a complete pinned source,
prepares project data, invokes native entrypoints and displays engine state.

An earlier design kept a six-stage scheduler, acceptance receipts, private
assumption approval, an autonomy ladder and after-merge deployment in the factory
itself. Archon's shared workflows cover each of those requirements, so none of
that orchestration lives in the factory. See [what each side owns](../template/factory/MIGRATION.md).

The integration stays supervised until the producer proves runtime coverage,
queue gating, governed discovery, regression, standing intake and release ownership.
