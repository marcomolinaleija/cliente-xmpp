from __future__ import annotations

import sqlite3

for database_path in (
    "/var/lib/slidge/slidge.sqlite",
    "/var/lib/slidge/whatsapp/whatsapp.db",
):
    print(database_path)
    connection = sqlite3.connect(database_path)
    for (sql,) in connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND sql IS NOT NULL ORDER BY name"
    ):
        print(sql)
