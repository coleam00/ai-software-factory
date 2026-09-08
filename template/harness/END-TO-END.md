# Runtime scenarios

Example task-service data. Replace these with the observable journeys for your app.
Shared runtime verification owns execution and evidence.

## A person captures a task and finishes it

1. Add a task called `refill the kalimba humidifier`.
2. Check it shows up on the list, still open.
3. Mark it done.
4. Reload the list. It is still marked done, and the open count went down by one.

**What would make this fail:** the task does not come back after the reload, the
count is stale, or completing one task changes the state of another.

## The list survives a restart

1. Add two tasks with distinct names.
2. Complete one of them.
3. Restart the app.
4. Both tasks are still there, and exactly the one you completed is done.

**What would make this fail:** anything held only in memory, or a write that is
acknowledged before it is persisted.
