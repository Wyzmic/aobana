import os
import sys
import re
import sqlite3
import threading
import time
import uuid

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

from flask import Flask, render_template, make_response, request, jsonify, g, abort
from engine import get_search_results, format_episode_title, format_book_title, get_formatted_title, get_ruby_lexicon
import paths
import library
import folder_picker
import updater
from utils import outdated_sources

app = Flask(__name__, template_folder='.', static_folder='static')
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 365 * 24 * 3600


def _asset_version():
    import hashlib
    h = hashlib.sha1()
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    for d, _, names in sorted(os.walk(root)):
        for n in sorted(names):
            st = os.stat(os.path.join(d, n))
            h.update(f"{n}:{st.st_size}:{st.st_mtime_ns};".encode())
    return h.hexdigest()[:10]


ASSET_V = _asset_version()


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("aobana.svg")

BOOT_ID = os.environ.setdefault("AOBANA_BOOT_ID", uuid.uuid4().hex)

VERSION = "1.2"
RELEASES_URL = "https://github.com/Wyzmic/aobana/releases/latest"
RELEASES_API = "https://api.github.com/repos/Wyzmic/aobana/releases"
LATEST_API = f"{RELEASES_API}/latest"
RELEASES_TAG_URL = "https://github.com/Wyzmic/aobana/releases/tag/v"
update_info = {"checked": False, "latest": None, "release": None, "page_waiting": 0.0}
TERMUX = os.environ.get("AOBANA_TERMUX") == "1"
SELF_UPDATE = TERMUX and os.environ.get("AOBANA_UPDATER") == "1"
TERMUX_INSTALL = "curl -fsSL https://raw.githubusercontent.com/Wyzmic/aobana/main/termux/install.sh | bash"


def version_tuple(v):
    return tuple(int(n) for n in re.findall(r"\d+", v or ""))


