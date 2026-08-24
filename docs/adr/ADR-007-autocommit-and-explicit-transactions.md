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

## Proving the fix

A unit test against a fake repository has no transaction to roll back, so it cannot see a partial
write — the fake either has both rows or neither, by construction. The only test that can observe
this bug is a functional test against the real database: make the second statement raise, then assert
the *first* row is absent afterward. That is the one check a missing `connection.transaction()` fails
and a wrapped one passes.

## Operationalization

- `project/core/composition_root.py` — the pool's `autocommit=True` kwarg, where this decision is
  made once for every connection the application borrows.
- `project/infrastructure/persistence/` — where a future repository method gains a second `execute()`
  and must wrap both in `async with connection.transaction():`.
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
