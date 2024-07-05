from __future__ import annotations

import ctypes
import sys
import threading
from typing import Any
from xmlrpc.server import SimpleXMLRPCRequestHandler
from xmlrpc.server import SimpleXMLRPCServer

import binaryninja

host = "127.0.0.1"
port = 31337

logger = binaryninja.log.Logger(0, "pwndbg-integration")


class CustomLogHandler(SimpleXMLRPCRequestHandler):
    def log_message(self, format: str, *args: Any):
        logger.log_debug(format % args)


# get the earliest-starting function that contains a given address
def get_widest_func(bv: binaryninja.BinaryView, addr: int) -> binaryninja.Function | None:
    funcs = bv.get_functions_containing(addr)
    if len(funcs) == 0:
        return None
    return min(funcs, key=lambda f: f.start)


# workaround for BinaryView.add_tag not supporting auto tags
def add_auto_tag(bv: binaryninja.BinaryView, addr: int, name: str, desc: str) -> None:
    tag = binaryninja.core.BNCreateTag(bv.get_tag_type(name).handle, desc)
    binaryninja.core.BNAddTag(bv.handle, tag, False)
    binaryninja.core.BNAddAutoDataTag(bv.handle, addr, tag)


# workaround for there to be no way to get all address tags in the python API
def get_tag_refs(bv: binaryninja.BinaryView, ty: str) -> list[binaryninja.core.BNTagReference]:
    tag_type = bv.get_tag_type(ty)
    count = binaryninja.core.BNGetAllTagReferencesOfTypeCount(bv.handle, tag_type.handle)
    ref_ptr = binaryninja.core.BNGetAllTagReferencesOfType(
        bv.handle, tag_type.handle, ctypes.c_ulong(count)
    )
    return [ref_ptr[i] for i in range(count)]


def remove_tag_ref(bv: binaryninja.BinaryView, ref: binaryninja.core.BNTagReference):
    binaryninja.core.BNRemoveTagReference(bv.handle, ref)


def count_pointers(ty: Any) -> tuple[str, int]:
    derefcnt = 0
    while isinstance(ty, binaryninja.types.PointerType):
        ty = ty.target
        derefcnt += 1
    return (str(ty), derefcnt)


to_register = []


def should_register(f):
    to_register.append(f.__name__)
    return f


class ServerHandler:
    bv: binaryninja.BinaryView

    def __init__(self, bv: binaryninja.BinaryView):
        self.bv = bv

    # initialize a binaryview if not already initialized, e.g. add a tag type
    def init(self) -> None:
        tag_types = {"pwndbg-pc": "➡️", "pwndbg-bp": "🔴"}
        for k, v in tag_types.items():
            if k not in self.bv.tag_types:
                self.bv.create_tag_type(k, v)

    @should_register
    def clear_pc_tag(self) -> None:
        for t in get_tag_refs(self.bv, "pwndbg-pc"):
            remove_tag_ref(self.bv, t)

    @should_register
    def navigate_to(self, addr: int) -> None:
        self.bv.navigate(self.bv.view, addr)

    @should_register
    def update_pc_tag(self, new_pc: int) -> None:
        self.clear_pc_tag()
        add_auto_tag(self.bv, new_pc, "pwndbg-pc", "current pc")

    @should_register
    def get_bp_tags(self) -> list[int]:
        return [t.addr for t in get_tag_refs(self.bv, "pwndbg-bp")]

    @should_register
    def get_symbol(self, addr: int) -> str | None:
        sym = self.bv.get_symbol_at(addr)
        if sym is None:
            return None
        return sym.full_name

    @should_register
    def get_func_info(self, addr: int) -> tuple[str, int] | None:
        func = get_widest_func(self.bv, addr)
        if func is None:
            return None
        return (func.symbol.full_name, func.start)

    @should_register
    def get_data_info(self, addr: int) -> tuple[str, int] | None:
        dv = self.bv.get_data_var_at(addr)
        if dv is None:
            return None
        if dv.symbol is not None:
            return (dv.symbol.full_name, dv.address)
        return (dv.name or f"data_{dv.address:x}", dv.address)

    @should_register
    def get_comments(self, addr: int) -> list[str]:
        ret = []
        for f in sorted(self.bv.get_functions_containing(addr), key=lambda f: f.start):
            ret += f.get_comment_at(addr).split("\n")
        ret += self.bv.get_comment_at(addr).split("\n")
        # remove empty lines and prepend double slash
        return ["// " + x for x in ret if x]

    @should_register
    def decompile_func(self, addr: int) -> list[tuple[int, list[tuple[str, str]]]] | None:
        func = get_widest_func(self.bv, addr)
        if func is None:
            return None
        func = func.hlil_if_available
        if func is None:
            return None
        ret = []
        for line in func.root.lines:
            ret.append((line.address, [(tok.text, tok.type.name) for tok in line.tokens]))
        return ret

    @should_register
    def get_func_type(
        self, addr: int
    ) -> tuple[tuple[str, int, str], list[tuple[str, int, str]]] | None:
        f = self.bv.get_function_at(addr)
        if f is None:
            return None
        ret_ty = (*count_pointers(f.return_type), f.name)
        arg_tys = [(*count_pointers(arg.type), arg.name) for arg in f.parameter_vars]
        return (ret_ty, arg_tys)

    @should_register
    def get_base(self) -> int:
        return self.bv.start

    @should_register
    def get_py_version(self) -> str:
        return sys.version

    @should_register
    def get_version(self) -> str:
        return binaryninja.core_version()


server: SimpleXMLRPCServer | None = None
handler: ServerHandler | None = None


# TODO: enable switching with this
def start_server(bv: binaryninja.BinaryView) -> None:
    global server

    handler = ServerHandler(bv)
    handler.init()

    if server is not None:
        return

    server = SimpleXMLRPCServer((host, port), requestHandler=CustomLogHandler, allow_none=True)
    server.register_introspection_functions()

    for f in to_register:
        server.register_function(getattr(handler, f))

    # TODO: change server logging so it doesn't get counted as errors
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    logger.log_info(f"XML-RPC server listening on http://{host}:{port}")


def stop_server(bv: binaryninja.BinaryView) -> None:
    global server

    if server is None:
        return

    server.shutdown()
    server.server_close()
    server = None


def toggle_breakpoint(bv: binaryninja.BinaryView, addr: int) -> None:
    found = False
    for t in get_tag_refs(bv, "pwndbg-bp"):
        if t.addr == addr:
            remove_tag_ref(bv, t)
            found = True
    if not found:
        add_auto_tag(bv, addr, "pwndbg-bp", "GDB breakpoint")


binaryninja.plugin.PluginCommand.register(
    "pwndbg\\Start integration on current view",
    "Start pwndbg integration on current view.",
    start_server,
)
binaryninja.plugin.PluginCommand.register(
    "pwndbg\\Stop integration", "Stop pwndbg integration.", stop_server
)
binaryninja.plugin.PluginCommand.register_for_address(
    "pwndbg\\Toggle breakpoint here",
    "Toggles a GDB breakpoint at the current address.",
    toggle_breakpoint,
)
