#!/usr/bin/env python3
"""
Make a sanitized dump of the live database, for docker/compose.dev.yml. See
"A copy of live data" in README.rst.

  scripts/dump_for_dev.py [-o DIR] [--admin-user USER]

Run it on the live server, from the repo root. It copies the database in
config.py's SQLALCHEMY_DATABASE_URI into a scratch database on the same
server, scrubs the copy, dumps it to DIR/fbc-dev-<date>.sql.gz and drops the
copy. The fbc user can't create databases, so --admin-user names a MariaDB
user that can; its password is asked for (or read from stdin, if that isn't
a terminal). Without it, the config.py user makes the scratch database.

Before finishing it checks the dump for rows of the emptied tables and for
any of the live tokens, password hashes, API keys and user emails, and
deletes it if it finds one.

It only needs Python 3 and the MariaDB client (mariadb and mariadb-dump, or
the older mysql and mysqldump names), not the app's packages.
"""

import argparse
import ast
import datetime
import getpass
import gzip
import importlib.abc
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from urllib.parse import parse_qs, unquote, urlsplit

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# Kept as schema only, with no rows: tokens, API keys and the app's users.
# The dev database gets an admin from `flask fab create-admin` instead.
EMPTIED_TABLES = [
    "ebay_oauth_token",
    "discogs_oauth_token",
    "ab_api_key",
    "ab_register_user",
    "ab_user_role",
    "ab_user_group",
    "ab_user",
]

# Live values that must not turn up anywhere in the dump.
SECRET_COLUMNS = [
    ("ebay_oauth_token", "refresh_token"),
    ("discogs_oauth_token", "token"),
    ("discogs_oauth_token", "secret"),
    ("ab_api_key", "key_hash"),
    ("ab_api_key", "lookup_hash"),
    ("ab_register_user", "password"),
    ("ab_register_user", "email"),
    ("ab_user", "password"),
    ("ab_user", "email"),
]

# Buyers' eBay usernames become buyer-1, buyer-2, ..., in order of their
# first order, so the same buyer keeps the same name within the dump.
SCRUB_BUYERS = """
CREATE TEMPORARY TABLE buyer_alias AS
    SELECT buyer,
           CONCAT('buyer-', ROW_NUMBER() OVER (
               ORDER BY MIN(created_time), MIN(order_id))) AS alias
    FROM ebay_order
    WHERE buyer IS NOT NULL AND buyer <> ''
    GROUP BY buyer;
UPDATE ebay_order o JOIN buyer_alias a ON o.buyer = a.buyer
    SET o.buyer = a.alias;
DROP TEMPORARY TABLE buyer_alias;
"""
BUYER_ALIAS = "^buyer-[0-9]+$"


class Failed(Exception):
    pass


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Stands in for the modules config.py imports (flask_appbuilder) when
    they aren't installed, as on the live server outside the app's image.
    Only those: an installed package may try a module that isn't there on
    purpose (dateutil tries winreg), and needs that import to fail."""

    def __init__(self, names):
        self.names = names

    def find_spec(self, name, path, target=None):
        if name not in self.names:
            return None
        return importlib.util.spec_from_loader(name, self, is_package=True)

    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        pass


def imported_modules(source):
    """The modules some Python source imports, and their parent packages."""
    modules = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.append(node.module)
    names = set()
    for module in modules:
        parts = module.split(".")
        names.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return names


def database_uri(repo=REPO):
    """config.py's SQLALCHEMY_DATABASE_URI, which also honours the variable
    of that name in the environment."""
    path = os.path.join(repo, "config.py")
    if not os.path.exists(path):
        raise Failed(f"There's no config.py in {os.path.abspath(repo)}.")
    with open(path) as f:
        finder = _StubFinder(imported_modules(f.read()))
    spec = importlib.util.spec_from_file_location("config", path)
    config = importlib.util.module_from_spec(spec)
    # Last, so installed modules are used when there are any.
    sys.meta_path.append(finder)
    try:
        spec.loader.exec_module(config)
    except Exception as e:
        raise Failed(f"Couldn't read config.py: {type(e).__name__}: {e}") from e
    finally:
        sys.meta_path.remove(finder)
        for name, module in list(sys.modules.items()):
            if getattr(module, "__loader__", None) is finder:
                del sys.modules[name]
    return config.SQLALCHEMY_DATABASE_URI


def parse_uri(uri):
    """The connection in a mysql:// or mysql+pymysql:// URI, as client
    options."""
    parts = urlsplit(uri)
    if not parts.scheme.startswith(("mysql", "mariadb")):
        raise Failed(
            f"SQLALCHEMY_DATABASE_URI isn't a MySQL/MariaDB URI: {parts.scheme}"
        )
    database = unquote(parts.path.lstrip("/"))
    if not database:
        raise Failed("SQLALCHEMY_DATABASE_URI doesn't name a database.")
    options = {"user": unquote(parts.username or "")}
    if parts.password is not None:
        options["password"] = unquote(parts.password)
    if parts.hostname:
        options["host"] = parts.hostname
    if parts.port:
        options["port"] = str(parts.port)
    socket = parse_qs(parts.query).get("unix_socket")
    if socket:
        options["socket"] = socket[0]
    return database, options


def option_file(directory, name, options):
    """A --defaults-extra-file holding the options, so the password is never
    on a command line."""
    path = os.path.join(directory, name)
    lines = ["[client]"]
    for key, value in options.items():
        value = value.replace("\\", "\\\\")
        lines.append(f'{key}="{value}"')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def quote_name(name):
    return "`" + name.replace("`", "``") + "`"


def quote_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def find_tool(*names):
    """The first of the names that's MariaDB's client. Some installs only
    have the mysql names for it; MySQL's own tools under those names don't
    count, as its mysqldump doesn't work the same with MariaDB."""
    for name in names:
        path = shutil.which(name)
        if not path:
            continue
        version = subprocess.run(
            [path, "--version"], capture_output=True, text=True
        ).stdout
        if "MariaDB" in version:
            return path
    raise Failed(
        f"MariaDB's {' or '.join(names)} isn't installed; it comes with the "
        "MariaDB client."
    )


