#!/usr/bin/env python3
"""Мигратор: применяет migrations/NNN_*.sql по порядку, повторный запуск безопасен.

Установка:  pip install pymysql
Запуск:     python migrate.py            (применить недостающие)
            python migrate.py --status   (показать, что применено)
Окружение:  DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD
Использовать МИГРАЦИОННУЮ учётную запись (runtime-роли DDL не выполняют).

Правила файлов миграций: только простые SQL-выражения, разделённые ';',
без хранимых процедур и DELIMITER; строки, начинающиеся с '--', игнорируются.
Уже применённая миграция не перезапускается — менять её нельзя, новые правки
оформляйте новым файлом 002_..., 003_... (контрольная сумма проверяется).
"""
import hashlib
import os
import sys
from pathlib import Path

import pymysql

MIGRATIONS = Path(__file__).parent / "migrations"


def split_statements(sql: str):
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        stmt = stmt.strip()
        if stmt:
            yield stmt


def main() -> None:
    conn = pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "kyberryvok"),
        charset="utf8mb4",
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("SET time_zone = '+00:00'")
        cur.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   filename VARCHAR(255) CHARACTER SET ascii NOT NULL,
                   checksum CHAR(64) CHARACTER SET ascii NOT NULL,
                   applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                   PRIMARY KEY (filename)
               ) ENGINE=InnoDB"""
        )
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        applied = dict(cur.fetchall())

        files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
        if "--status" in sys.argv:
            for f in files:
                print(("[x] " if f.name in applied else "[ ] ") + f.name)
            return

        for f in files:
            text = f.read_text(encoding="utf-8")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if f.name in applied:
                if applied[f.name] != digest:
                    sys.exit(f"ОШИБКА: {f.name} изменён после применения. Создайте новую миграцию.")
                print(f"пропуск   {f.name}")
                continue
            for stmt in split_statements(text):
                cur.execute(stmt)  # DDL в MySQL неявно коммитится
            cur.execute("INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)", (f.name, digest))
            print(f"применено {f.name}")
    conn.close()


if __name__ == "__main__":
    main()
