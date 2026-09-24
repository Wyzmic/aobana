import os
import re
import shutil
import subprocess
import sys
import threading
import time

import paths

_LOCK = threading.Lock()
_STATE = {"running": False}

_STAGES = (("subs", "indexer.py"), ("epub", "epub_indexer.py"))
_SUMMARY_RE = {
    "subs": re.compile(r"Skipped (\d+) unchanged files\. Indexed (\d+) new/updated files\. "
                       r"Removed (\d+) deleted files"),
    "epub": re.compile(r"Skipped (\d+) unchanged files\. Indexed (\d+) new/updated books\. "
                       r"Removed (\d+) deleted"),
}


def _count_files(root, ext, need_subfolder, skip_dot):
    found = loose = other = 0
    if not root or not os.path.isdir(root):
        return None
    for dirpath, _, files in os.walk(root):
        at_root = os.path.abspath(dirpath) == os.path.abspath(root)
        for f in files:
            if f.lower().endswith(ext) and not (skip_dot and f.startswith('.')):
                if need_subfolder and at_root:
                    loose += 1
                else:
                    found += 1
            elif not f.startswith('.'):
                other += 1
    return {"files": found, "loose": loose, "other": other}


def _indexed(conn, table):
    if conn is None:
        return {"files": 0, "rows": 0}
    try:
        return {"files": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                "rows": conn.execute(f"SELECT COUNT(rowid) FROM {table}").fetchone()[0]}
    except Exception:
        return {"files": 0, "rows": 0}


def describe(db_subs, db_epub):
    subs, books = paths.subs_dir(), paths.books_dir()
    return {
        "installed": paths.INSTALLED,
        "subs_dir": subs,
        "books_dir": books,
        "data_dir": paths.db_dir(),
        "db_default": paths.STORE_DIR,
        "db_is_default": _same_folder(paths.db_dir(), paths.STORE_DIR),
        "db_sizes": _db_sizes(paths.db_dir()),
        "port": paths.server_port(),
        "port_env": bool(os.environ.get("AOBANA_PORT")),
        "subs_disk": _count_files(subs, ".srt", need_subfolder=True, skip_dot=False),
        "books_disk": _count_files(books, ".epub", need_subfolder=False, skip_dot=True),
        "subs_indexed": _indexed(db_subs, "subtitles"),
        "books_indexed": _indexed(db_epub, "epubs"),
        "index": index_status(),
    }


def set_folders(subs_dir, books_dir):
    if _STATE.get("running"):
        return "busy"
    cfg = paths.load_config()
    for key, value in (("subs_dir", subs_dir), ("books_dir", books_dir)):
        if value is None:
            continue
        value = os.path.abspath(os.path.expanduser(str(value).strip().strip('"')))
        if not os.path.isdir(value):
            return f"not_found:{key}"
        cfg[key] = value
    paths.save_config(cfg)
    return None


_DB_NAMES = ("subs.db", "epub.db")
_DB_SIDECARS = ("", "-wal", "-shm", "-journal")


def _db_files(folder):
    return [n + s for n in _DB_NAMES for s in _DB_SIDECARS if os.path.isfile(os.path.join(folder, n + s))]


def _db_sizes(folder):
    return {n: (os.path.getsize(os.path.join(folder, n)) if os.path.isfile(os.path.join(folder, n)) else None)
            for n in _DB_NAMES}


def _same_folder(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def move_databases(target):
    with _LOCK:
        if _STATE.get("running") or _STATE.get("moving"):
            return "busy", []
        _STATE["moving"] = True
    try:
        return _move_databases(target)
    finally:
        with _LOCK:
            _STATE.pop("moving", None)


def _move_databases(target):
    raw = str(target or "").strip().strip('"')
    default = raw in ("", "default")
    dest = paths.STORE_DIR if default else os.path.abspath(os.path.expanduser(raw))
    src = paths.db_dir()
    if not os.path.isdir(dest):
        return "not_found", []
    if _same_folder(src, dest):
        return "same", []
    if any(os.path.exists(os.path.join(dest, n)) for n in _DB_NAMES):
        return "exists", []
    files = _db_files(src)
    probe = os.path.join(dest, ".aobana-write-test")
    try:
        with open(probe, "w"):
            pass
        os.remove(probe)
    except OSError:
        return "not_writable", []

    renamed, copied = [], []
    try:
        for name in files:
            s, d = os.path.join(src, name), os.path.join(dest, name)
            try:
                os.replace(s, d)
                renamed.append(name)
                continue
            except OSError:
                pass
            tmp = d + ".moving"
            shutil.copy2(s, tmp)
            if os.path.getsize(tmp) != os.path.getsize(s):
                raise OSError(f"size mismatch copying {name}")
            os.replace(tmp, d)
            copied.append(name)
        cfg = paths.load_config()
        if default:
            cfg.pop("db_dir", None)
        else:
            cfg["db_dir"] = dest
        paths.save_config(cfg)
    except OSError:
        for name in renamed:
            try:
                os.replace(os.path.join(dest, name), os.path.join(src, name))
            except OSError:
                pass
        for name in copied:
            try:
                os.remove(os.path.join(dest, name))
            except OSError:
                pass
        for name in files:
            try:
                os.remove(os.path.join(dest, name + ".moving"))
            except OSError:
                pass
        return "failed", []

    old_kept = []
    if copied:
        for name in copied:
            path = os.path.join(src, name)
            for attempt in range(10):
                try:
                    os.remove(path)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.3)
            else:
                old_kept.append(path)
    _after_db_change()
    return None, old_kept


