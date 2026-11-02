#!/usr/bin/env python3
"""
Once, on the live server: move the secrets in config.py to docker/.env, as
FBC_CONFIG_* overrides, and line config.py up with config.py.tpl. See
"Settings from the environment" in README.md.

  scripts/move_secrets_to_env.py [--apply] [--diff]

Run it from the repo root. On its own it only reports what it would do, and
changes nothing; --apply makes the changes. --diff also shows the changes to
config.py, with the moved secrets hidden; any other secret in it shows.

The new config.py is config.py.tpl, comments and all, with config.py's
values in it:

- A setting both have gets config.py's value. Inside a dict, such as
  EBAY_SETTINGS, this goes key by key.
- A setting only config.py.tpl has is left commented out, since config.py
  didn't set it. Uncomment the ones you want.
- A setting only config.py has is kept, at the end or inside its dict.
- The secrets (SECRET_KEY, the database URI, and the eBay and Discogs keys
  fbc/devmode.py blanks) are blanked, and their values added to
  docker/.env. One already overridden there is only blanked.

Comments in config.py that aren't in config.py.tpl are dropped. Before
changing anything it checks the app would see exactly the same settings as
before, with docker/.env's overrides applied, and stops if not. The old
config.py is kept as config.py.bak-<time>, readable only by you. It still
holds the secrets, so delete it once the app runs with the new one.

It only needs Python 3, not the app's packages.
"""

import argparse
import ast
import copy
import datetime
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dump_for_dev  # noqa: E402

Failed = dump_for_dev.Failed

# fbc/settings.py's ENV_PREFIX, which this script can't import without the
# app's packages.
PREFIX = "FBC_CONFIG_"

# The settings that move to docker/.env. The eBay and Discogs keys are the
# ones fbc/devmode.py treats as secret.
SECRETS = [
    ("SECRET_KEY",),
    ("SQLALCHEMY_DATABASE_URI",),
    *[
        ("EBAY_SETTINGS", key)
        for key in ("APP_ID", "DEV_ID", "CERT_ID", "RUNAME", "OAUTH_REFRESH_TOKEN")
    ],
    *[
        ("DISCOGS_SETTINGS", key)
        for key in ("CONSUMER_KEY", "CONSUMER_SECRET", "USER_TOKEN")
    ],
]

# Words in a setting's name that suggest a secret this script doesn't know
# about, which it reports rather than moves.
SECRET_WORDS = ("SECRET", "PASSWORD", "TOKEN", "KEY")

UNSET = object()


def dotted(path):
    return ".".join(path)


def env_name(path):
    return PREFIX + "__".join(path)


def lookup(settings, path):
    value = settings
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return UNSET
        value = value[part]
    return value


def apply_overrides(settings, environment):
    """What Flask's from_prefixed_env does to the settings: each FBC_CONFIG_*
    variable, read as JSON when it can be, with __ reaching into dicts."""
    for key in sorted(environment):
        if not key.startswith(PREFIX):
            continue
        value = environment[key]
        try:
            value = json.loads(value)
        except ValueError:
            pass
        *parts, tail = key[len(PREFIX) :].split("__")
        current = settings
        for part in parts:
            current = current.setdefault(part, {})
        current[tail] = value
    return settings


def effective(source, path, environment):
    """The settings config.py's source gives the app, with `environment`
    (docker/.env) set as it would be in the container, before and after
    its overrides."""
    saved = dict(os.environ)
    os.environ.update(environment)
    try:
        names = dump_for_dev.run_config(source, path)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    alone = {key: value for key, value in names.items() if key.isupper()}
    overridden = apply_overrides(copy.deepcopy(alone), environment)
    return alone, overridden


def env_line(path, value):
    """docker/.env's line for a secret, or None if it can't be written as one.
    Single quotes keep Compose from touching $ and #; a value that would
    read as JSON (a number, true, a quoted string) is written as a JSON
    string, so the app gets back the same string."""
    text = value
    try:
        if json.loads(value) != value:
            text = json.dumps(value, ensure_ascii=False)
    except ValueError:
        pass
    if "'" in text or "\n" in text:
        return None
    return f"{env_name(path)}='{text}'"


def assignments(tree):
    """The top-level `NAME = value` statements, by name. The last one wins,
    as when the file runs."""
    found = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            found[node.targets[0].id] = node
    return found


