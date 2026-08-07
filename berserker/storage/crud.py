"""
berserker.storage.crud — CRUD operation helpers for the Database class.

Provides convenience methods that build SQL from table names and dicts:
    db.insert("session", {"id": "x", "title": "Hello"})
    db.update("session", {"id": "x"}, {"title": "Updated"})
    db.delete("session", {"id": "x"})
"""

import json
import time


def _rowid(db):
    """Return the last inserted rowid."""
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def insert(db, table, data):
    """
    Insert a single row and return the rowid.

    Args:
        db: Database instance
        table: table name
        data: dict of column -> value

    Returns:
        The integer rowid of the inserted row.
    """
    columns = list(data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)
    values = [_serialize(v) for v in data.values()]

    db.execute(
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
        values,
    )
    return _rowid(db)


def insert_many(db, table, rows):
    """
    Insert multiple rows in a single transaction.

    Args:
        db: Database instance
        table: table name
        rows: list of dicts

    Returns:
        Number of rows inserted.
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)
    values_list = [[_serialize(v) for v in row.values()] for row in rows]

    db.executemany(
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
        values_list,
    )
    return len(rows)


def update(db, table, where, data):
    """
    Update rows matching the where clause.

    Args:
        db: Database instance
        table: table name
        where: dict of column -> value for the WHERE clause
        data: dict of column -> value to update

    Returns:
        Number of rows affected.
    """
    set_parts = [f"{k} = ?" for k in data]
    where_parts = [f"{k} = ?" for k in where]

    values = [_serialize(v) for v in data.values()]
    values += [_serialize(v) for v in where.values()]

    set_clause = ", ".join(set_parts)
    where_clause = " AND ".join(where_parts)

    cur = db.execute(
        f"UPDATE {table} SET {set_clause} WHERE {where_clause}",
        values,
    )
    return cur.rowcount


def delete(db, table, where):
    """
    Delete rows matching the where clause.

    Args:
        db: Database instance
        table: table name
        where: dict of column -> value for the WHERE clause

    Returns:
        Number of rows deleted.
    """
    where_parts = [f"{k} = ?" for k in where]
    values = [_serialize(v) for v in where.values()]
    where_clause = " AND ".join(where_parts)

    cur = db.execute(f"DELETE FROM {table} WHERE {where_clause}", values)
    return cur.rowcount


def upsert(db, table, data, conflict_columns):
    """
    Insert or update on conflict (SQLite 3.24+ UPSERT).

    Args:
        db: Database instance
        table: table name
        data: dict of column -> value
        conflict_columns: list of column names for ON CONFLICT

    Returns:
        The rowid (existing or new).
    """
    columns = list(data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)
    values = [_serialize(v) for v in data.values()]

    update_cols = [c for c in columns if c not in conflict_columns]
    if not update_cols:
        update_cols = columns

    set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    conflict_clause = ", ".join(conflict_columns)

    db.execute(
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict_clause}) DO UPDATE SET {set_clause}",
        values,
    )
    return _rowid(db)


def _serialize(value):
    """Serialize a value for SQLite storage (JSON for dicts/lists)."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _deserialize(value):
    """Deserialize a value from SQLite (parse JSON strings)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value
