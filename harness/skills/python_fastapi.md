## Python — FastAPI

### When this skill applies
The workspace has `main.py`/`app.py` importing from `fastapi`, plus a `requirements.txt` or `pyproject.toml` listing `fastapi`, `uvicorn`, and (usually) `pydantic`. Routes live in `routers/` or `api/`; ORM models in `models/`; DB sessions in `database.py` or `db/`.

### File layout (idiomatic)
```
app/
  main.py            # FastAPI() instance + middleware
  routers/           # APIRouter modules grouped by resource
  models/            # SQLAlchemy / SQLModel models
  schemas/           # Pydantic request/response models (NOT models/)
  database.py        # engine, SessionLocal, get_db dependency
  core/config.py     # Settings via pydantic-settings
  dependencies.py    # auth, current_user, etc.
alembic/             # migration scripts
alembic.ini
```

### Conventions to follow
- Use `APIRouter(prefix="/users", tags=["users"])` per resource. Don't put endpoints in `main.py` directly beyond `/health`.
- Pydantic schemas (request/response) and SQLAlchemy models are **different classes** — never reuse the model class as a response schema.
- Use `Annotated[Session, Depends(get_db)]` for DB injection (Python 3.10+ syntax); avoid `Session = Depends(get_db)` in defaults.
- Async endpoints get `async def`; sync endpoints get `def`. Don't `await` sync ORM calls; use `await` only with explicitly async DB clients (asyncpg, async SQLAlchemy).
- Status codes via `status.HTTP_201_CREATED`, not raw integers.

### Common patches the LLM gets wrong
- Returning ORM models directly instead of response schemas (causes lazy-load surprises and leaks internal fields).
- Forgetting `response_model=UserResponse` on the route decorator.
- Mixing sync and async DB clients in the same request.
- Adding endpoints to `main.py` instead of a router.

### Migrations
- Alembic: `alembic revision --autogenerate -m "msg"` then `alembic upgrade head`.
- Build/test command typically: `pytest && alembic upgrade head --sql > /dev/null` (autogenerate is checked manually first).