def check_for_update():
    try:
        import json
        import urllib.request
        req = urllib.request.Request(LATEST_API, headers={
            "Accept": "application/vnd.github+json", "User-Agent": f"Aobana/{VERSION}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            release = json.load(resp)
        tag = str(release.get("tag_name") or "")
        update_info["latest"] = tag.lstrip("vV") or None
        update_info["release"] = release
    except Exception:
        pass
    finally:
        update_info["checked"] = True


threading.Thread(target=check_for_update, daemon=True).start()

threading.Thread(target=get_ruby_lexicon, daemon=True).start()
from engine import warm_media_library
threading.Thread(target=warm_media_library, daemon=True).start()

def get_db():
    if 'db_subs' not in g:
        subs_path = paths.subs_db()
        if os.path.exists(subs_path):
            g.db_subs = sqlite3.connect(f"file:{subs_path}?mode=ro", uri=True)
            g.db_subs.row_factory = sqlite3.Row
            g.db_subs.execute("PRAGMA mmap_size = 2147483648;")
        else:
            g.db_subs = None

    if 'db_epub' not in g:
        epub_path = paths.epub_db()
        if os.path.exists(epub_path):
            g.db_epub = sqlite3.connect(f"file:{epub_path}?mode=ro", uri=True)
            g.db_epub.row_factory = sqlite3.Row
            g.db_epub.execute("PRAGMA mmap_size = 536870912;")
        else:
            g.db_epub = None

    return g.db_subs, g.db_epub

@app.teardown_appcontext
def close_db(error):
    db_subs = g.pop('db_subs', None)
    if db_subs is not None:
        db_subs.close()
    db_epub = g.pop('db_epub', None)
    if db_epub is not None:
        db_epub.close()

active_queries = {}
queries_lock = threading.Lock()

@app.route("/", methods=["GET"])
def index():
    q = request.args.get("q", "")
    sort = request.args.get("sort", "recommended")
    exact = request.args.get("exact", "")
    media = request.args.get("media", "all")
    resp = make_response(render_template("index.html", q=q, sort=sort, exact=exact, media=media,
                                         boot=BOOT_ID, asset_v=ASSET_V, version=VERSION,
                                         handoff=library.load_profile_handoff(PORT),
                                         handoff_pending=library.handoff_pending_from(PORT)))
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/api/search", methods=["GET"])
def api_search():
    token = request.args.get("client", "")
    client_key = f"client:{token}" if token else f"ip:{request.remote_addr}"
    q = request.args.get("q", "")
    sort = request.args.get("sort", "recommended")
    folder = request.args.get("folder", "")
    exact = request.args.get("exact") == "on"
    limit = request.args.get("limit", 500, type=int)
    offset = request.args.get("offset", 0, type=int)
    file_param = request.args.get("file", "")
    media = request.args.get("media", "all")
    seed = request.args.get("seed", type=int)

    if not folder:
        folder = None
        
    db_subs, db_epub = get_db()
    abort_flag = [False]
    search = (q, sort, seed if sort == "random" else None, media, exact, folder, file_param)
    mine = (search, abort_flag, (db_subs, db_epub))

    with queries_lock:
        running = active_queries.get(client_key, [])
        for other_search, other_flag, other_conns in running:
            if other_search == search:
                continue
            other_flag[0] = True
            for conn in other_conns:
                if conn is not None:
                    try:
                        conn.interrupt()
                    except Exception:
                        pass
        active_queries[client_key] = [r for r in running if r[0] == search] + [mine]

    try:
        results, folder_counts, global_total, all_folders, has_more = get_search_results(
            db_subs, q, sort=sort, folder=folder, exact=exact, 
            abort_flag=abort_flag, limit=limit, offset=offset, file=file_param,
            db_epub=db_epub, media=media, seed=seed
        )
        if abort_flag[0]:
            return jsonify({"aborted": True}), 499
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            return jsonify({"aborted": True}), 499
        raise
    finally:
        with queries_lock:
            left = [r for r in active_queries.get(client_key, []) if r is not mine]
            if left:
                active_queries[client_key] = left
            else:
                active_queries.pop(client_key, None)
    
    return jsonify({
        "results": results,
        "folder_counts": folder_counts,
        "global_total": global_total,
        "all_folders": all_folders,
        "has_more": has_more
    })

@app.route("/api/search/progress", methods=["GET"])
def api_search_progress():
    from engine import search_progress
    db_subs, db_epub = get_db()
    return jsonify(search_progress(
        db_subs, db_epub, request.args.get("q", ""),
        sort=request.args.get("sort", "recommended"), seed=request.args.get("seed", type=int),
        media=request.args.get("media", "all"), exact=request.args.get("exact") == "on",
        folder=request.args.get("folder") or None, file=request.args.get("file") or None))

@app.route("/api/episodes", methods=["GET"])
def api_episodes():
    folder = request.args.get("folder", "")
    media = request.args.get("media", "all")
    if not folder:
        return jsonify({"files": []})
        
    db_subs, db_epub = get_db()
    files_data = []
    
    if media in ("all", "subs") and db_subs is not None:
        folder_prefix = folder + "/"
        folder_prefix_win = folder + "\\"
        try:
            cur = db_subs.execute('''
                SELECT relpath FROM sources 
                WHERE relpath LIKE ? OR relpath LIKE ?
                ORDER BY relpath ASC
            ''', (f"{folder_prefix}%", f"{folder_prefix_win}%"))
            for row in cur:
                relpath = row["relpath"]
                title = get_formatted_title(db_subs, relpath)
                files_data.append({
                    "file": relpath,
                    "title": title
                })
        except Exception:
            pass

    if not files_data and media in ("all", "epub") and db_epub is not None:
        pattern = folder + "\\" + "%"
        pattern_fwd = folder + "/" + "%"
        try:
            cur_epub = db_epub.execute('''
                SELECT DISTINCT file FROM epubs 
                WHERE file LIKE ? OR file LIKE ?
                ORDER BY file ASC
            ''', (pattern, pattern_fwd))
            for row in cur_epub:
                file_key = row["file"]
                title = format_book_title(file_key, db_epub=db_epub)
                files_data.append({
                    "file": file_key,
                    "title": title
                })
        except Exception:
            pass

    if files_data and any('話' in x['title'] for x in files_data):
        files_data.sort(key=lambda x: (
            1 if '話' in x['title'] else (0 if re.search(r'(?:Movie|Film|劇場版|映画|劇場|\[映\])', x['file'], re.IGNORECASE) else 1),
            x['file']
        ))
        
    return jsonify({"files": files_data})

@app.route("/api/context", methods=["GET"])
def api_context():
    rowid = request.args.get("rowid", type=int)
    file = request.args.get("file", "")
    q = request.args.get("q", "")
    media = request.args.get("media", "").strip().lower()
    
    db_subs, db_epub = get_db()
    if rowid is None:
        return jsonify({"error": "Missing rowid"}), 400

    if not file:
        if media == "epub" and db_epub is not None:
            r = db_epub.execute("SELECT file FROM epubs WHERE rowid = ?", (rowid,)).fetchone()
            if r:
                file = r["file"]
        elif media == "subs" and db_subs is not None:
            r = db_subs.execute("SELECT file FROM subtitles WHERE rowid = ?", (rowid,)).fetchone()
            if r:
                file = r["file"]
        else:
            if db_subs is not None:
                r = db_subs.execute("SELECT file FROM subtitles WHERE rowid = ?", (rowid,)).fetchone()
                if r:
                    file = r["file"]
                    media = "subs"
            if not file and db_epub is not None:
                r = db_epub.execute("SELECT file FROM epubs WHERE rowid = ?", (rowid,)).fetchone()
                if r:
                    file = r["file"]
                    media = "epub"

    if not file:
        return jsonify({"error": "Missing file or unknown rowid"}), 400

    def window(name, default):
        n = request.args.get(name, type=int)
        return default if n is None else max(0, min(10, n))
        
    rows = []
    
    is_epub = False
    if media == "epub":
        is_epub = True
    elif media == "subs":
        is_epub = False
    elif db_epub is not None:
        try:
            chk = db_epub.execute("SELECT line FROM epubs WHERE file = ? LIMIT 1", (file,)).fetchone()
            if chk:
                is_epub = True
        except Exception:
            pass

    if is_epub and db_epub is not None:
        try:
            cur = db_epub.execute("""
                SELECT rowid, line FROM epubs 
                WHERE rowid BETWEEN ? AND ? AND file = ? 
                  AND source_id = (SELECT source_id FROM epubs WHERE rowid = ?)
                ORDER BY rowid ASC
            """, (rowid - window("before", 3), rowid + window("after", 3), file, rowid))
            rows = cur.fetchall()
        except Exception:
            pass

    if not is_epub and db_subs is not None:
        query = """
            SELECT rowid, line FROM subtitles 
            WHERE rowid BETWEEN ? AND ? AND file = ? 
              AND source_id = (SELECT source_id FROM subtitles WHERE rowid = ?)
            ORDER BY rowid ASC
        """
        cur = db_subs.execute(query, (rowid - window("before", 4), rowid + window("after", 4), file, rowid))
        rows = cur.fetchall()
    
    from engine import highlight_and_furigana, analyze_query, split_negated_terms, work_key
    
    pos_q, _ = split_negated_terms(q)
    clean_q = pos_q.strip('""“”')
    content_bases, sql_bases, readings, base_groups = analyze_query(clean_q)
    
    context_html_lines = []
    for r in rows:
        line = r['line']
        hl_context = highlight_and_furigana(line, content_bases, clean_q, mark=True, bold=False, base_groups=base_groups, readings=readings, work=work_key("epub" if is_epub else "subs", file))
        context_html_lines.append(hl_context)
        
    html = '<div class="spacer"></div>'.join(context_html_lines)
    match = next((i for i, r in enumerate(rows) if r['rowid'] == rowid), None)
    text = None
    db = db_epub if is_epub else db_subs
    if match is not None and db is not None:
        r = db.execute(f"SELECT clean_text FROM {'epubs' if is_epub else 'subtitles'} WHERE rowid = ?",
                       (rowid,)).fetchone()
        text = r["clean_text"] if r else None
    return jsonify({"context": html, "lines": context_html_lines, "match": match, "text": text})

@app.route("/api/locate", methods=["GET"])
def api_locate():
    texts = [t.replace('\xa0', ' ').strip() for t in request.args.getlist("text") if t.strip()]
    media_req = request.args.get("media", "").strip().lower()
    if not texts:
        return jsonify({"error": "Missing text"}), 400
    db_subs, db_epub = get_db()
    if db_subs is None and db_epub is None:
        return jsonify({"rows": []})

    from engine import get_tagger, get_formatted_title, format_book_title
    contains = " AND ".join(["instr(replace(clean_text, char(160), ' '), ?) > 0"] * len(texts))
    tokenizer_obj, mode = get_tagger()
    forms = []
    for text in texts:
        for word in tokenizer_obj.tokenize(text, mode):
            form = word.normalized_form()
            if form and any(ch.isalnum() for ch in form) and form not in forms:
                forms.append(form)
    forms = sorted(forms, key=len, reverse=True)[:3]
    match = " AND ".join('base_forms:"%s"' % f.replace('"', '""') for f in forms) if forms else ""

    def query_corpus(db, table, is_book):
        if db is None:
            return []
        res = []
        if match:
            try:
                res = db.execute(
                    f"SELECT rowid, file, line, clean_text FROM {table} WHERE {table} MATCH ? AND "
                    + contains + " ORDER BY length(clean_text) LIMIT 500", (match, *texts)).fetchall()
            except sqlite3.OperationalError:
                res = []
        if not res:
            res = db.execute(
                f"SELECT rowid, file, line, clean_text FROM {table} WHERE " + contains
                + " ORDER BY length(clean_text) LIMIT 500",
                tuple(texts)).fetchall()
        out = []
        for r in res:
            title = format_book_title(r["file"], db) if is_book else get_formatted_title(db, r["file"])
            out.append({
                "rowid": r["rowid"],
                "file": r["file"],
                "line": r["line"],
                "clean_text": r["clean_text"],
                "title": title,
                "media_type": "epub" if is_book else "subs"
            })
        return out

    rows = []
    if media_req != "epub" and db_subs is not None:
        rows.extend(query_corpus(db_subs, "subtitles", False))
    if media_req != "subs" and db_epub is not None:
        rows.extend(query_corpus(db_epub, "epubs", True))

    rows.sort(key=lambda r: len(r.get("clean_text") or ""))
    return jsonify({"rows": rows[:500]})

@app.route("/api/relocate", methods=["POST"])
def api_relocate():
    _require_page()
    items = (request.get_json(silent=True) or {}).get("items", [])[:5000]
    db_subs, db_epub = get_db()
    from engine import get_tagger
    tokenizer_obj, mode = get_tagger()
    ruby = re.compile(r'｜?([^()\s　（）]+)[（(][^()（）]*[)）]')
    out = []
    for it in items:
        is_book = it.get("media_type") == "epub"
        db, table = (db_epub, "epubs") if is_book else (db_subs, "subtitles")
        line, rowid, file = it.get("line") or "", it.get("rowid"), it.get("file") or ""
        if db is None or not line or rowid is None:
            out.append(None)
            continue
        r = db.execute(f"SELECT file, line FROM {table} WHERE rowid = ?", (rowid,)).fetchone()
        if r and r["line"] == line and r["file"] == file:
            out.append(None)
            continue
        text = ruby.sub(r'\1', line).replace('｜', '').strip()
        forms = []
        for word in tokenizer_obj.tokenize(text, mode):
            form = word.normalized_form()
            if form and any(ch.isalnum() for ch in form) and form not in forms:
                forms.append(form)
        forms = sorted(forms, key=len, reverse=True)[:3]
        rows = []
        if forms:
            try:
                rows = db.execute(
                    f"SELECT rowid, file, line FROM {table} WHERE {table} MATCH ? LIMIT 2000",
                    (" AND ".join('base_forms:"%s"' % f.replace('"', '""') for f in forms),)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        work = lambda f: f.split("\\")[0] if is_book else f.replace("\\", "/").rsplit("/", 1)[0]
        same = [x for x in rows if x["line"] == line]
        best = (min(same, key=lambda x: (work(x["file"]) != work(file), abs(x["rowid"] - rowid))) if same else None)
        out.append({"rowid": best["rowid"], "file": best["file"]} if best else None)
    return jsonify({"items": out})


def _require_page():
    if request.headers.get("X-Aobana") != "1":
        abort(403)


@app.route("/api/media", methods=["GET"])
def api_media():
    from engine import get_media_library, media_library_status, media_page
    db_subs, db_epub = get_db()
    status = media_library_status(db_subs, db_epub)
    if not status["ready"]:
        return jsonify(status), 202
    folder = request.args.get("folder")
    page = media_page(get_media_library(db_subs, db_epub),
                      media=request.args.get("media", "all"),
                      needle=request.args.get("q", ""),
                      offset=max(0, request.args.get("offset", 0, type=int)),
                      limit=max(0, request.args.get("limit", 0, type=int)),
                      folder=folder)
    page["ready"] = True
    return jsonify(page)


@app.route("/api/library", methods=["GET"])
def api_library():
    db_subs, db_epub = get_db()
    return jsonify({**library.describe(db_subs, db_epub), "folder_picker": folder_picker.available()})


@app.route("/api/library/outdated", methods=["GET"])
def api_library_outdated():
    db_subs, db_epub = get_db()
    return jsonify({"subs_outdated": outdated_sources(db_subs, "subs"),
                    "books_outdated": outdated_sources(db_epub, "epub")})


@app.route("/api/library", methods=["POST"])
def api_library_set():
    _require_page()
    body = request.get_json(silent=True) or {}
    if "db_dir" in body:
        error, old_kept = library.move_databases(body.get("db_dir"))
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"ok": True, "old_kept": old_kept})
    if "port" in body:
        error = library.set_port(body.get("port"))
        if not error and not os.environ.get("AOBANA_PORT"):
            library.save_profile_handoff(int(str(body["port"]).strip()), PORT, body.get("profile"))
    else:
        error = library.set_folders(body.get("subs_dir"), body.get("books_dir"))
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True})


