import os
import sys
import re
import sqlite3
import threading
import uuid

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

from flask import Flask, render_template, make_response, request, jsonify, g, abort
from engine import get_search_results, format_episode_title, format_book_title, get_formatted_title, get_ruby_lexicon
import paths
import library

app = Flask(__name__, template_folder='.', static_folder='static')

BOOT_ID = os.environ.setdefault("AOBANA_BOOT_ID", uuid.uuid4().hex)

VERSION = "1.0"
RELEASES_URL = "https://github.com/Wyzmic/aobana/releases/latest"
LATEST_API = "https://api.github.com/repos/Wyzmic/aobana/releases/latest"
update_info = {"checked": False, "latest": None}


def version_tuple(v):
    return tuple(int(n) for n in re.findall(r"\d+", v or ""))


def check_for_update():
    try:
        import json
        import urllib.request
        req = urllib.request.Request(LATEST_API, headers={
            "Accept": "application/vnd.github+json", "User-Agent": f"Aobana/{VERSION}"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            tag = str(json.load(resp).get("tag_name") or "")
        update_info["latest"] = tag.lstrip("vV") or None
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
active_flags = {}
queries_lock = threading.Lock()

@app.route("/", methods=["GET"])
def index():
    q = request.args.get("q", "")
    sort = request.args.get("sort", "recommended")
    exact = request.args.get("exact", "")
    media = request.args.get("media", "all")
    resp = make_response(render_template("index.html", q=q, sort=sort, exact=exact, media=media,
                                         boot=BOOT_ID))
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
    
    with queries_lock:
        if client_key in active_flags:
            active_flags[client_key][0] = True
            
        if client_key in active_queries:
            for conn in active_queries[client_key]:
                if conn is not None:
                    try:
                        conn.interrupt()
                    except Exception:
                        pass
                
        active_flags[client_key] = abort_flag
        active_queries[client_key] = [db_subs, db_epub]
        
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
            if active_queries.get(client_key) == [db_subs, db_epub]:
                del active_queries[client_key]
            if active_flags.get(client_key) == abort_flag:
                del active_flags[client_key]
    
    return jsonify({
        "results": results,
        "folder_counts": folder_counts,
        "global_total": global_total,
        "all_folders": all_folders,
        "has_more": has_more
    })

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


def _require_page():
    if request.headers.get("X-Aobana") != "1":
        abort(403)


@app.route("/api/media", methods=["GET"])
def api_media():
    from engine import get_media_library
    db_subs, db_epub = get_db()
    media = request.args.get("media", "all")
    items = [it for it in get_media_library(db_subs, db_epub)
             if media == "all" or it["media"] == media]
    return jsonify({"items": items})


@app.route("/api/library", methods=["GET"])
def api_library():
    db_subs, db_epub = get_db()
    return jsonify(library.describe(db_subs, db_epub))


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
    else:
        error = library.set_folders(body.get("subs_dir"), body.get("books_dir"))
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True})


@app.route("/api/library/open", methods=["POST"])
def api_library_open():
    _require_page()
    which = (request.get_json(silent=True) or {}).get("which")
    error = library.open_folder(which)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True})


@app.route("/api/index", methods=["POST"])
def api_index_start():
    _require_page()
    started = library.start_indexing()
    return jsonify({"started": started, **library.index_status()})


@app.route("/api/update", methods=["GET"])
def api_update():
    latest = update_info["latest"]
    newer = bool(latest) and version_tuple(latest) > version_tuple(VERSION)
    return jsonify({"checked": update_info["checked"], "current": VERSION, "latest": latest,
                    "newer": newer, "url": RELEASES_URL})


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
        print("Close this window to stop the server.")
    app.run(host='127.0.0.1', port=PORT, debug=DEBUG)