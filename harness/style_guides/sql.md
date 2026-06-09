---
applies_to: [postgres, mysql]
---

## SQL Style Guide

### Source
- GitLab SQL Design Guidelines (https://about.gitlab.com/handbook/business-technology/data-team/platform/sql-style-guide/)
- SQL Style Guide — Simon Holywell (https://www.sqlstyle.guide/)

### Casing & keywords
- Uppercase SQL keywords: `SELECT`, `FROM`, `WHERE`, `JOIN`, `ON`, `GROUP BY`, `ORDER BY`, `WITH`, `AS`, `CASE WHEN`, `THEN`, `ELSE`, `END`. Lowercase identifiers (tables, columns, aliases).
- Don't quote identifiers unless you must — quoted names hide case sensitivity bugs and break across databases.

### Naming
- `snake_case` for all identifiers.
- Tables: plural nouns describing the entity (`users`, `orders`, `order_line_items`).
- Columns: singular, descriptive. Primary key is `id`; foreign keys are `<referenced_table_singular>_id` (`user_id`, `order_id`).
- Timestamps: `<verb>_at` (`created_at`, `updated_at`, `deleted_at`). Booleans: `is_<state>` / `has_<thing>` (`is_active`, `has_paid`).
- Avoid reserved words as identifiers (`order`, `user`, `select` — choose `purchase_order`, `account_user`, `pick_list`).
- Never abbreviate beyond well-known acronyms (`ip`, `url`, `id`). `acct` and `qty` save four characters and lose all clarity.

### Layout
- One major clause per line. Vertically align column lists under `SELECT`, predicates under `WHERE`, conditions under `ON`.
  ```
  SELECT
      u.id,
      u.email,
      COUNT(o.id) AS order_count
  FROM users AS u
  LEFT JOIN orders AS o
      ON o.user_id = u.id
      AND o.status = 'paid'
  WHERE u.created_at >= '2024-01-01'
  GROUP BY u.id, u.email
  ORDER BY order_count DESC
  ```
- 4-space indentation inside subqueries and CTEs.
- Trailing commas at end of line, not leading.
- Always alias tables in any query with more than one (`users AS u`, `orders AS o`); always qualify columns with the alias.
- `AS` is required for column aliases and recommended for table aliases — make the rename explicit.

### Joins
- Explicit `JOIN` keyword every time (`INNER JOIN`, `LEFT JOIN`) — never comma-join in the `FROM` clause.
- `ON` clauses on their own line(s), indented under the `JOIN`.
- `INNER JOIN` is the default — write `INNER JOIN` explicitly when readers might wonder if you meant `LEFT`.

### Queries
- `SELECT *` only in ad-hoc exploration. Production queries enumerate columns so downstream callers don't break when a column is added.
- Use CTEs (`WITH ... AS (...)`) to name intermediate result sets when the query has more than one logical stage. Nested subqueries past one level become unreadable.
- Filter as early as possible — predicates that limit rows go in the deepest subquery / CTE that can host them.
- Avoid `OR` across different columns; UNION two simpler queries instead — the planner usually picks better.

### DDL
- Migrations are immutable: never edit a deployed migration; always write a new one.
- Use `NOT NULL` by default. Allow null only when null genuinely means "unknown / not applicable" in the domain.
- Add indexes for every foreign key and every common predicate column.
- Use timestamptz (PostgreSQL) / explicit timezone columns; never naive timestamps.
