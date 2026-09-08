# Ownership decision

The shared Archon SDLC pack owns agent execution, stage composition, retry policy,
approval gates and forge mutations. Factory installs a complete pinned source,
prepares project data, invokes native entrypoints and displays engine state.

The six-stage scheduler, independent acceptance receipts, private assumption
approval, autonomy ladder and after-merge deployment were retired in this migration.
Their original requirements do not justify keeping parallel orchestration. See the
[responsibility and data mapping](../template/factory/MIGRATION.md).

The migration is supervised until the integrated producer proves runtime coverage,
queue gating, governed discovery, regression, standing intake and release ownership.
