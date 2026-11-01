import gzip
import importlib.util
import os
import stat
import sys

import pytest

_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "dump_for_dev.py")
_spec = importlib.util.spec_from_file_location("dump_for_dev", _path)
dump_for_dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dump_for_dev)


def test_parse_uri():
    database, options = dump_for_dev.parse_uri(
        "mysql+pymysql://fbc:p%40ss%2Fw0rd@db.example:3307/fbc?charset=utf8mb4"
    )
    assert database == "fbc"
    assert options == {
        "user": "fbc",
        "password": "p@ss/w0rd",
        "host": "db.example",
        "port": "3307",
    }


def test_parse_uri_socket_and_no_password():
    database, options = dump_for_dev.parse_uri(
        "mysql://fbc@localhost/fbc?unix_socket=/run/mysqld/mysqld.sock"
    )
    assert database == "fbc"
    assert options == {
        "user": "fbc",
        "host": "localhost",
        "socket": "/run/mysqld/mysqld.sock",
    }


@pytest.mark.parametrize("uri", ["sqlite:///app.db", "mysql+pymysql://fbc:pw@host/"])
def test_parse_uri_refuses(uri):
    with pytest.raises(dump_for_dev.Failed):
        dump_for_dev.parse_uri(uri)


def test_option_file(tmp_path):
    path = dump_for_dev.option_file(
        str(tmp_path), "live.cnf", {"user": "fbc", "password": 'a\\b"c'}
    )
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with open(path) as f:
        assert f.read() == '[client]\nuser="fbc"\npassword="a\\\\b"c"\n'


def test_database_uri_without_the_apps_packages(tmp_path, monkeypatch):
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URI", raising=False)
    (tmp_path / "config.py").write_text(
        "import os\n"
        "from not_installed.security.manager import AUTH_DB\n"
        "AUTH_TYPE = AUTH_DB\n"
        "SQLALCHEMY_DATABASE_URI = os.environ.get(\n"
        "    'SQLALCHEMY_DATABASE_URI', 'mysql+pymysql://fbc:pw@localhost/fbc')\n"
    )
    assert dump_for_dev.database_uri(str(tmp_path)) == (
        "mysql+pymysql://fbc:pw@localhost/fbc"
    )
    # The stand-ins don't outlast reading it.
    assert "not_installed" not in sys.modules

    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", "mysql://other@host/db")
    assert dump_for_dev.database_uri(str(tmp_path)) == "mysql://other@host/db"


def test_database_uri_without_config(tmp_path):
    with pytest.raises(dump_for_dev.Failed, match="no config.py"):
        dump_for_dev.database_uri(str(tmp_path))


def _dump(tmp_path, *lines):
    path = tmp_path / "dump.sql.gz"
    with gzip.open(path, "wb") as f:
        f.write(b"\n".join(lines) + b"\n")
    return str(path)


# As mariadb-dump 10.11 writes them, a row to a line.
CLEAN = [
    b"-- MariaDB dump",
    b"CREATE TABLE `ab_user` (`id` int(11) NOT NULL);",
    b"LOCK TABLES `ab_user` WRITE;",
    b"UNLOCK TABLES;",
    b"INSERT INTO `ebay_order` VALUES",
    b"('1-1','2026-01-01 00:00:00','buyer-1'),",
    b"('1-2','2026-01-02 00:00:00','buyer-2');",
]


def test_check_dump_clean(tmp_path):
    secrets = [("ab_user.password", b"scrypt:32768:8:1$salt$hash")]
    assert dump_for_dev.check_dump(_dump(tmp_path, *CLEAN), ["ab_user"], secrets) == []


def test_check_dump_finds_rows_of_an_emptied_table(tmp_path):
    path = _dump(tmp_path, *CLEAN, b"INSERT INTO `ab_user` VALUES", b"(1,'admin');")
    assert dump_for_dev.check_dump(path, ["ebay_oauth_token", "ab_user"], []) == [
        "it has rows for ab_user"
    ]


def test_check_dump_finds_secrets(tmp_path):
    path = _dump(
        tmp_path,
        *CLEAN,
        b"INSERT INTO `purchase_lot` VALUES",
        b"(1,'v^1.1#i^1#r^1');",
        b"INSERT INTO `unit` VALUES",
        b"(1,'it\\'s me@example.com');",
    )
    secrets = [
        ("ebay_oauth_token.refresh_token", b"v^1.1#i^1#r^1"),
        ("ab_user.email", b"it's me@example.com"),
        ("ab_user.password", b"never-there"),
    ]
    # Never the values themselves.
    assert dump_for_dev.check_dump(path, [], secrets) == [
        "it has a value from the live ab_user.email",
        "it has a value from the live ebay_oauth_token.refresh_token",
    ]