@app.route("/api/profile-handoff", methods=["POST"])
def api_profile_handoff():
    _require_page()
    body = request.get_json(silent=True) or {}
    if "items" in body:
        library.refresh_profile_handoff(PORT, body["items"])
    elif "applied" in body:
        library.drop_profile_handoff(PORT, body["applied"])
    elif body.get("drop"):
        library.drop_profile_handoff(PORT)
    return jsonify({"ok": True})


@app.route("/api/library/open", methods=["POST"])
def api_library_open():
    _require_page()
    which = (request.get_json(silent=True) or {}).get("which")
    error = library.open_folder(which)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True})


@app.route("/api/library/pick", methods=["POST"])
def api_library_pick():
    _require_page()
    body = request.get_json(silent=True) or {}
    start = {"subs": paths.subs_dir, "books": paths.books_dir, "data": paths.db_dir}.get(body.get("which"))
    try:
        start = start() if start else None
    except Exception:
        start = None
    status, path = folder_picker.pick(start, str(body.get("title") or "")[:200])
    return jsonify({"status": status, "path": path})


@app.route("/api/library/analyse", methods=["POST"])
def api_library_analyse():
    _require_page()
    only = (request.get_json(silent=True) or {}).get("only")
    started = library.start_analysis(only)
    return jsonify({"started": started, **library.analysis_status()})


