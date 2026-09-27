#!/usr/bin/env python3
"""Give libuv's Windows console-resize threads a real shutdown path.

libuv starts two never-returning work items in uv__console_init() to emulate
SIGWINCH on console resize:

    uv__tty_console_resize_message_loop_thread  (GetMessage pump + WinEvent hook)
    uv__tty_console_resize_watcher_thread       (WaitForSingleObject INFINITE)

A pending work-item callback holds an LdrAddRefDll reference on the module that
contains it, so an embedder image pulling in libuv (e.g. /WHOLEARCHIVE:libnode.lib)
can never be unloaded: one LoadLibraryExW needs three FreeLibrary, and a DLL that
never unloads leaks every godot-cpp class name it holds as an "Orphan StringName"
at StringName::cleanup() time.

This patch keeps the threads (SIGWINCH-on-resize stays intact) and adds
uv__tty_console_cleanup(), which asks both callbacks to return and then waits
(bounded) until they do, so the module reference is dropped. It is invoked from
uv_library_shutdown(), which embedders already call on their shutdown path.

Only deps/uv/src/win/* and the two uv-common files are touched; the change is
Windows-only and inert on other platforms.

Fail-closed: every replacement must match exactly once. Node's v24.x branch is a
moving target, so an anchor that stops matching is reported loudly instead of
silently producing an unpatched (still-unloadable) libnode.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MARKER = "uv__tty_console_cleanup"


def fail(msg: str) -> "NoReturn":
    print(f"patch_libuv_console error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def sub_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        fail(f"{path}: expected exactly 1 occurrence, found {n} for:\n{old[:300]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def append_once(path: Path, old: str, new: str) -> None:
    """Insert `new` immediately after the single occurrence of `old`."""
    sub_once(path, old, old + new)


def patch(node_root: Path) -> None:
    tty_c = node_root / "deps/uv/src/win/tty.c"
    winapi_c = node_root / "deps/uv/src/win/winapi.c"
    winapi_h = node_root / "deps/uv/src/win/winapi.h"
    common_c = node_root / "deps/uv/src/uv-common.c"
    common_h = node_root / "deps/uv/src/uv-common.h"

    for p in (tty_c, winapi_c, winapi_h, common_c, common_h):
        if not p.is_file():
            fail(f"missing expected file: {p}")
        if MARKER in p.read_text(encoding="utf-8"):
            fail(f"{p}: already patched ({MARKER} present); refusing to patch twice")

    # --- tty.c: handles + stop event used to shut the callbacks down ---------
    append_once(
        tty_c,
        "static uv_mutex_t uv__tty_console_resize_mutex;\n",
        """
/* GodotJS-Ext: the two resize callbacks below never return, and a pending
 * work-item callback keeps a reference on this module. These let
 * uv__tty_console_cleanup() ask them to return and wait for that to happen. */
static HANDLE uv__tty_console_stop = NULL;
static HANDLE uv__tty_console_msg_thread = NULL;
static HANDLE uv__tty_console_watcher_thread = NULL;
static DWORD uv__tty_console_msg_thread_id = 0;
static HWINEVENTHOOK uv__tty_console_hook = NULL;
""",
    )

    # Only spawn the threads when the stop event exists; otherwise there is no
    # way to ask them to return.
    sub_once(
        tty_c,
        """    QueueUserWorkItem(uv__tty_console_resize_message_loop_thread,
                      NULL,
                      WT_EXECUTELONGFUNCTION);
""",
        """    uv__tty_console_stop = CreateEvent(NULL, TRUE, FALSE, NULL);
    if (uv__tty_console_stop != NULL) {
      QueueUserWorkItem(uv__tty_console_resize_message_loop_thread,
                        NULL,
                        WT_EXECUTELONGFUNCTION);
    }
