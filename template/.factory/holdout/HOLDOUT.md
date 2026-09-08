# Holdout scenarios

Example task-service data. Replace with independent composed scenarios.
The producer must establish isolation and start a fresh environment for this pass.
No factory Python helper enforces a private holdout boundary.

## Three lists, one restart, and a rename in the middle

1. Create tasks `quarry-lantern`, `sable-ferry` and `nine-of-cups`.
2. Complete `sable-ferry`.
3. Rename `nine-of-cups` to `nine-of-swords`.
4. Restart the app.
5. Exactly three tasks exist. Exactly one is done, and it is `sable-ferry`.
   `nine-of-cups` does not appear anywhere. The open count is 2.

## Deleting the completed one does not resurrect it

1. Starting from the state above, delete `sable-ferry`.
2. The list has two tasks, both open, open count 2.
3. Restart the app.
4. Still two tasks, both open. `sable-ferry` is gone and the completed count is 0.
