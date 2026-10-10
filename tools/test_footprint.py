"""Optional executed-line comparison, not a statement/branch coverage percentage."""
from __future__ import annotations

import sys
from collections import defaultdict
from contextlib import contextmanager
from types import CodeType, FunctionType


@contextmanager
def executed_lines(root):
    # Local LINE events avoid tracing Unicode-table construction during imports.
    monitor = sys.monitoring
    available = [i for i in range(1, 6) if monitor.get_tool(i) is None]
    if not available:
        raise RuntimeError("No monitoring slot available for executed-line measurement")
    tool = available[0]
    prefix = str(root) + "\\" if sys.platform == "win32" else str(root) + "/"
    roots = (prefix + "cliente_xmpp", prefix + "tools")
    codes = set()
    covered = defaultdict(set)
    paths = {}

    def callback(code, line):
        covered[paths[code.co_filename]].add(line)

    def register(code):
        if code in codes or not code.co_filename.startswith(roots):
            return
        codes.add(code)
        paths[code.co_filename] = code.co_filename[len(prefix):].replace("\\", "/")
        monitor.set_local_events(tool, code, monitor.events.LINE)
        for value in code.co_consts:
            if isinstance(value, CodeType):
                register(value)

    monitor.use_tool_id(tool, "can-test-footprint")
    monitor.register_callback(tool, monitor.events.LINE, callback)
    try:
        for module in list(sys.modules.values()):
            if not module or not getattr(module, "__name__", "").startswith(
                ("cliente_xmpp.", "tools.")
            ):
                continue
            for value in list(vars(module).values()):
                if isinstance(value, FunctionType):
                    register(value.__code__)
                elif isinstance(value, type):
                    for method in vars(value).values():
                        if isinstance(method, (staticmethod, classmethod)):
                            method = method.__func__
                        if isinstance(method, FunctionType):
                            register(method.__code__)
        yield covered
    finally:
        for code in codes:
            monitor.set_local_events(tool, code, 0)
        monitor.register_callback(tool, monitor.events.LINE, None)
        monitor.free_tool_id(tool)
