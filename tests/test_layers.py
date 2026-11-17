"""
The layout rules for fbc/ (docs/development.md, "How fbc/ is laid out"):

- Packages are layers, lowest first. A module imports only from its own layer
  or lower, whether the import is at the top of the file or inside a function.
- Modules don't import each other in a circle when they load.
- models/ may also import the two leaf modules that import nothing from fbc
  when they load.
- No loose modules in fbc/, and package __init__.py files are empty.

The few imports that go the wrong way are named in ALLOWED, each with why, so
a new one fails here and has to be argued for. #164 is about removing them.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fbc"

# Lowest first. ebay and discogs sit side by side: ebay.drafts reads a
# release through discogs.client, and discogs.listing builds on ebay.describe,
# but no module in one imports a module of the other in a circle.
LAYERS = {
    "core": 0,
    "models": 1,
    "ebay": 2,
    "discogs": 2,
    "pictures": 2,
    "sync": 3,
    "sales": 4,
    "views": 5,
}

# Modules that import nothing from fbc but core when they load, so models can
# import them at the top.
LEAVES = {"fbc.discogs.naming", "fbc.ebay.tracking"}

# (importing module, imported module): why it goes up a layer. All are
# imports inside a function, which is what lets them work.
ALLOWED = {
    # A model's list column that runs a query, whose code imports the models.
    ("fbc.models.ebay", "fbc.ebay.mystery"): "EbayListing.fmt_boxes_available",
    ("fbc.models.inventory", "fbc.sales.finance"): "PurchaseLot.figures",
}


def _module_name(path):
    parts = list(path.relative_to(ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _modules():
    return {_module_name(p): p for p in sorted(ROOT.rglob("*.py"))}


def _layer(module):
    parts = module.split(".")
    return LAYERS.get(parts[1]) if len(parts) > 1 else None


def _imports(path, module_names):
    """[(imported module, at the top of the file?)] for every fbc import."""
    tree = ast.parse(path.read_text())
    top = {id(node) for node in tree.body}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            named = [f"{node.module}.{a.name}" for a in node.names]
            # `from fbc.sync import state` imports the module fbc.sync.state,
            # not the package.
            submodules = [t for t in named if t in module_names]
            targets = submodules or [node.module]
        else:
            continue
        for target in targets:
            if target in module_names and target != "fbc":
                found.append((target, id(node) in top))
    return found


def test_each_module_imports_only_its_own_layer_or_lower():
    modules = _modules()
    wrong = []
    used = set()
    for module, path in modules.items():
        mine = _layer(module)
        if mine is None:
            continue
        for target, _ in _imports(path, modules):
            theirs = _layer(target)
            if theirs is None or theirs <= mine:
                continue
            if module.startswith("fbc.models.") and target in LEAVES:
                continue
            if (module, target) in ALLOWED:
                used.add((module, target))
                continue
            wrong.append(f"{module} imports {target}")

    assert sorted(set(wrong)) == []
    assert set(ALLOWED) - used == set(), "an ALLOWED import is no longer needed"


def test_the_allowed_imports_are_inside_functions():
    modules = _modules()
    for module, target in ALLOWED:
        at_the_top = [
            t for t, top in _imports(modules[module], modules) if t == target and top
        ]
        assert at_the_top == [], f"{module} imports {target} when it loads"


def test_modules_dont_import_each_other_in_a_circle_when_they_load():
    modules = _modules()
    graph = {
        module: sorted({t for t, top in _imports(path, modules) if top})
        for module, path in modules.items()
    }
    # An import of a module's own package (fbc.models) or of fbc itself is
    # how a module reaches its siblings, not a dependency on them.
    packages = {m for m, p in modules.items() if p.name == "__init__.py"}
    for module in graph:
        graph[module] = [t for t in graph[module] if t not in packages]

    done, active = set(), []

    def visit(module):
        if module in active:
            cycle = active[active.index(module) :] + [module]
            raise AssertionError("import circle: " + " -> ".join(cycle))
        if module in done:
            return
        active.append(module)
        for target in graph[module]:
            visit(target)
        active.pop()
        done.add(module)

    for module in graph:
        visit(module)


def test_the_leaf_modules_import_only_core_when_they_load():
    modules = _modules()
    for leaf in LEAVES:
        loads = [t for t, top in _imports(modules[leaf], modules) if top]
        assert [t for t in loads if _layer(t) != 0] == [], leaf


def test_there_are_no_loose_modules_in_fbc():
    loose = sorted(p.name for p in ROOT.glob("*.py") if p.name != "__init__.py")
    assert loose == []


def test_package_init_files_are_empty():
    filled = [
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("__init__.py")
        if p.parent != ROOT and p.read_text().strip()
    ]
    assert filled == []