def dict_entries(node):
    """A dict display's (key, value) nodes by key, or None unless every key
    is a string."""
    if not isinstance(node, ast.Dict):
        return None
    entries = {}
    for key, value in zip(node.keys, node.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        entries[key.value] = (key, value)
    return entries


class Source:
    """Python source, with ast positions (UTF-8 byte columns) turned into
    offsets in the text."""

    def __init__(self, text):
        self.text = text
        self.tree = ast.parse(text)
        self.lines = text.splitlines(keepends=True)
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))

    def offset(self, lineno, col):
        line = self.lines[lineno - 1]
        return self.starts[lineno - 1] + len(line.encode()[:col].decode())

    def span(self, node):
        return (
            self.offset(node.lineno, node.col_offset),
            self.offset(node.end_lineno, node.end_col_offset),
        )

    def segment(self, node):
        start, end = self.span(node)
        return self.text[start:end]

    def indent(self, node):
        line = self.lines[node.lineno - 1]
        return line[: len(line) - len(line.lstrip())]

    def line_start(self, lineno):
        return self.starts[lineno - 1]


def reindent(text, old, new):
    """Move a segment's continuation lines from one indent to another."""
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].startswith(old):
            lines[i] = new + lines[i][len(old) :]
    return "\n".join(lines)


