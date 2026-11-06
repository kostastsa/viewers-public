#!/usr/bin/env python3
"""pyview runner: runs cells in one shared namespace.

The server starts this file with the interpreter of the user's project:

    python runner.py --fd N --file /path/to/notebook.py

Commands arrive as JSON lines on stdin. Events leave as JSON lines on file descriptor N,
a private pipe, so that anything the cells print (even from C code or child processes)
can never be mistaken for a message. Standard library only; it works with Python 3.8+.
"""
import ast
import base64
import builtins
import io
import json
import linecache
import os
import signal
import struct
import sys
import threading
import time
import traceback
import warnings


def main():
    fd = int(sys.argv[sys.argv.index("--fd") + 1])
    path = sys.argv[sys.argv.index("--file") + 1]
    events = os.fdopen(fd, "w", buffering=1, encoding="utf-8")
    commands = sys.stdin
    out_lock = threading.Lock()

    def emit(ev):
        with out_lock:
            events.write(json.dumps(ev) + "\n")
            events.flush()

    # ----- streams: what the cells print becomes "stream" events, in order -----
    buf, buf_lock = [], threading.Lock()

    def flush_streams():
        with buf_lock:
            items, buf[:] = list(buf), []
        merged = []
        for name, text in items:
            if merged and merged[-1][0] == name:
                merged[-1][1] += text
            else:
                merged.append([name, text])
        for name, text in merged:
            emit({"ev": "stream", "name": name, "text": text})

    class Stream(io.TextIOBase):
        def __init__(self, name):
            self.name = name

        def writable(self):
            return True

        def write(self, s):
            if not isinstance(s, str):
                s = str(s)
            with buf_lock:
                buf.append((self.name, s))
                now = s.endswith("\n") or sum(len(t) for _, t in buf) > 8192
            if now:  # like a terminal: a finished line goes out at once, so child processes keep their order
                flush_streams()
            return len(s)

        def flush(self):
            flush_streams()

        def isatty(self):
            return False

    def flusher():
        while True:
            time.sleep(0.05)
            if buf:
                flush_streams()

    threading.Thread(target=flusher, daemon=True).start()

    # ----- the namespace the cells share -----
    here = os.path.dirname(os.path.abspath(path))
    os.chdir(here)
    sys.path.insert(0, here)
    sys.argv = [path]
    signal.signal(signal.SIGINT, signal.default_int_handler)  # the server may have started us with it ignored
    warnings.filterwarnings("ignore", message=".*non-interactive.*")  # plt.show() under the Agg backend

    def no_input(*args, **kwargs):
        raise RuntimeError("input() is not available in pyview: there is no keyboard to answer it.")

    builtins.input = no_input
    sys.stdin = io.StringIO("")
    sys.stdout, sys.stderr = Stream("stdout"), Stream("stderr")
    ns = {"__name__": "__main__", "__file__": path, "__builtins__": builtins, "__doc__": None}
    state = {"pandas_set": False}

    hidden_files = {os.path.abspath(__file__), os.path.abspath(ast.__file__)}

    def fmt_error(e):
        te = traceback.TracebackException.from_exception(e)
        seen = []
        while te is not None and te not in seen:  # also the exceptions this one was raised from
            seen.append(te)
            te.stack = traceback.StackSummary.from_list(
                [f for f in te.stack if os.path.abspath(f.filename) not in hidden_files])
            te = te.__cause__ or te.__context__
        return "".join(seen[0].format())

    def emit_error(e):
        flush_streams()
        emit({"ev": "error", "ename": type(e).__name__, "evalue": str(e), "text": fmt_error(e)})

    def show(val):
        if val is None:
            return
        cls = type(val)
        if cls.__module__.startswith(("matplotlib.figure", "matplotlib.axes")):
            return  # the figure itself is captured when the cell ends
        try:
            text = repr(val)
        except BaseException as e:  # a broken __repr__ must not break the cell
            text = "<repr failed: %s>" % e
        item = {"ev": "result", "text": text}
        if not isinstance(val, type):
            fn = getattr(val, "_repr_html_", None)
            if callable(fn):
                try:
                    html = fn()
                    if isinstance(html, str) and html:
                        item["html"] = html
                except BaseException:
                    pass
        flush_streams()
        emit(item)

    def capture_figures():
        plt = sys.modules.get("matplotlib.pyplot")
        if plt is None:
            return
        try:
            for num in plt.get_fignums():
                fig = plt.figure(num)
                png = io.BytesIO()
                fig.savefig(png, format="png", dpi=200, bbox_inches="tight")
                data = png.getvalue()
                w, h = struct.unpack(">II", data[16:24])
                flush_streams()
                emit({"ev": "image", "data": base64.b64encode(data).decode("ascii"),
                      "w": w // 2, "h": h // 2, "text": repr(fig)})
            plt.close("all")
        except BaseException as e:
            emit_error(e)

    def tune_pandas():
        pd = sys.modules.get("pandas")
        if pd is not None and not state["pandas_set"]:
            state["pandas_set"] = True
            try:
                pd.set_option("display.width", 200)
                pd.set_option("display.max_columns", 30)
            except Exception:
                pass

    def run(msg):
        src, name = msg["source"], msg["name"]
        linecache.cache[name] = (len(src), None, src.splitlines(True), name)
        ok = True
        t0 = time.time()
        try:
            tree = ast.parse(src, name, "exec")
            last = None
            if tree.body and isinstance(tree.body[-1], ast.Expr) and not src.rstrip().endswith(";"):
                last = ast.Expression(tree.body.pop().value)
            exec(compile(tree, name, "exec"), ns)
            tune_pandas()
            if last is not None:
                show(eval(compile(last, name, "eval"), ns))
        except BaseException as e:  # KeyboardInterrupt (Interrupt button) and SystemExit too
            ok = False
            emit_error(e)
        finally:
            tune_pandas()
            flush_streams()
            capture_figures()
        emit({"ev": "done", "id": msg["id"], "ok": ok, "ms": int((time.time() - t0) * 1000)})

    emit({"ev": "ready", "python": sys.version.split()[0], "executable": sys.executable})
    while True:
        try:
            line = commands.readline()
        except KeyboardInterrupt:  # an Interrupt that arrived between two cells
            continue
        if not line:
            break  # the server closed the pipe, or died
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("op") == "run":
            try:
                run(msg)
            except KeyboardInterrupt:  # an Interrupt that arrived while a cell was ending
                emit({"ev": "done", "id": msg["id"], "ok": False, "ms": 0})
        elif msg.get("op") == "exit":
            break


if __name__ == "__main__":
    main()
