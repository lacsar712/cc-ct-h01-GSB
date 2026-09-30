"""仅用于本地/CI 无 PostgreSQL 时的测试配置；docker-compose 仍使用默认 Postgres 配置。"""

import os

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("SQLITE_NAME", ":memory:"),
    }
}
