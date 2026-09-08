# ADR-007: Autocommit Pool, Explicit Transactions for Multi-Statement Writes

## Status

Accepted (2026-08-24)

## Decision

The shared psycopg pool built in `composition_root.py` opens with `"autocommit": True`. Every
`execute()` on a borrowed connection commits on its own. A repository method that issues exactly one
statement needs nothing further. A method that must land two or more statements together — an
aggregate and its outbox row, an order and its line items — opts in to atomicity explicitly, inside
the connection block it already has:

```python
async with self._pool.connection() as connection:
    async with connection.transaction():
        await connection.execute(INSERT_AGGREGATE, (...))
        await connection.execute(INSERT_OUTBOX_ROW, (...))
```

## Rationale

Autocommit is right for the shape this template actually ships: every method in the reference
vertical's repository opens a connection, issues exactly one `execute()` or `cursor.execute()`, and
returns. There is no second statement to fall out of step with the first. Autocommit also closes a
real footgun that a manually-committed pool has and this one does not: without it, a method that
forgets `connection.commit()` leaves the connection sitting inside an open transaction when it goes
back to the pool, and the next unrelated request that borrows it inherits that open transaction,
blind to why.

It stops being right the moment a vertical needs two statements to land together. With autocommit on,
each `execute()` commits independently, so a crash or an exception between the two leaves the first
permanently written and the second never attempted — a partial write, not an atomic one.

No gate in this repository catches that failure mode:

- `make quality-gates` runs none of your SQL (CLAUDE.md, "It runs none of your queries" — the gate
  runs no query, so it cannot see that two of them needed to be one transaction).
- The shipped `reference_task_repository.py` cannot demonstrate the fix either. All three of its
  methods issue exactly one `execute()`, so `async with connection.transaction():` does not appear
  anywhere in the worked example, and a vertical author has nothing to copy from when their own
  repository grows a second statement.

## Read-modify-write across requests (added 2026-09-02)

A transaction makes two statements land together. It does not make a *read* and a later *write*
agree, and that is the second failure this pool shape invites — the one an agent hits first, because
it is the shape of every update.

`PATCH /reference-tasks/{id}` reads the task, changes one field and writes it back. Between the read
and the write another request can do the same. With the obvious statement:

```sql
UPDATE reference_tasks SET title = %s, details = %s WHERE id = %s
```

both requests report success and the later write erases the earlier one. Nothing raises, both
clients are told their change was stored, and the data is simply gone. Wrapping the update in
`connection.transaction()` does not help: each request's read and write are already atomic, they are
just atomic over stale data.

The reference vertical therefore ships the conditional form, and a vertical that needs an update
copies it rather than the obvious one:

```python
_UPDATE_BY_ID = (
    "UPDATE reference_tasks SET title = %s, details = %s, status = %s, updated_at = %s "
    "WHERE id = %s AND updated_at = %s RETURNING id, title, details, status, created_at, updated_at"
)
```

`updated_at` is the optimistic token: the service passes the value it *read* as the condition and a
fresh one as data, so a row somebody else has written since matches nothing, the repository returns
`None`, and the service raises `ConflictError` — 409, which the caller retries after re-reading. The
`RETURNING` clause keeps the write and the read of the stored state one statement, so the answer
cannot be a row that changed again in between, and the method stays at a single `execute()` where
autocommit is correct.

One field of the same method is not about concurrency at all and is included because a first
attempt gets it wrong the same way: a patch has three states per field — absent, set to a value,
set to null — and `None` can only carry two of them. `details: str | None = None` therefore makes
`{"details": null}` indistinguishable from a body that never mentioned details, so a nullable
column can never be emptied and the request still answers 200. The service takes an `UNCHANGED`
sentinel instead, and the endpoint decides between them with `payload.model_fields_set`.

A project that cannot accept a timestamp as the token — because it writes the same row more than
once per microsecond, or because it wants the version visible to clients — uses an integer `version`
column with `SET version = version + 1 ... WHERE id = %s AND version = %s` instead. The mechanism is
identical; only the token changes.

Measured on 2026-09-02: an agent given "add a vertical with PATCH" and following
`.agents/skills/add-vertical/SKILL.md` wrote the blind form, because the reference vertical had no
update to copy and this ADR said nothing about the case. All 876 tests passed.

## Where the single-row token does not reach (added 2026-09-08)

The `updated_at` token above answers one question: did somebody else touch *this* row between my
read and my write. It says nothing about a second row. Two requests that each read zero matching
rows, each conclude a slot is free, and each `INSERT` their own report a conflict-free 200 apiece —
the token never runs, because neither request's write disagreed with anything it had itself read.

Measured in a project built from this template: a berth-booking vertical took reservations with
exactly the shape this ADR already ships — read, decide, single-row `INSERT`, one `execute()` per
method, autocommit correct by the Decision above. 24 concurrent `POST /reservations` requests
against one berth produced two overlapping reservations for the same berth in 10 of 20 runs. Nothing
in this ADR was violated; a set-level invariant — no two overlapping intervals on one resource, no
second active reservation, no item sold twice — was never something the single-row token promised to
hold, and this ADR said nothing about the case until now.

Two mechanisms reach it, and neither is a bigger version of the token above:

- **A constraint the database enforces**, when the invariant is expressible as one. A partial
  exclusion constraint rejects the second overlapping row at `INSERT` time, no prior read needed —
  the equality term on `berth_id` needs the `btree_gist` extension, `CREATE EXTENSION IF NOT EXISTS
  btree_gist;` once per database, before this migration runs:

  ```sql
  ALTER TABLE reservations ADD CONSTRAINT no_overlapping_berth_reservations
      EXCLUDE USING gist (berth_id WITH =, daterange(arrival, departure) WITH &&)
      WHERE (status = 'active');
  ```