class Rebuild:
    """config.py.tpl, with the live config.py's values put in."""

    def __init__(self, template, live, blanks):
        self.tpl = Source(template)
        self.live = Source(live)
        # Secret path: its value, to blank wherever config.py sets it.
        self.blanks = blanks
        self.blanked = set()
        self.edits = []
        self.only_template = []
        self.only_live = []
        self.differ = []
        self.kept_statements = 0

    def build(self):
        tpl_assigns = assignments(self.tpl.tree)
        live_assigns = assignments(self.live.tree)
        for name, node in tpl_assigns.items():
            if name in live_assigns:
                self.merge((name,), node.value, live_assigns[name].value)
            elif name.isupper():
                # Helpers such as basedir stay; only settings are dropped.
                self.comment_out(node, node)
                self.only_template.append((name,))
        self.add_live_only(tpl_assigns, live_assigns)
        text = self.tpl.text
        last = len(text) + 1
        for start, end, new in sorted(self.edits, reverse=True):
            if end > last:
                raise Failed("Overlapping changes; reconcile config.py by hand.")
            text = text[:start] + new + text[end:]
            last = start
        try:
            ast.parse(text)
        except SyntaxError as e:
            raise Failed(f"The new config.py wouldn't parse: {e}") from e
        return text

    def merge(self, path, tpl_value, live_value):
        tpl_entries = dict_entries(tpl_value)
        live_entries = dict_entries(live_value)
        if tpl_entries is None or live_entries is None:
            text = self.live_value(path, live_value, self.tpl.indent(tpl_value))
            if ast.dump(tpl_value) != ast.dump(live_value):
                self.differ.append((path, self.tpl.segment(tpl_value), text))
            self.edits.append((*self.tpl.span(tpl_value), text))
            return
        for key, (key_node, value) in tpl_entries.items():
            if key in live_entries:
                self.merge(path + (key,), value, live_entries[key][1])
            else:
                self.comment_out(key_node, value)
                self.only_template.append(path + (key,))
        extras = [key for key in live_entries if key not in tpl_entries]
        if extras:
            self.insert_entries(path, tpl_value, live_entries, extras)

    def live_value(self, path, node, indent):
        """The live config.py's source for a value, reindented to `indent`,
        with the secrets in it blanked."""
        start, end = self.live.span(node)
        replacements = []
        for secret, value in self.blanks.items():
            if secret[: len(path)] != path:
                continue
            for const in ast.walk(node):
                if isinstance(const, ast.Constant) and const.value == value:
                    replacements.append(self.live.span(const))
                    self.blanked.add(secret)
        text = self.live.text[start:end]
        for const_start, const_end in sorted(set(replacements), reverse=True):
            # An empty string in the same quotes.
            quote = self.live.text[const_start]
            blank = quote * 2 if quote in "'\"" else '""'
            text = text[: const_start - start] + blank + text[const_end - start :]
        return reindent(text, self.live.indent(node), indent)

    def comment_out(self, first, last):
        """Comment out the lines from `first` to `last`, which must hold
        nothing else."""
        line = self.tpl.lines[first.lineno - 1]
        before = line.encode()[: first.col_offset].decode()
        after = self.tpl.lines[last.end_lineno - 1].encode()[last.end_col_offset :]
        if before.strip() or not re.fullmatch(rb"\s*,?\s*(#.*)?\s*", after):
            raise Failed(
                f"config.py.tpl's line {first.lineno} holds more than one"
                " setting; reconcile config.py by hand."
            )
        indent = before
        start = self.tpl.line_start(first.lineno)
        end = self.tpl.line_start(last.end_lineno + 1)
        lines = self.tpl.text[start:end].splitlines(keepends=True)
        text = indent + "# Not set in the old config.py:\n"
        for line in lines:
            body = line[len(indent) :] if line.startswith(indent) else line.lstrip()
            text += indent + "# " + body if body.strip() else line
        self.edits.append((start, end, text))

    def insert_entries(self, path, tpl_dict, live_entries, keys):
        """Add the live config.py's keys a template dict doesn't have, before
        its closing brace."""
        closing = self.tpl.lines[tpl_dict.end_lineno - 1]
        if closing.strip()[:1] != "}":
            raise Failed(
                f"{dotted(path)}'s closing brace in config.py.tpl isn't on its"
                " own line; reconcile config.py by hand."
            )
        if tpl_dict.keys:
            indent = self.tpl.indent(tpl_dict.keys[0])
        else:
            indent = closing[: len(closing) - len(closing.lstrip())] + "    "
        text = indent + "# From the old config.py; config.py.tpl doesn't have these.\n"
        for key in keys:
            key_node, value = live_entries[key]
            value_text = self.live_value(path + (key,), value, indent)
            text += f"{indent}{self.live.segment(key_node)}: {value_text},\n"
            self.only_live.append(path + (key,))
        start = self.tpl.line_start(tpl_dict.end_lineno)
        self.edits.append((start, start, text))

    def add_live_only(self, tpl_assigns, live_assigns):
        """Keep the live config.py's other statements: imports and helpers
        before the template's first setting, everything else at the end."""
        tpl_imports = {
            ast.dump(node)
            for node in self.tpl.tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        head, tail = [], []
        for node in self.live.tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if ast.dump(node) not in tpl_imports:
                    head.append(self.live.segment(node))
            elif isinstance(node, ast.Assign) and node in live_assigns.values():
                name = node.targets[0].id
                if name in tpl_assigns:
                    continue
                text = f"{name} = {self.live_value((name,), node.value, '')}"
                if name.isupper():
                    tail.append(text)
                    self.only_live.append((name,))
                else:
                    head.append(text)
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                # An earlier assignment the last one replaces.
                continue
            elif (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and node is self.live.tree.body[0]
            ):
                continue  # its docstring
            else:
                tail.append(self.live.segment(node))
                self.kept_statements += 1
        if head:
            first_setting = next(
                node
                for node in self.tpl.tree.body
                if isinstance(node, ast.Assign)
                and node in tpl_assigns.values()
                and node.targets[0].id.isupper()
            )
            previous = [
                node
                for node in self.tpl.tree.body
                if node.end_lineno < first_setting.lineno
            ]
            at = self.tpl.line_start(previous[-1].end_lineno + 1) if previous else 0
            text = "\n# From the old config.py.\n" + "\n".join(head) + "\n"
            self.edits.append((at, at, text))
        if tail:
            # In place of the template's trailing blank lines.
            start = len(self.tpl.text.rstrip("\n"))
            text = "\n\n\n# From the old config.py; config.py.tpl doesn't have these.\n"
            text += "\n".join(tail) + "\n"
            self.edits.append((start, len(self.tpl.text), text))


class Plan:
    """What the script would do, worked out without changing anything."""

    def __init__(self, repo):
        self.config_path = os.path.join(repo, "config.py")
        self.template_path = os.path.join(repo, "config.py.tpl")
        self.env_path = os.path.join(repo, "docker", ".env")
        for path in (self.config_path, self.template_path):
            if not os.path.exists(path):
                raise Failed(f"There's no {os.path.relpath(path, repo)}.")
        with open(self.config_path) as f:
            self.old_text = f.read()
        with open(self.template_path) as f:
            template = f.read()
        environment = {}
        if os.path.exists(self.env_path):
            environment = dump_for_dev.env_file(self.env_path)

        alone, self.old_settings = effective(
            self.old_text, self.config_path, environment
        )
        self.moved = {}  # path: docker/.env line
        self.overridden = []
        self.left = []  # (path, why)
        blanks = {}
        for path in SECRETS:
            value = lookup(alone, path)
            if not isinstance(value, str) or not value:
                continue
            if env_name(path) in environment:
                self.overridden.append(path)
            else:
                line = env_line(path, value)
                if line is None:
                    self.left.append((path, "it has a ' or a line break in it"))
                    continue
                self.moved[path] = line
            blanks[path] = value
        self.secret_values = list(blanks.values())

        try:
            rebuild = Rebuild(template, self.old_text, blanks)
            self.new_text = rebuild.build()
        except SyntaxError as e:
            raise Failed(f"Couldn't parse config.py or config.py.tpl: {e}") from e
        for path in blanks:
            if path not in rebuild.blanked:
                self.left.append((path, "its value isn't a plain string there"))
                self.moved.pop(path, None)
                if path in self.overridden:
                    self.overridden.remove(path)
        self.rebuild = rebuild

        new_environment = dict(environment)
        new_environment.update(dump_for_dev.parse_env("\n".join(self.moved.values())))
        _, new_settings = effective(self.new_text, self.config_path, new_environment)
        self.changed = sorted(
            key
            for key in set(self.old_settings) | set(new_settings)
            if self.old_settings.get(key, UNSET) != new_settings.get(key, UNSET)
        )

    def nothing_to_do(self):
        return not self.moved and self.new_text == self.old_text

    def hide(self, text):
        for value in self.secret_values:
            if len(value) >= 4:
                text = text.replace(value, "<secret>")
        return text

    def report(self, out=sys.stdout):
        def section(title, items):
            if items:
                print("\n" + title, file=out)
                for item in items:
                    print("  " + item, file=out)

        def looks_secret(path):
            return path in SECRETS or any(w in path[-1] for w in SECRET_WORDS)

        def short(text):
            text = " ".join(text.split())
            return text if len(text) <= 50 else text[:47] + "..."

        rebuild = self.rebuild
        section(
            "Secrets to move to docker/.env, and blank in config.py:",
            [f"{dotted(p)} -> {env_name(p)}" for p in self.moved],
        )
        section(
            "Already overridden in docker/.env, so only blanked in config.py:",
            [dotted(p) for p in self.overridden],
        )
        section(
            "Secrets left in config.py (move these by hand):",
            [f"{dotted(p)}: {why}" for p, why in self.left],
        )
        differ = [
            f"{dotted(p)}: {short(tpl)} -> {short(live)}"
            if not looks_secret(p)
            else f"{dotted(p)}: (hidden)"
            for p, tpl, live in rebuild.differ
            if p not in self.moved and p not in self.overridden
        ]
        section("Different from config.py.tpl; config.py's value is kept:", differ)
        section(
            "Only in config.py; kept:",
            [dotted(p) for p in rebuild.only_live],
        )
        section(
            "Only in config.py.tpl; left commented out, since config.py didn't"
            " set them:",
            [dotted(p) for p in rebuild.only_template],
        )
        section(
            "Might be secrets, but aren't moved; check them:",
            [
                dotted(p)
                for p in rebuild.only_live + [p for p, _, _ in rebuild.differ]
                if looks_secret(p) and p not in SECRETS
            ],
        )
        if rebuild.kept_statements:
            print(
                f"\n{rebuild.kept_statements} other statement(s) from config.py"
                " are kept, at the end.",
                file=out,
            )
        if self.changed:
            section(
                "The app would see these settings change, so nothing can be"
                " done automatically:",
                self.changed,
            )
        else:
            print(
                "\nChecked: the app sees the same settings before and after.",
                file=out,
            )

    def diff(self, out=sys.stdout):
        lines = difflib.unified_diff(
            self.hide(self.old_text).splitlines(keepends=True),
            self.hide(self.new_text).splitlines(keepends=True),
            "config.py",
            "config.py (new)",
        )
        out.writelines(lines)

    def apply(self, now=None):
        if self.changed:
            raise Failed("The app would see different settings; nothing changed.")
        now = now or datetime.datetime.now()
        backup = f"{self.config_path}.bak-{now:%Y%m%d-%H%M%S}"
        write_private(backup, self.old_text)
        if self.moved:
            existing = ""
            if os.path.exists(self.env_path):
                with open(self.env_path) as f:
                    existing = f.read()
            if existing and not existing.endswith("\n"):
                existing += "\n"
            added = (
                ("\n" if existing else "")
                + f"# Moved from config.py by scripts/move_secrets_to_env.py,"
                f" {now:%Y-%m-%d}.\n" + "\n".join(self.moved.values()) + "\n"
            )
            write_private(self.env_path, existing + added)
        write_private(self.config_path, self.new_text)
        if os.path.exists(self.env_path):
            os.chmod(self.env_path, 0o600)
        return backup


def write_private(path, text):
    """Write a file only its owner can read, replacing it in one step."""
    temporary = path + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(
        description="Move config.py's secrets to docker/.env, and line"
        " config.py up with config.py.tpl."
    )
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="show the changes to config.py (moved secrets hidden)",
    )
    args = parser.parse_args()
    try:
        plan = Plan(dump_for_dev.REPO)
        if plan.nothing_to_do():
            print("Nothing to do: config.py matches config.py.tpl, with no secrets.")
            return
        print("config.py against config.py.tpl:")
        plan.report()
        print("\nComments in config.py that config.py.tpl doesn't have are dropped.")
        if args.diff:
            print()
            plan.diff()
        if not args.apply:
            print("\nNothing changed. Run again with --apply to make these changes.")
            return
        backup = plan.apply()
    except Failed as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"""
Done. The old config.py is in {os.path.basename(backup)}; it still holds
the secrets, so delete it once the app runs with the new one. Next:

  docker/live-up.sh         # rebuild, and recreate the container
  docker/live-logs.sh       # check it started"""
    )


if __name__ == "__main__":
    main()