@app.route("/api/library/analyse/stop", methods=["POST"])
def api_library_analyse_stop():
    _require_page()
    return jsonify({"stopping": library.stop_analysis(), **library.analysis_status()})


@app.route("/api/library/analysis", methods=["GET"])
def api_library_analysis():
    return jsonify({"status": library.analysis_status(), "report": library.analysis_report(),
                    "filtered": library.filtered_list()})


@app.route("/api/library/filter", methods=["POST"])
def api_library_filter():
    _require_page()
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    try:
        ids = {int(i) for i in ids}
    except (TypeError, ValueError):
        return jsonify({"error": "bad_ids"}), 400
    error, result = library.filter_flagged(ids)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True, **result})


@app.route("/api/library/unfilter", methods=["POST"])
def api_library_unfilter():
    _require_page()
    entries = (request.get_json(silent=True) or {}).get("entries") or []
    try:
        entries = [(str(e["media"]), str(e["name"])) for e in entries]
    except (TypeError, KeyError):
        return jsonify({"error": "bad_entries"}), 400
    error, n = library.unfilter(entries)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True, "unfiltered": n})


@app.route("/api/library/estimate", methods=["GET"])
def api_library_estimate():
    return jsonify(library.estimate(request.args.get("only")))


@app.route("/api/index", methods=["POST"])
def api_index_start():
    _require_page()
    body = request.get_json(silent=True) or {}
    only = body.get("only")
    started = library.start_indexing(only if only in ("subs", "epub") else None, bool(body.get("outdated")))
    return jsonify({"started": started, **library.index_status()})