""",
    )

    # --- tty.c: message-loop thread publishes a waitable handle -------------
    append_once(
        tty_c,
        """static DWORD WINAPI uv__tty_console_resize_message_loop_thread(void* param) {
  NTSTATUS status;
  ULONG_PTR conhost_pid;
  MSG msg;
""",
        """  HANDLE self;

  /* Publish a waitable handle to this thread so uv__tty_console_cleanup() can
   * wait for the callback to return. */
  self = NULL;
  DuplicateHandle(GetCurrentProcess(),
                  GetCurrentThread(),
                  GetCurrentProcess(),
                  &self,
                  0,
                  FALSE,
                  DUPLICATE_SAME_ACCESS);
  uv__tty_console_msg_thread = self;
  uv__tty_console_msg_thread_id = GetCurrentThreadId();
""",
    )

    # --- tty.c: message-loop body now waits on the stop handle too -----------
    # PostThreadMessageW() alone cannot wake a thread that never created a
    # message queue, which would leave the callback pending forever.
    sub_once(
        tty_c,
        """  if (!pSetWinEventHook(EVENT_CONSOLE_LAYOUT,
                        EVENT_CONSOLE_LAYOUT,
                        NULL,
                        uv__tty_console_resize_event,
                        (DWORD)conhost_pid,
                        0,
                        WINEVENT_OUTOFCONTEXT))
    return 0;

  while (GetMessage(&msg, NULL, 0, 0)) {
    TranslateMessage(&msg);
    DispatchMessage(&msg);
  }
  return 0;
}
""",
        """  uv__tty_console_hook = pSetWinEventHook(EVENT_CONSOLE_LAYOUT,
                                          EVENT_CONSOLE_LAYOUT,
                                          NULL,
                                          uv__tty_console_resize_event,
                                          (DWORD)conhost_pid,
                                          0,
                                          WINEVENT_OUTOFCONTEXT);
  if (uv__tty_console_hook == NULL)
    return 0;

  /* Wait on the stop handle as well as on messages: this thread may never have
   * had a message queue created, in which case PostThreadMessageW() alone
   * cannot wake it and it would never return (holding a module reference). */
  for (;;) {
    DWORD wait_result;

    wait_result = MsgWaitForMultipleObjects(1,
                                            &uv__tty_console_stop,
                                            FALSE,
                                            INFINITE,
                                            QS_ALLINPUT);
    if (wait_result == WAIT_OBJECT_0)
      break;  /* stop requested */
    if (wait_result != WAIT_OBJECT_0 + 1)
      break;  /* wait failed: do not spin */

    while (PeekMessage(&msg, NULL, 0, 0, PM_REMOVE)) {
      if (msg.message == WM_QUIT)
        goto done;
      TranslateMessage(&msg);
      DispatchMessage(&msg);
    }
  }

