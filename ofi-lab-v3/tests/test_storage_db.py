import sqlite3

from storage.db import open_database, DEFAULT_DB_PATH


def test_open_database_creates_file_if_missing(tmp_path):
    db_path = tmp_path / "v3.db"
    conn = open_database(str(db_path))
    assert db_path.exists()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_open_database_enables_wal_and_foreign_keys(tmp_path):
    db_path = tmp_path / "v3.db"
    conn = open_database(str(db_path))
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert journal == "wal"
    assert fk == 1
    conn.close()


def test_default_db_path_constant():
    assert DEFAULT_DB_PATH == "/data/v3.db"