class Client:
    """Runs MariaDB's client and dump tool as one user."""

    def __init__(self, defaults_file, mariadb, mariadb_dump):
        self.defaults = f"--defaults-extra-file={defaults_file}"
        self.mariadb = mariadb
        self.mariadb_dump = mariadb_dump

    def query(self, sql, database=None):
        """Rows of the last statement, tab-separated, as lists of strings."""
        cmd = [self.mariadb, self.defaults, "--batch", "--skip-column-names"]
        if database:
            cmd.append(database)
        result = subprocess.run(cmd, input=sql, capture_output=True, text=True)
        if result.returncode:
            raise Failed(f"{self.mariadb} failed: {result.stderr.strip()}")
        return [line.split("\t") for line in result.stdout.splitlines()]

    def dump(self, database, stdout):
        return subprocess.Popen(
            [
                self.mariadb_dump,
                self.defaults,
                # A consistent snapshot, without locking the live app's tables.
                "--single-transaction",
                # A superset of the live tables' utf8mb3; each CREATE TABLE
                # keeps its own character set.
                "--default-character-set=utf8mb4",
                "--hex-blob",
                database,
            ],
            stdout=stdout,
            stderr=subprocess.PIPE,
        )


def tables(client, database):
    rows = client.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = {quote_string(database)}"
    )
    return {row[0] for row in rows}


def secret_values(client, database, existing):
    """Each live secret, keyed by where it came from, so a failed check can
    say where without printing it."""
    columns = client.query(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = {quote_string(database)}"
    )
    columns = {(table, column) for table, column in columns}
    secrets = []
    for table, column in SECRET_COLUMNS:
        if table not in existing or (table, column) not in columns:
            continue
        # As hex, so no value is mangled by the client's escaping.
        rows = client.query(
            f"SELECT HEX({quote_name(column)}) FROM {quote_name(table)} "
            f"WHERE {quote_name(column)} <> ''",
            database,
        )
        for (value,) in rows:
            secrets.append((f"{table}.{column}", bytes.fromhex(value)))
    return secrets


def dump_escaped(value):
    """How mariadb-dump writes the value inside a string literal."""
    for raw, escaped in [
        (b"\\", b"\\\\"),
        (b"\0", b"\\0"),
        (b"\n", b"\\n"),
        (b"\r", b"\\r"),
        (b"\x1a", b"\\Z"),
        (b"'", b"\\'"),
        (b'"', b'\\"'),
    ]:
        value = value.replace(raw, escaped)
    return value


def check_dump(path, emptied, secrets):
    """Problems with the gzipped dump: rows for an emptied table, or a live
    secret. mariadb-dump escapes newlines inside values, so a value is
    always on one line."""
    inserts = re.compile(
        rb"^INSERT INTO `(" + b"|".join(re.escape(t.encode()) for t in emptied) + rb")`"
    )
    needles = []
    for where, value in secrets:
        needles.append((where, value))
        if dump_escaped(value) != value:
            needles.append((where, dump_escaped(value)))
    problems = set()
    with gzip.open(path, "rb") as f:
        for line in f:
            match = inserts.match(line) if emptied else None
            if match:
                problems.add(f"it has rows for {match.group(1).decode()}")
            for where, needle in needles:
                if needle in line:
                    problems.add(f"it has a value from the live {where}")
    return sorted(problems)


