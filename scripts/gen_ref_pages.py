"""Generate the API-reference pages from the source tree.

Run automatically by the ``mkdocs-gen-files`` plugin at build time. It walks
``src/pitch_pilot`` and writes one ``reference/<module>.md`` page per module, each
containing a single mkdocstrings ``:::`` directive so the content is pulled *live*
from the docstrings and therefore never drifts. A ``reference/SUMMARY.md`` nav
file is emitted for ``mkdocs-literate-nav`` so new modules appear automatically.

This is the engine behind documentation self-maintenance goal #2: add a module in
a later phase and its reference page shows up with no manual edits.
"""

from __future__ import annotations

from pathlib import Path

import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()
root = Path(__file__).parent.parent
src = root / "src"

for path in sorted(src.rglob("*.py")):
    module_path = path.relative_to(src).with_suffix("")
    doc_path = path.relative_to(src).with_suffix(".md")
    full_doc_path = Path("reference", doc_path)

    parts = tuple(module_path.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
        full_doc_path = full_doc_path.with_name("index.md")
    elif parts[-1].startswith("_"):
        # Skip dunder/private modules (e.g. __main__).
        continue

    if not parts:
        continue

    nav[parts] = doc_path.as_posix()

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        identifier = ".".join(parts)
        fd.write(f"::: {identifier}\n")

    mkdocs_gen_files.set_edit_path(full_doc_path, path.relative_to(root))

with mkdocs_gen_files.open("reference/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
