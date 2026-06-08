---
applies_to: [django]
---

## Python — Django

### When this skill applies
The workspace has `manage.py`, a `settings.py` (or `settings/` package), and per-feature `apps/` or top-level app directories each containing `models.py`, `views.py`, `urls.py`, `apps.py`.

### File layout (idiomatic)
```
manage.py
project_name/        # Django project (settings, root urls, wsgi/asgi)
  settings.py        # or settings/ package split base.py / dev.py / prod.py
  urls.py
  asgi.py
apps/
  users/
    models.py
    views.py
    serializers.py   # if using DRF
    urls.py
    migrations/
    admin.py
    apps.py
```

### Conventions to follow
- One app per bounded context. Don't put models for unrelated domains in the same app.
- Settings split: `base.py` for common, `dev.py`/`prod.py` import from base and override. Select via `DJANGO_SETTINGS_MODULE`.
- Use `get_user_model()` rather than importing `User` directly — projects often have a custom user model.
- DRF: keep serializers small; push business logic into model methods or services, not view code.
- Querysets are lazy; chain filters, don't list-materialize unless needed.

### Common patches the LLM gets wrong
- Adding a new field to `models.py` without `makemigrations`.
- Direct `from django.contrib.auth.models import User` (breaks projects with a custom user model — use `get_user_model()`).
- Putting business logic in views instead of model methods or a services module.
- Forgetting `app_name = "users"` in app-level `urls.py`, which breaks namespaced reversing.

### Migrations
- `python manage.py makemigrations` → review the generated file → `python manage.py migrate`.
- For data migrations, write a separate `RunPython` operation in its own migration file.
- Build/test command: `python manage.py test && python manage.py makemigrations --check --dry-run` (the dry-run catches forgotten migrations in CI).