- **`SELECT ... FOR UPDATE` on the parent row**, when the invariant spans rows a constraint cannot
  name. The repository locks the parent inside the transaction it already opens for a
  multi-statement write, then checks and inserts before releasing it:

  ```python
  async with self._pool.connection() as connection:
      async with connection.transaction():
          await connection.execute(_LOCK_BERTH_FOR_UPDATE, (berth_id,))
          await connection.execute(_CHECK_NO_OVERLAP, (berth_id, arrival, departure))
          await connection.execute(_INSERT_RESERVATION, (...))
  ```

Either way, the database raises a driver error on the second writer — `errors.ExclusionViolation` or
`errors.UniqueViolation` from psycopg — and the repository translates it into a domain
`ConflictError`, the same shape as the zero-row `RETURNING` above, so the API layer answers 409
instead of the client seeing the driver's exception as a 500.

The reference vertical cannot demonstrate this fix. It has one row per aggregate and no set-level
invariant to violate, so — exactly as it could not show a transaction spanning two statements, and
could not show the read-modify-write case before 2026-09-02 — copying `ReferenceTaskRepository`
alone will not carry this pattern into a vertical that needs it.

## A foreign key's deletion policy is a domain decision, not a schema detail (added 2026-09-08)

`alembic revision --autogenerate` writes exactly the `ForeignKey(...)` the ORM model declares. A
model that names no `ondelete` produces a migration that names none either, and PostgreSQL's own
default takes over silently — behaviourally `RESTRICT`: deleting a parent with a child still
attached raises `IntegrityError`, which reaches the client as a 500 from the driver, not as the
domain's own answer.

Measured in two projects built from this template: a parent delete against a child holding an active
reference failed exactly this way in `make test-e2e`, where the domain wanted "cannot delete while a
booking is still active" — a 409 or a 403 the project already had in prose and nowhere in the
schema.

`ondelete` is not a fact autogenerate can infer, because it is not a fact about the schema — it is
what the domain wants done to the child when the parent goes away, and the right answer differs by
relationship even inside one project: `CASCADE` when the child has no meaning without the parent, a
domain check plus `RESTRICT` when the rule needs its own error message instead of the database's,
`SET NULL` when the child is meant to survive as an orphan. Choose it where the `ForeignKey(...)` is
written, before autogeneration ever runs — a migration written from a bare declaration has to be
edited by hand or regenerated, not patched around:

```python
customer_id: Mapped[str] = mapped_column(
    ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
)
```

Nothing in `make quality-gates` catches a bare `ForeignKey` — it is a syntactically valid column, not
a malformed one — and `make test-e2e` only catches it if some test actually deletes a parent with a
child present and asserts the domain's answer rather than accepting whatever the driver happens to
raise.

## Proving the fix

A unit test against a fake repository has no transaction to roll back, so it cannot see a partial
write — the fake either has both rows or neither, by construction. The only test that can observe
this bug is a functional test against the real database: make the second statement raise, then assert
the *first* row is absent afterward. That is the one check a missing `connection.transaction()` fails
and a wrapped one passes.

The same asymmetry applies to the lost update above, and for the same reason. A fake can model the
condition — `tests/application/test_reference_task_vertical.py` does, and its in-memory double
refuses a write whose expected timestamp does not match — but nothing interleaves inside one
process, so a fake proves only that the service passes the right argument, never that the database
enforces it. `tests/functional/src/test_reference_task_repository.py` stages the interleaving against
real PostgreSQL: write, let another writer land, then write from the first read and assert the row
still holds the other writer's value. `tests/functional/src/test_reference_tasks_api.py` runs the
unstaged version over HTTP with `asyncio.gather`, asserting the invariant rather than a fixed
outcome — every request that answered 200 must find its own change in the final state.

## Operationalization

- `project/core/composition_root.py` — the pool's `autocommit=True` kwarg, where this decision is
  made once for every connection the application borrows.
- `project/infrastructure/persistence/` — where a future repository method gains a second `execute()`
  and must wrap both in `async with connection.transaction():`; also where a set-level invariant gets
  its exclusion constraint or its `SELECT ... FOR UPDATE`, per "Where the single-row token does not
  reach" above.
- `project/infrastructure/persistence/orm_models.py` — where a `ForeignKey(...)` is written, and so
  where its `ondelete` is chosen, per "A foreign key's deletion policy is a domain decision" above.
  `tests/infrastructure/test_persistence_models.py` guards that no foreign key in the shipped
  metadata is missing one.
- `.agents/skills/add-vertical/SKILL.md` — the constraint list a vertical author reads before writing
  a repository; it points here rather than repeating the reasoning.

## Consequences

- A repository method with a single statement needs no transaction block; adding one would be inert
  ceremony, not a mistake, but it is also not required.
- A repository method that grows a second statement must add `async with connection.transaction():`
  in the same change, and prove it with a functional test that raises between the two statements —
  not a unit test, per "Proving the fix" above.
- Nothing in `make quality-gates` will flag a missing transaction wrapper; review and `make
  test-e2e` are what catch it, so a diff touching `project/infrastructure/persistence/` is finished
  by the functional lane, not the gate.
- A set-level invariant — no two overlapping rows, no double sale — is not covered by the
  `updated_at` token or by wrapping statements in a transaction; it needs a database constraint or a
  `SELECT ... FOR UPDATE` on the parent, per "Where the single-row token does not reach" above, and a
  concurrent functional test to prove it, not a unit test against a fake.
- A `ForeignKey` with no explicit `ondelete` autogenerates anyway — `make quality-gates` passes and
  the migration applies — and then answers a domain delete rule with the driver's `IntegrityError`
  and a 500 the first time a project actually exercises it, instead of the 409 or 403 the domain
  wanted.
