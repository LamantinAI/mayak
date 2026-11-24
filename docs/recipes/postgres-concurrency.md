# Concurrent PostgreSQL writes and migration ownership

Run the worked example against the disposable database used by the test suite:

```sh
uv run pytest -q tests/db/test_postgres_concurrency_recipe.py
```

The test starts two independent requests against one room. Each obtains a separate connection
and meets at a barrier before writing. Within its transaction each locks the same parent row,
then inserts. The exclusion constraint protects the database invariant and its named violation
maps to HTTP 409. The test requires HTTP 201 and 409 plus exactly one stored row. It then adds a nullable column
to the populated table and verifies that the prior row can be read with a null value.
The cleanup drops the booking table while an independent GiST index still uses the extension.

Use the lock consistently in every application path that changes the invariant, and keep
database constraints for other writers. When several resources are locked, acquire them in a
stable order. Use bounded retries for transient deadlocks only when it is safe to replay the
whole transaction after rollback. A deadlock alone does not establish a booking conflict.

Lock with one statement and read with the next. A `SELECT ... JOIN ... FOR UPDATE` that waited for
another writer re-checks the new version of the locked row against the joined rows as its own
snapshot saw them; when that writer changed the join, the row drops out and the query returns
nothing. In a product made from this template a lookup shaped that way answered 404 to 38 of 40
concurrent HTTP requests that should have got 409. When the operation allows it, lock the parent by
its key alone (`SELECT id FROM items WHERE id = %s FOR UPDATE`), then read what the decision needs
with a separate statement in the same transaction: under READ COMMITTED a new statement sees what
the other writer committed. Test it with a second connection that holds the lock while the first
request waits.

## Upgrade an existing database

- Provision `btree_gist` before using equality with GiST. Alembic autogeneration does not add
  extensions. Treat the extension as a database prerequisite owned by deployment. If your feature
  runs `CREATE EXTENSION IF NOT EXISTS`, a feature downgrade must leave the shared extension in place.
- Run the upgrade from the predecessor revision with an existing row. When adding an optional
  cancellation timestamp, verify the old booking keeps its status and gets a null timestamp.
- If the feature supports downgrade, test it with a separate table and index that still use the
  extension. The rollback should remove its own tables and indexes while preserving the consumer.
  Use a disposable database for both directions.