def run(args):
    tools = (find_tool("mariadb", "mysql"), find_tool("mariadb-dump", "mysqldump"))

    database, live_options = parse_uri(database_uri())
    admin_options = dict(live_options)
    if args.admin_user:
        admin_options["user"] = args.admin_user
        admin_options.pop("password", None)
        if sys.stdin.isatty():
            password = getpass.getpass(f"MariaDB password for {args.admin_user}: ")
        else:
            password = sys.stdin.readline().rstrip("\n")
        # Empty for a user that signs in with unix_socket, under sudo.
        if password:
            admin_options["password"] = password

    scratch = f"{database}_dev_scrub"
    date = datetime.date.today().isoformat()
    final = os.path.join(args.output, f"fbc-dev-{date}.sql.gz")
    partial = os.path.join(args.output, f".fbc-dev-{date}.sql.gz.part")

    os.makedirs(args.output, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        live = Client(option_file(tmp, "live.cnf", live_options), *tools)
        admin = Client(option_file(tmp, "admin.cnf", admin_options), *tools)

        existing = tables(live, database)
        if not existing:
            raise Failed(f"The {database} database has no tables, or isn't there.")
        emptied = [t for t in EMPTIED_TABLES if t in existing]
        secrets = secret_values(live, database, existing)
        charset, collation = live.query(
            "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
            "FROM information_schema.SCHEMATA "
            f"WHERE SCHEMA_NAME = {quote_string(database)}"
        )[0]

        if admin.query(
            "SELECT 1 FROM information_schema.SCHEMATA "
            f"WHERE SCHEMA_NAME = {quote_string(scratch)}"
        ):
            raise Failed(
                f"The scratch database {scratch} is already there, maybe left "
                "from an earlier run that was killed. Check it isn't in use, "
                f"then drop it: DROP DATABASE {quote_name(scratch)};"
            )

        print(f"Copying {database} to {scratch} ...")
        try:
            admin.query(
                f"CREATE DATABASE {quote_name(scratch)} "
                f"CHARACTER SET {charset} COLLATE {collation}"
            )
        except Failed as e:
            if args.admin_user:
                raise
            raise Failed(
                f"{e}\nThe {live_options['user']} user can't create the scratch "
                "database; pass --admin-user with one that can."
            ) from None
        try:
            copy(live, admin, database, scratch)

            print("Scrubbing the copy ...")
            scrub = ["SET FOREIGN_KEY_CHECKS = 0;"]
            scrub += [f"DELETE FROM {quote_name(t)};" for t in emptied]
            scrub.append("SET FOREIGN_KEY_CHECKS = 1;")
            if "ebay_order" in existing:
                scrub.append(SCRUB_BUYERS)
            admin.query("\n".join(scrub), scratch)
            if "ebay_order" in existing:
                (left,) = admin.query(
                    "SELECT COUNT(*) FROM ebay_order WHERE buyer <> '' "
                    f"AND buyer NOT REGEXP {quote_string(BUYER_ALIAS)}",
                    scratch,
                )[0]
                if left != "0":
                    raise Failed(f"{left} eBay orders still have a buyer's username.")

            print(f"Dumping it to {final} ...")
            write_dump(admin, scratch, partial)
        finally:
            admin.query(f"DROP DATABASE IF EXISTS {quote_name(scratch)}")

    print("Checking the dump ...")
    problems = check_dump(partial, emptied, secrets)
    if problems:
        os.remove(partial)
        raise Failed("The dump was deleted, since " + "; ".join(problems) + ".")
    os.replace(partial, final)
    size = os.path.getsize(final) / 1024 / 1024
    print(f"\n{final} ({size:.1f} MB)")
    print("Copy it to docker/dev-db/ on the dev machine; see README.rst.")


def copy(live, admin, database, scratch):
    dump = live.dump(database, subprocess.PIPE)
    load = subprocess.run(
        [admin.mariadb, admin.defaults, scratch],
        stdin=dump.stdout,
        capture_output=True,
    )
    dump.stdout.close()
    dump_err = dump.stderr.read().decode().strip()
    if dump.wait():
        raise Failed(f"mariadb-dump of {database} failed: {dump_err}")
    if load.returncode:
        raise Failed(f"Loading {scratch} failed: {load.stderr.decode().strip()}")


def write_dump(client, database, path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as out:
            dump = client.dump(database, subprocess.PIPE)
            shutil.copyfileobj(dump.stdout, out)
            err = dump.stderr.read().decode().strip()
            if dump.wait():
                raise Failed(f"mariadb-dump of {database} failed: {err}")
    except BaseException:
        os.remove(path)
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Make a sanitized dump of the live database for the dev setup."
    )
    parser.add_argument(
        "-o",
        "--output",
        default=".",
        help="the folder to write fbc-dev-<date>.sql.gz to (default: here)",
    )
    parser.add_argument(
        "--admin-user",
        help="a MariaDB user that can create and drop databases, for the "
        "scratch copy; its password is asked for",
    )
    args = parser.parse_args()
    # So progress and errors come out in order.
    sys.stdout.reconfigure(line_buffering=True)
    try:
        run(args)
    except Failed as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
