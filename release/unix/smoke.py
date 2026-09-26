"""Start a built Aobana the way a user does, index a tiny library through the Library tab's API,
and search it. Exit 0 only if both the subtitle and the book come back.

    python release/unix/smoke.py <command that starts Aobana> [args...]

Runs with any Python 3 (stdlib only), not the bundled one: it tests the build from outside.
Everything the app writes goes into one temporary folder (HOME, the data folder, the media).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

PORT = 5095
BASE = f"http://127.0.0.1:{PORT}"
SRT_LINE = "猫が屋根の上で昼寝をしている"
BOOK_LINE = "図書館で古い地図を見つけた。"


def make_library(root):
    subs = os.path.join(root, "media", "字幕")
    books = os.path.join(root, "media", "書籍")
    os.makedirs(os.path.join(subs, "テスト番組"))
    os.makedirs(books)
    with open(os.path.join(subs, "テスト番組", "テスト番組 01.srt"), "w", encoding="utf-8") as fh:
        fh.write(f"1\n00:00:01,000 --> 00:00:03,000\n{SRT_LINE}\n\n"
                 "2\n00:00:04,000 --> 00:00:06,000\nそろそろ帰ろう\n")
    with zipfile.ZipFile(os.path.join(books, "テスト本.epub"), "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                   "</rootfiles></container>")
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="id">smoke</dc:identifier><dc:title>テスト本</dc:title>'
                   "<dc:language>ja</dc:language></metadata><manifest>"
                   '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
                   '<spine><itemref idref="c1"/></spine></package>')
        z.writestr("OEBPS/c1.xhtml",
                   '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                   f"<head><title>一</title></head><body><p>{BOOK_LINE}</p>"
                   "<p>それは祖父のものだった。</p></body></html>")
    return subs, books


def get(path, timeout=10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path):
    req = urllib.request.Request(BASE + path, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Aobana": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_for(what, check, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = check()
            if value:
                return value
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(1)
    sys.exit(f"smoke: timed out waiting for {what}")


def main(cmd):
    root = tempfile.mkdtemp(prefix="aobana-smoke-")
    subs, books = make_library(root)
    env = dict(os.environ, HOME=os.path.join(root, "home"), AOBANA_DATA_DIR=os.path.join(root, "data"),
               SUBS_ROOT_DIR=subs, EPUB_ROOT_DIR=books, AOBANA_PORT=str(PORT), AOBANA_DEBUG="0",
               AOBANA_NO_TERMINAL="1", BROWSER="true", PYTHONIOENCODING="utf-8")
    os.makedirs(env["HOME"])
    log = open(os.path.join(root, "server.log"), "w", encoding="utf-8")
    print(f"smoke: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    ok = False
    try:
        wait_for("the server", lambda: get("/api/library"), 120)
        print("smoke: server answers", flush=True)
        post("/api/index")
        status = wait_for("indexing", lambda: (lambda s: s if not s.get("running") else None)(
            get("/api/index/status")), 600)
        if status.get("error") or status.get("failed"):
            sys.exit(f"smoke: indexing failed: {status.get('error')} {status.get('failed')}")
        print(f"smoke: indexed {json.dumps(status.get('results'), ensure_ascii=False)}", flush=True)
        q = urllib.request.quote
        subs_hits = get(f"/api/search?q={q('昼寝')}&media=subs")["results"]
        book_hits = get(f"/api/search?q={q('地図')}&media=epub")["results"]
        print(f"smoke: 昼寝 -> {len(subs_hits)} subtitle line(s), 地図 -> {len(book_hits)} book sentence(s)")
        update = wait_for("the update check", lambda: (lambda u: u if u.get("checked") else None)(
            get("/api/update")), 30)
        print(f"smoke: /api/update current {update['current']}, latest {update['latest']}")
        page = urllib.request.urlopen(BASE + "/", timeout=10).read().decode("utf-8")
        ok = bool(subs_hits) and bool(book_hits) and "露草" in page
        stray = [n for n in ("subs.db", "epub.db") if not os.path.exists(os.path.join(env["AOBANA_DATA_DIR"], n))]
        if stray:
            print(f"smoke: {stray} not in the data folder: the build does not use the installed layout")
            ok = False
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            try:
                os.killpg(proc.pid, 15)
            except OSError:
                proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if not ok:
            print("---- server log ----")
            print(open(os.path.join(root, "server.log"), encoding="utf-8", errors="replace").read())
        shutil.rmtree(root, ignore_errors=True)
    print("smoke: PASS" if ok else "smoke: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