@app.route("/api/index/stop", methods=["POST"])
def api_index_stop():
    _require_page()
    return jsonify({"stopping": library.stop_indexing(), **library.index_status()})


@app.route("/api/update", methods=["GET"])
def api_update():
    latest = update_info["latest"]
    newer = bool(latest) and version_tuple(latest) > version_tuple(VERSION)
    if request.args.get("waiting"):
        update_info["page_waiting"] = time.time()
    return jsonify({"checked": update_info["checked"], "current": VERSION, "latest": latest,
                    "newer": newer, "url": RELEASES_URL, "termux": TERMUX,
                    "self_update": SELF_UPDATE, "install_cmd": TERMUX_INSTALL,
                    "auto": bool(newer and updater.pick_asset(update_info["release"])),
                    "page_waiting": time.time() - update_info["page_waiting"] < 8})


UPDATE_EXIT_CODE = 75


@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    _require_page()
    if not SELF_UPDATE:
        lang = str((request.get_json(silent=True) or {}).get("lang") or "en")
        error = updater.start(update_info["release"], lang, VERSION,
                              lambda: threading.Timer(1.0, os._exit, args=(0,)).start())
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"ok": True})
    threading.Timer(0.5, os._exit, args=(UPDATE_EXIT_CODE,)).start()
    return jsonify({"ok": True})


