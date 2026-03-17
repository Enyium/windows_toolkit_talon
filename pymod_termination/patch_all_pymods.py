# import builtins
# import functools
# import traceback
# from collections.abc import Mapping, Sequence
# from pathlib import Path
# from types import ModuleType
# from typing import cast
# 
# import win32api
# 
# from .py_module_termination import get_pymod_termination_hook
# 
# _LOG_FILE_PATH = Path(__file__).resolve().with_suffix(".log")
# _original_import = builtins.__import__
# 
# def _log(string: str) -> None:
#     with _LOG_FILE_PATH.open("a", encoding="utf-8") as file:
#         file.write(string + "\n")
# 
# @functools.wraps(_original_import)
# def _patched_import(
#     name: str,
#     globals: Mapping[str, object] | None = None,
#     locals: Mapping[str, object] | None = None,
#     fromlist: Sequence[str] | None = (),
#     level: int = 0,
# ) -> ModuleType:
#     try:
#         if globals is not None:
#             importer_module_name = cast(str | None, globals.get("__name__"))
#             if importer_module_name is not None and importer_module_name.startswith("user."):
#                 get_pymod_termination_hook(importer_module_name)
#     except BaseException:
#         _log(traceback.format_exc().rstrip())
#         win32api.Beep(400, 50)  # Hz, ms.
# 
#     module = _original_import(name, globals, locals, fromlist, level)
#     # _log(module.__name__)
#     # _log(f'{globals["__spec__"].name}\t{globals["__name__"]}')
# 
#     return module
# 
# builtins.__import__ = _patched_import
