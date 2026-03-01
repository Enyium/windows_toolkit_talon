# import sys
# import time
# import threading
# import weakref
# 
# from .pymod_termination import get_pymod_termination_hook
# 
# _module_dict_id = id(sys.modules[__name__].__dict__)
# 
# print("══════════════════════════════════════════════════════════════════════")
# # print("██████████████████████████████████████████████████████████████████████")
# print(f"Start of module with `__dict__` at 0x{_module_dict_id:X}.")
# 
# _pymod_termination_hook = get_pymod_termination_hook()
# _must_exit_thread = threading.Event()
# 
# 
# class Foo:
#     def __init__(self):
#         _pymod_termination_hook.on_module_finalize(self.on_pymod_finalize)
#         _pymod_termination_hook.on_globals_teardown(self.on_globals_teardown)
# 
#     def on_pymod_finalize(self):
#         print(f"In callback: Finalizing module object with `__dict__` at 0x{_module_dict_id:X}.")
#         _must_exit_thread.set()
# 
#     def on_globals_teardown(self):
#         print(f"In callback: Tearing down module with `__dict__` at 0x{_module_dict_id:X}.")
# 
# 
# print(f"Created `Foo` at 0x{id(Foo):X}.")
# weakref.finalize(Foo, print, f"Finalized `Foo` at 0x{id(Foo):X}.")
# # weakref.finalize(Foo.__init__, print, f"Finalized `Foo.__init__` at 0x{id(Foo.__init__):X}.")
# 
# _foo = Foo()
# 
# def _run_thread():
#     print(f"Start of thread in module with `__dict__` at 0x{_module_dict_id:X}.")
#     with _pymod_termination_hook.globals_teardown_deferrer:
#         while True:
#             if _must_exit_thread.is_set():
#                 break
#             time.sleep(2)
#     print(f"End of thread in module with `__dict__` at 0x{_module_dict_id:X}.")
# threading.Thread(target=_run_thread).start()
# threading.Thread(target=_run_thread).start()
# 
# print(f"End of module with `__dict__` at 0x{_module_dict_id:X}.")