def _after_db_change():
    try:
        from engine import reset_caches, warm_media_library
        reset_caches()
        threading.Thread(target=warm_media_library, daemon=True).start()
    except Exception:
        pass


def set_port(value):
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return "bad_port"
    if not 1024 <= port <= 65535:
        return "bad_port"
    cfg = paths.load_config()
    cfg["port"] = port
    paths.save_config(cfg)
    return None


def open_folder(which):
    target = {"subs": paths.subs_dir, "books": paths.books_dir, "data": paths.db_dir}.get(which)
    if target is None:
        return "unknown"
    path = target()
    if not path or not os.path.isdir(path):
        return "not_found"
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            opener = "termux-open" if os.environ.get("TERMUX_VERSION") else "xdg-open"
            subprocess.Popen([opener, path])
    except Exception as e:
        return f"failed:{e}"
    return None


def index_status():
    with _LOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _STATE.items()}


def start_indexing():
    with _LOCK:
        if _STATE.get("running") or _STATE.get("moving"):
            return False
        _STATE.clear()
        _STATE.update({
            "running": True, "stage": "subs", "done": 0, "total": 0, "current": "",
            "started_at": time.time(), "finished_at": None, "error": None,
            "results": {}, "skipped_loose": [], "failed": [], "ignored_other": {},
            "root_missing": [], "root_not_set": [], "log": [],
        })
    threading.Thread(target=_run, daemon=True).start()
    return True


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


def _run():
    env = dict(os.environ, AOBANA_PROGRESS="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        for stage, script in _STAGES:
            _set(stage=stage, done=0, total=0, current="")
            proc = subprocess.Popen(
                [sys.executable, os.path.join(paths.BASE_DIR, script)],
                cwd=paths.BASE_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", creationflags=flags)
            for line in proc.stdout:
                _read_line(stage, line.rstrip("\r\n"))
            code = proc.wait()
            if code != 0:
                _set(error=f"{script} exited with code {code}")
                break
    except Exception as e:
        _set(error=f"{type(e).__name__}: {e}")
    finally:
        try:
            from engine import reset_caches
            reset_caches()
        except Exception:
            pass
        _set(running=False, stage="done", current="", finished_at=time.time())
        try:
            from engine import warm_media_library
            threading.Thread(target=warm_media_library, daemon=True).start()
        except Exception:
            pass


def _read_line(stage, line):
    with _LOCK:
        log = _STATE["log"]
        if not line.startswith("PROGRESS "):
            log.append(line)
            del log[:-200]
        if line.startswith("TOTAL "):
            _STATE["total"] = int(line.split()[1])
        elif line.startswith("PROGRESS "):
            head, _, rel = line[len("PROGRESS "):].partition(" ")
            done, _, total = head.partition("/")
            _STATE.update(done=int(done), total=int(total), current=rel)
        elif line.startswith("SKIPPED_LOOSE "):
            _STATE["skipped_loose"].append(line[len("SKIPPED_LOOSE "):])
        elif line.startswith("IGNORED_OTHER "):
            _STATE["ignored_other"][stage] = int(line.split()[1])
        elif line.startswith("FAILED "):
            _STATE["failed"].append(line[len("FAILED "):])
        elif line.startswith("ROOT_MISSING "):
            _STATE["root_missing"].append(stage)
        elif line.startswith("ROOT_NOT_SET "):
            _STATE["root_not_set"].append(stage)
        else:
            m = _SUMMARY_RE[stage].search(line)
            if m:
                unchanged, indexed, removed = map(int, m.groups())
                _STATE["results"][stage] = {"unchanged": unchanged, "indexed": indexed,
                                            "removed": removed}
