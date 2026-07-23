from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys


class _ApiLoader(importlib.abc.Loader):
    def __init__(self, wrapped_loader):
        self.wrapped_loader = wrapped_loader

    def create_module(self, spec):
        create_module = getattr(self.wrapped_loader, "create_module", None)
        return create_module(spec) if create_module else None

    def exec_module(self, module):
        self.wrapped_loader.exec_module(module)
        from .month_comments import install_month_comments

        install_month_comments(module.app)
        try:
            sys.meta_path.remove(_api_finder)
        except ValueError:
            pass


class _ApiFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "app.api":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _ApiLoader(spec.loader)
        return spec


_api_finder = _ApiFinder()
if "app.api" not in sys.modules:
    sys.meta_path.insert(0, _api_finder)

__all__ = []