done:
  /* The hook callback lives in this module: drop it before returning. */
  if (uv__tty_console_hook != NULL && pUnhookWinEvent != NULL) {
    pUnhookWinEvent(uv__tty_console_hook);
    uv__tty_console_hook = NULL;
  }
  return 0;
}
""",
    )

    # --- tty.c: watcher thread publishes a handle and can be stopped --------
    append_once(
        tty_c,
        """static DWORD WINAPI uv__tty_console_resize_watcher_thread(void* param) {
""",
        """  HANDLE self;
  HANDLE waits[2];
  DWORD result;

  self = NULL;
  DuplicateHandle(GetCurrentProcess(),
                  GetCurrentThread(),
                  GetCurrentProcess(),
                  &self,
                  0,
                  FALSE,
                  DUPLICATE_SAME_ACCESS);
  uv__tty_console_watcher_thread = self;
""",
    )
    sub_once(
        tty_c,
        "    WaitForSingleObject(uv__tty_console_resized, INFINITE);\n",
        """    waits[0] = uv__tty_console_resized;
    waits[1] = uv__tty_console_stop;
    result = WaitForMultipleObjects(2, waits, FALSE, INFINITE);
    /* Anything other than "resized" means stop (or error): return so the
     * work-item reference on this module is released. */
    if (result != WAIT_OBJECT_0)
      break;
""",
    )

    # --- tty.c: the cleanup entry point -------------------------------------
    sub_once(
        tty_c,
        "void uv_tty_set_vterm_state(uv_tty_vtermstate_t state) {\n",
        """void uv__tty_console_cleanup(void) {
  HANDLE handles[2];
  DWORD count;

  count = 0;
  if (uv__tty_console_msg_thread != NULL)
    handles[count++] = uv__tty_console_msg_thread;
  if (uv__tty_console_watcher_thread != NULL)
    handles[count++] = uv__tty_console_watcher_thread;
  if (count == 0)
    return;

  /* Ask both callbacks to return: the watcher wakes on the stop event, the
   * message pump returns from GetMessage() on WM_QUIT. */
  if (uv__tty_console_stop != NULL)
    SetEvent(uv__tty_console_stop);
  if (uv__tty_console_msg_thread_id != 0 && pPostThreadMessageW != NULL)
    pPostThreadMessageW(uv__tty_console_msg_thread_id, WM_QUIT, 0, 0);

  /* The module reference is held until the callbacks actually return, so wait
   * for them (bounded) instead of assuming. */
  WaitForMultipleObjects(count, handles, TRUE, 2000);
}

void uv_tty_set_vterm_state(uv_tty_vtermstate_t state) {
""",
    )

    # --- winapi.h: declare the two extra user32 imports ---------------------
    append_once(
        winapi_h,
        """                       DWORD        idThread,
                       UINT         dwflags);
""",
        """
typedef BOOL (WINAPI *sUnhookWinEvent)
                     (HWINEVENTHOOK hWinEventHook);

typedef BOOL (WINAPI *sPostThreadMessageW)
                     (DWORD idThread,
                      UINT  Msg,
                      WPARAM wParam,
                      LPARAM lParam);
""",
    )
    append_once(
        winapi_h,
        "extern sSetWinEventHook pSetWinEventHook;\n",
        "extern sUnhookWinEvent pUnhookWinEvent;\nextern sPostThreadMessageW pPostThreadMessageW;\n",
    )

    # --- winapi.c: define + resolve them ------------------------------------
    append_once(
        winapi_c,
        "/* User32.dll function pointer */\nsSetWinEventHook pSetWinEventHook;\n",
        "sUnhookWinEvent pUnhookWinEvent;\nsPostThreadMessageW pPostThreadMessageW;\n",
    )
    append_once(
        winapi_c,
        "    sSetWinEventHook pSetWinEventHook;\n",
        "    sUnhookWinEvent pUnhookWinEvent;\n    sPostThreadMessageW pPostThreadMessageW;\n",
    )
    append_once(
        winapi_c,
        """    u.proc = GetProcAddress(user32_module, "SetWinEventHook");
    pSetWinEventHook = u.pSetWinEventHook;
""",
        """    u.proc = GetProcAddress(user32_module, "UnhookWinEvent");
    pUnhookWinEvent = u.pUnhookWinEvent;
    u.proc = GetProcAddress(user32_module, "PostThreadMessageW");
    pPostThreadMessageW = u.pPostThreadMessageW;
""",
    )

    # --- uv-common.h / uv-common.c: declare + invoke from shutdown ----------
    append_once(
        common_h,
        "void uv__threadpool_cleanup(void);\n",
        "void uv__tty_console_cleanup(void);\n",
    )
    append_once(
        common_c,
        "  uv__process_title_cleanup();\n  uv__signal_cleanup();\n",
        "#ifdef _WIN32\n  uv__tty_console_cleanup();\n#endif\n",
    )

    print(f"patched: {tty_c} (+winapi, uv-common) — {MARKER} wired into uv_library_shutdown()")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("node_root", type=Path, help="root of the fetched Node.js source tree")
    args = ap.parse_args()
    patch(args.node_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
