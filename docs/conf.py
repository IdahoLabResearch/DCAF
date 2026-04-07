import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "DCAF"
project_copyright = "2025, Batelle Energy Alliance"
author = "Statisical Simulation & Optimization Dept."
release = "0.0.0"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
]

source_suffix = [".md"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

autodoc_typehints = "description"
autodoc_class_signature = "separated"
autodoc_preserve_defaults = True
napoleon_numpy_docstring = True
always_document_param_types = True
autodoc_member_order = "bysource"

html_theme = "sphinx_book_theme"
html_static_path = ["_static"]

html_theme_options = {
    "show_navbar_depth": 1,
    "show_toc_level": 2,
}
