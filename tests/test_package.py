"""Package-level surface: the version and the names promised by ``__all__``."""

from __future__ import annotations

import importlib.metadata

import politeclient


def test_version_matches_the_installed_metadata():
    assert politeclient.__version__ == importlib.metadata.version("politeclient")


def test_every_name_in_all_is_importable():
    for name in politeclient.__all__:
        assert hasattr(politeclient, name), name
