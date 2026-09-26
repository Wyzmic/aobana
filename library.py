import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import paths
from utils import FILTER_COLUMNS, filtered_rows, outdated_sources

_LOCK = threading.Lock()
_STATE = {"running": False}


def _beside_db(name):
    return os.path.join(os.path.dirname(os.path.abspath(paths.subs_db())), name)


def _stop_file(kind):
    return _beside_db(f".{kind}.stop")


def _clear_stop(kind):
    try:
        os.remove(_stop_file(kind))
    except OSError:
        pass


def _run_marker():
    return _beside_db(".index_run.json")


def _unfinished():
    try:
        with open(_run_marker(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None

_STAGES = (("subs", "indexer.py"), ("epub", "epub_indexer.py"))
_SUMMARY_RE = {
    "subs": re.compile(r"Skipped (\d+) unchanged files\. Indexed (\d+) new/updated files\. "
                       r"Removed (\d+) deleted files"),
    "epub": re.compile(r"Skipped (\d+) unchanged files\. Indexed (\d+) new/updated books\. "
                       r"Removed (\d+) deleted"),
}


def _count_files(root, ext, skip_dot):
    found = other = 0
    if not root or not os.path.isdir(root):
        return None
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(ext) and not (skip_dot and f.startswith('.')):
                found += 1
            elif not f.startswith('.'):
                other += 1
    return {"files": found, "loose": 0, "other": other}


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
    subs_disk = _count_files(subs, (".srt", ".ass", ".ssa"), skip_dot=False)
    books_disk = _count_files(books, ".epub", skip_dot=True)
    listed = filtered_rows(paths.filtered_list())
    for disk, media in ((subs_disk, "subs"), (books_disk, "epub")):
        if disk:
            disk["filtered"] = sum(1 for r in listed if r["media"] == media)
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
        "subs_disk": subs_disk,
        "books_disk": books_disk,
        "subs_indexed": _indexed(db_subs, "subtitles"),
        "books_indexed": _indexed(db_epub, "epubs"),
        "subs_outdated": outdated_sources(db_subs, "subs"),
        "books_outdated": outdated_sources(db_epub, "epub"),
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


_DB_COMPANIONS = ("filtered.tsv", "analysis.json", "analysis.db")


def _db_files(folder):
    return ([n + s for n in _DB_NAMES for s in _DB_SIDECARS if os.path.isfile(os.path.join(folder, n + s))]
            + [n for n in _DB_COMPANIONS if os.path.isfile(os.path.join(folder, n))])


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
        out = {k: (list(v) if isinstance(v, list) else v) for k, v in _STATE.items()}
    if not out.get("running"):
        out["unfinished"] = _unfinished()
    return out


def stop_indexing():
    with _LOCK:
        if not _STATE.get("running"):
            return False
        _STATE["stopping"] = True
    try:
        open(_stop_file("index"), "w").close()
    except OSError:
        return False
    return True


def stop_analysis():
    with _LOCK:
        if not _ASTATE.get("running"):
            return False
        _ASTATE["stopping"] = True
    try:
        open(_stop_file("check"), "w").close()
    except OSError:
        return False
    return True


def start_indexing(only=None, outdated=False):
    stages = tuple(st for st in _STAGES if only in (None, "", "all") or st[0] == only)
    if not stages:
        return False
    with _LOCK:
        if _STATE.get("running") or _STATE.get("moving") or _ASTATE.get("running"):
            return False
        _STATE.clear()
        _STATE.update({
            "running": True, "stage": stages[0][0], "stages": [st[0] for st in stages],
            "done": 0, "total": 0, "current": "",
            "started_at": time.time(), "finished_at": None, "error": None,
            "results": {}, "skipped_clash": [], "failed": [], "ignored_other": {}, "filtered": {},
            "root_missing": [], "root_not_set": [], "log": [], "stopping": False, "stopped": False,
            "outdated": bool(outdated),
        })
    _clear_stop("index")
    try:
        with open(_run_marker(), "w", encoding="utf-8") as fh:
            json.dump({"started_at": time.time(), "stages": [st[0] for st in stages],
                       "outdated": bool(outdated)}, fh)
    except OSError:
        pass
    threading.Thread(target=_run, args=(stages, outdated), daemon=True).start()
    return True


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


def _run(stages, outdated=False):
    env = dict(os.environ, AOBANA_PROGRESS="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               AOBANA_STOP_FILE=_stop_file("index"))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        for stage, script in stages:
            _set(stage=stage, done=0, total=0, current="")
            proc = subprocess.Popen(
                [sys.executable, os.path.join(paths.BASE_DIR, script)] + (["--outdated"] if outdated else []),
                cwd=paths.BASE_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", creationflags=flags)
            for line in proc.stdout:
                _read_line(stage, line.rstrip("\r\n"))
            code = proc.wait()
            if code != 0:
                _set(error=f"{script} exited with code {code}")
                break
            if _STATE.get("stopped"):
                break
    except Exception as e:
        _set(error=f"{type(e).__name__}: {e}")
    finally:
        try:
            from engine import reset_caches
            reset_caches()
        except Exception:
            pass
        _set(running=False, stopping=False, stage="done", current="", finished_at=time.time())
        _clear_stop("index")
        try:
            os.remove(_run_marker())
        except OSError:
            pass
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
        elif line.startswith("SKIPPED_CLASH "):
            _STATE["skipped_clash"].append(line[len("SKIPPED_CLASH "):])
        elif line.startswith("IGNORED_OTHER "):
            _STATE["ignored_other"][stage] = int(line.split()[1])
        elif line.startswith("FILTERED "):
            _STATE["filtered"][stage] = int(line.split()[1])
        elif line.startswith("FAILED "):
            _STATE["failed"].append(line[len("FAILED "):])
        elif line.startswith("ROOT_MISSING "):
            _STATE["root_missing"].append(stage)
        elif line.startswith("ROOT_NOT_SET "):
            _STATE["root_not_set"].append(stage)
        elif line == "STOPPED":
            _STATE["stopped"] = True
        else:
            m = _SUMMARY_RE[stage].search(line)
            if m:
                unchanged, indexed, removed = map(int, m.groups())
                _STATE["results"][stage] = {"unchanged": unchanged, "indexed": indexed,
                                            "removed": removed}


_ASTATE = {"running": False}
FILTERABLE = ("bilingual", "other_language", "duplicate", "duplicate_kept")


def analysis_status():
    with _LOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _ASTATE.items()}


def start_analysis(only=None):
    only = only if only in ("subs", "epub") else None
    with _LOCK:
        if _ASTATE.get("running") or _STATE.get("running") or _STATE.get("moving"):
            return False
        _ASTATE.clear()
        _ASTATE.update(running=True, stage="subs" if only != "epub" else "epub", done=0, total=0,
                       current="", started_at=time.time(), finished_at=None, error=None, log=[],
                       stopping=False, stopped=False)
    _clear_stop("check")
    threading.Thread(target=_run_analysis, args=(only,), daemon=True).start()
    return True


def _run_analysis(only):
    env = dict(os.environ, AOBANA_PROGRESS="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               AOBANA_STOP_FILE=_stop_file("check"))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cmd = [sys.executable, os.path.join(paths.BASE_DIR, "analyser.py")] + (["--only", only] if only else [])
    try:
        proc = subprocess.Popen(cmd, cwd=paths.BASE_DIR, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                                creationflags=flags)
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            with _LOCK:
                if line.startswith("PROGRESS "):
                    head, _, rel = line[len("PROGRESS "):].partition(" ")
                    done, _, total = head.partition("/")
                    _ASTATE.update(done=int(done), total=int(total), current=rel)
                elif line.startswith("STAGE "):
                    _ASTATE.update(stage=line.split()[1], done=0, total=0, current="")
                elif line.startswith("TOTAL "):
                    _ASTATE["total"] = int(line.split()[1])
                elif line == "STOPPED":
                    _ASTATE["stopped"] = True
                else:
                    _ASTATE["log"].append(line)
                    del _ASTATE["log"][:-200]
        if proc.wait() != 0:
            with _LOCK:
                _ASTATE["error"] = f"analyser.py exited with code {proc.returncode}"
    except Exception as e:
        with _LOCK:
            _ASTATE["error"] = f"{type(e).__name__}: {e}"
    finally:
        with _LOCK:
            _ASTATE.update(running=False, stopping=False, current="", finished_at=time.time())
        _clear_stop("check")


def _report_path():
    return os.path.join(os.path.dirname(os.path.abspath(paths.subs_db())), "analysis.json")


def analysis_report():
    try:
        with open(_report_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_filtered(rows):
    path = paths.filtered_list()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(FILTER_COLUMNS) + "\n")
        for r in rows:
            fh.write("\t".join(str(r.get(c, "")).replace("\t", " ").replace("\n", " ")
                               for c in FILTER_COLUMNS) + "\n")
    os.replace(tmp, path)


def filtered_list():
    roots = {"subs": paths.subs_dir(), "epub": paths.books_dir()}
    out = []
    for r in filtered_rows(paths.filtered_list()):
        root = roots.get(r["media"])
        p = os.path.join(root, r["name"]) if root else ""
        if root and r["media"] == "subs" and not os.path.isfile(p):
            p = os.path.join(root, os.path.basename(r["name"]))
        out.append(dict(r, exists=bool(root) and os.path.isfile(p)))
    return out


def filter_flagged(ids):
    report = analysis_report()
    if not report:
        return "no_report", None
    with _LOCK:
        if _STATE.get("running") or _STATE.get("moving") or _ASTATE.get("running"):
            return "busy", None
        _STATE["moving"] = True
    try:
        roots = {"subs": paths.subs_dir(), "epub": paths.books_dir()}
        rows = filtered_rows(paths.filtered_list())
        have = {(r["media"], r["name"]) for r in rows}
        added, refused = [], []
        for item in report["items"]:
            if item["id"] not in ids:
                continue
            media = item["media"]
            if (item["reason"] not in FILTERABLE or not roots.get(media)
                    or not _same_folder(roots[media], report["roots"][media])):
                refused.append(item["path"])
                continue
            if (media, item["name"]) not in have:
                rows.append({"media": media, "name": item["name"], "reason": item["reason"],
                             "keep": item.get("keep", ""), "date": time.strftime("%Y-%m-%d %H:%M:%S")})
                have.add((media, item["name"]))
            item["filtered"] = True
            added.append(item["id"])
        _write_filtered(rows)
        tmp = _report_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, _report_path())
        return None, {"filtered": added, "refused": refused}
    finally:
        with _LOCK:
            _STATE.pop("moving", None)


def unfilter(entries):
    with _LOCK:
        if _STATE.get("running") or _STATE.get("moving") or _ASTATE.get("running"):
            return "busy", 0
        _STATE["moving"] = True
    try:
        drop = {(str(m), str(n)) for m, n in entries}
        rows = filtered_rows(paths.filtered_list())
        kept = [r for r in rows if (r["media"], r["name"]) not in drop]
        if len(kept) != len(rows):
            _write_filtered(kept)
        return None, len(rows) - len(kept)
    finally:
        with _LOCK:
            _STATE.pop("moving", None)


def estimate(only=None):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cmd = [sys.executable, os.path.join(paths.BASE_DIR, "analyser.py"), "--estimate"]
    if only in ("subs", "epub"):
        cmd += ["--only", only]
    try:
        out = subprocess.run(cmd, cwd=paths.BASE_DIR, capture_output=True, encoding="utf-8",
                             errors="replace", timeout=300, creationflags=flags,
                             env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