@app.route("/api/update/status", methods=["GET"])
def api_update_status():
    return jsonify(updater.status)


release_notes = {}


def notable_changes(body):
    m = re.search(r"^###\s+Notable Changes\s*$(.*?)(?=^##|\Z)", body or "", re.M | re.S)
    if not m:
        return []
    return [line[2:].strip() for line in m.group(1).splitlines() if line.startswith("- ")]


@app.route("/api/release-notes", methods=["GET"])
def api_release_notes():
    if VERSION not in release_notes:
        release = update_info["release"]
        if not (release and str(release.get("tag_name") or "").lstrip("vV") == VERSION):
            release = None
            try:
                import json
                import urllib.request
                req = urllib.request.Request(f"{RELEASES_API}/tags/v{VERSION}", headers={
                    "Accept": "application/vnd.github+json", "User-Agent": f"Aobana/{VERSION}"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    release = json.load(resp)
            except Exception:
                pass
        if release:
            release_notes[VERSION] = {"notable": notable_changes(release.get("body")),
                                      "url": release.get("html_url") or RELEASES_URL}
    notes = release_notes.get(VERSION) or {"notable": [], "url": f"{RELEASES_TAG_URL}{VERSION}"}
    return jsonify({"version": VERSION, **notes})


@app.route("/api/index/status", methods=["GET"])
def api_index_status():
    return jsonify(library.index_status())


DEBUG = paths.debug_mode()
PORT = paths.server_port()
app.config['TEMPLATES_AUTO_RELOAD'] = DEBUG

if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW("露草 / Aobana")
            except Exception:
                pass
        print(f"露草 / Aobana - http://127.0.0.1:{PORT}/")
        print("Termux を閉じるとサーバーが止まります。 / Close Termux to stop the server." if TERMUX else
              "このウィンドウを閉じるとサーバーが止まります。 / Close this window to stop the server.")
    paths.make_source_folders()
    app.run(host='127.0.0.1', port=PORT, debug=DEBUG)