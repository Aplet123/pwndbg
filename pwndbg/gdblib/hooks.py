from __future__ import annotations

import pwndbg
import pwndbg.gdblib.abi
import pwndbg.gdblib.events
import pwndbg.gdblib.file
import pwndbg.gdblib.memory
import pwndbg.gdblib.next
import pwndbg.gdblib.tls
import pwndbg.gdblib.typeinfo
from pwndbg.dbg import EventType
from pwndbg.gdblib import arch_mod

# TODO: Combine these `update_*` hook callbacks into one method


@pwndbg.dbg.event_handler(EventType.NEW_MODULE)
@pwndbg.dbg.event_handler(EventType.START)
@pwndbg.dbg.event_handler(EventType.STOP)
def update_typeinfo() -> None:
    pwndbg.gdblib.typeinfo.update()


@pwndbg.dbg.event_handler(EventType.START)
@pwndbg.dbg.event_handler(EventType.STOP)
@pwndbg.dbg.event_handler(EventType.NEW_MODULE)
def update_arch() -> None:
    arch_mod.update()


@pwndbg.dbg.event_handler(EventType.NEW_MODULE)
def reset_config() -> None:
    pwndbg.gdblib.kernel._kconfig = None


@pwndbg.dbg.event_handler(EventType.START)
def on_start() -> None:
    pwndbg.gdblib.abi.update()
    pwndbg.gdblib.memory.update_min_addr()


@pwndbg.dbg.event_handler(EventType.EXIT)
def on_exit() -> None:
    pwndbg.gdblib.file.reset_remote_files()
    pwndbg.gdblib.next.clear_temp_breaks()


@pwndbg.dbg.event_handler(EventType.STOP)
def on_stop() -> None:
    pwndbg.gdblib.strings.update_length()


import pwndbg.lib.cache

pwndbg.lib.cache.connect_clear_caching_events(
    {
        # Any cache that should be cleared when the program is stopped should also be cleared
        # if the user does an operation to modify memory or registers while the program is stopped.
        # We don't do this for the other events, because they hopefully don't change memory or
        # registers
        "stop": (
            pwndbg.gdblib.events.stop,
            pwndbg.gdblib.events.mem_changed,
            pwndbg.gdblib.events.reg_changed,
        ),
        "exit": (pwndbg.gdblib.events.exit,),
        "objfile": (pwndbg.gdblib.events.new_objfile,),
        "start": (pwndbg.gdblib.events.start,),
        "cont": (
            pwndbg.gdblib.events.cont,
            pwndbg.gdblib.events.mem_changed,
            pwndbg.gdblib.events.reg_changed,
        ),
        "thread": (pwndbg.gdblib.events.thread,),
        "prompt": (pwndbg.gdblib.events.before_prompt,),
        "forever": (),
    },
    priority=pwndbg.gdblib.events.HandlerPriority.CACHE_CLEAR,
)


def init() -> None:
    """Calls all GDB hook functions that need to be called when GDB/pwndbg
    itself is loaded, as opposed to when an actual hook event occurs
    """
    update_arch()
    update_typeinfo()
    pwndbg.gdblib.abi.update()


init()
