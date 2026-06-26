---
applies_to: [postgres, redis, mysql]
---

## Databases — PostgreSQL, Redis, MySQL

### When this skill applies
The workspace's manifest or config references one of:
- **PostgreSQL**: `psycopg`, `asyncpg`, `pg`, `postgresql` connection string, `pgvector` extension
- **Redis**: `redis-py`, `ioredis`, `redis` client crate / Jedis / Lettuce
- **MySQL**: `mysql-connector`, `pymysql`, `mysql2`, `mariadb` driver

### Connection conventions
- Use a connection POOL, not raw connections per request. PostgreSQL: `asyncpg.create_pool()`, `psycopg.pool.AsyncConnectionPool`, SQLAlchemy `create_engine(..., pool_size=20)`. Redis: `redis.ConnectionPool`. MySQL: same pattern.
- Default pool sizes are usually too low for production (5–10). For a real workload start at 20 and tune by metrics.
- Set explicit timeouts: connection timeout, query/command timeout, idle timeout. Default-no-timeout is the most common cause of "the database is fine but my service is hung".
- Read connection strings from env vars via a typed config layer — never hardcode.

### Migrations by ecosystem
| Stack | Tool | Command |
|---|---|---|
| FastAPI / SQLAlchemy | Alembic | `alembic revision --autogenerate -m "msg"` → `alembic upgrade head` |
| Django | Django migrations | `manage.py makemigrations` → `manage.py migrate` |
| Spring Boot | Flyway | drop `V<n>__name.sql` into `src/main/resources/db/migration/` |
| Spring Boot | Liquibase | edit `db.changelog-master.xml` |
| React backend (Node API consumed by frontend) | n/a — backend MUST be Python or Java | Migrate via the chosen backend's tooling above |

### pgvector specifics (PostgreSQL only)
- Requires `CREATE EXTENSION vector;` once per database.
- Column type: `vector(1536)` (or whatever embedding dimension). Always specify the dimension.
- Index: `CREATE INDEX ON items USING hnsw (embedding vector_cosine_ops);` — HNSW is the modern default; IVFFlat is faster to build but slower to query.
- Choose the distance op consistently: `vector_l2_ops` (L2), `vector_cosine_ops` (cosine), `vector_ip_ops` (inner product). The wrong one silently returns nonsense neighbors.

### Common patches the LLM gets wrong
- N+1 queries: looping over a list and querying inside the loop. Use joins, `IN (...)`, or eager loading.
- Missing index on a column that's used in `WHERE`, `JOIN`, or `ORDER BY` — write the index migration in the same change.
- Forgetting `LIMIT` on debug queries that work fine on dev (100 rows) but timeout on prod (10M rows).
- Storing JSON blobs and then querying them by inner key without a functional index (`CREATE INDEX ON t ((data->>'key'))`).
- For Redis: using `KEYS *` in production code (O(N), blocks the server). Use `SCAN` instead.
- For MySQL: silently truncated VARCHAR fields when `STRICT_TRANS_TABLES` is off — set strict mode explicitly.

### Build / test
- Run migrations as part of the build_command (`alembic upgrade head && pytest`) so a forgotten migration breaks the build, not production.
- For pgvector tests, provide a `CREATE EXTENSION IF NOT EXISTS vector;` in your test setup migration.
