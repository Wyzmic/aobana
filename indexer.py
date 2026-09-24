import os
import re
import sqlite3
import hashlib
from sudachipy import tokenizer, dictionary
from datetime import datetime

import paths
BASE_DIR = paths.BASE_DIR
ROOT_DIR = paths.subs_dir()
DB_PATH = paths.subs_db()
PROGRESS = os.environ.get("AOBANA_PROGRESS") == "1"

tokenizer_obj = dictionary.Dictionary(dict="core").create()
mode = tokenizer.Tokenizer.SplitMode.A

from utils import (
    KANA_RE, KANJI_CHARS, KANJI_PATTERN,
    ALPHA_CHARS, ALPHA_PATTERN, RUBY_BASE_RE, RUBY_RE,
    norm_relpath, write_tokenizer_meta, ruby_index_extras,
)

HTML_TAG_RE = re.compile(r'</?(?:i|b|u|s|font)[^>]*>', re.IGNORECASE)
SQUARE_BRACKETS_RE = re.compile(r'\[[^\[\]]*\]')
TIMECODE_RE = re.compile(r'\d{2}:\d{2}:\d{2},[^ ]+\s*-->\s*\d{2}:\d{2}:\d{2},[^ ]+')

SIMP_CHINESE_RE = re.compile(r'[这吗呢吧啦么谁们从说话样觉视频听欢买卖错让帮确对爱关门图运无计场动时发现经还进长您她啥够钟钱馆帅步舔护见恼变樱联语弃寻伫战龙击两亚丽丝态类优恶办统层头谈论戏极气为复杂转带质压雾辈适厌连隐谓认费愿拥应废东获谢闪诚责过军预给险刚脑测实迟钝虽剑临圣请哪呗尔谅创兰鲁伙开读网电车专乐业产农风飞杀药权树桥决减满卫织续损]')
PROMO_URL_RE = re.compile(r'(?:https?://|www\.|(?:\.com|\.cc|\.net|\.org)(?:\b|/))', re.IGNORECASE)
JAPANESE_CHARS_RE = re.compile(r'[\u3040-\u30FF\u4E00-\u9FFF\u3400-\u4DBF\uF900-\uFAFF\U00020000-\U000323AF\U0002F800-\U0002FA1F]')

_EXCLUDED_LOG = []
_WAKATI_LOG = []

from utils import convert_hw_katakana, SUBS_STR_REPLACEMENTS, katakana_to_hiragana

_RE_REPLACEMENTS = [
    (re.compile(r'＠ルビ.*?［(.+?)[｜|](.+?)］＠'), r'\1(\2)'),
    (re.compile(r'(?<![―—])[―—](?![―—])'), '――'),
    (re.compile(r'[・]{3,}'), '…'),
    (re.compile(r'~~~'), '～～～'),
    (re.compile(r'~~'),  '～～'),
    (re.compile(r'~'),   '～'),
]

def _postprocess_sentence(text: str) -> str:
    text = convert_hw_katakana(text)
    for src, dst in SUBS_STR_REPLACEMENTS:
        text = text.replace(src, dst)
    for pattern, dst in _RE_REPLACEMENTS:
        text = pattern.sub(dst, text)
    return close_unclosed_ruby(text)

UNCLOSED_RUBY_RE = re.compile(r'ルビ[上下右左]?［[^［］｜\n]*｜')
UNCLOSED_RUBY_PATH = paths.data_file('ruby', 'unclosed_ruby.tsv')

def _load_unclosed_ruby():
    if not os.path.exists(UNCLOSED_RUBY_PATH):
        return []
    rulings = []
    with open(UNCLOSED_RUBY_PATH, encoding='utf-8') as f:
        next(f)
        for row in f:
            tag, base, reading = row.rstrip('\n').split('\t')[:3]
            ruby = f'｜{base}({reading})'
            if not RUBY_RE.fullmatch(ruby):
                ruby = f'{base}（{reading}）'
            rulings.append((tag, ruby))
    return sorted(rulings, key=lambda r: -len(r[0]))

_UNCLOSED_RUBY = _load_unclosed_ruby()

def close_unclosed_ruby(text: str) -> str:
    if 'ルビ' not in text:
        return text
    for tag, ruby in _UNCLOSED_RUBY:
        text = text.replace(tag, ruby)
    return text

def clean_ruby_match(m) -> str:
    furi = m.group(3)
    return m.group(0)[:-len(furi) - 2] + '(' + re.sub(r'[ 　]', '', furi) + ')'

def is_paren_only_line(line):
    s = line.strip()
    if re.fullmatch(r'（[^）]+）', s): return True
    if re.fullmatch(r'\([^)]*\)', s): return True
    return False

def smart_join(lines):
    if not lines: return ''
    result = [lines[0]]
    for prev, curr in zip(lines, lines[1:]):
        last  = prev[-1] if prev else ''
        first = curr.lstrip('｜')[:1]
        if (re.match(r'[\u3040-\u30FF\u4E00-\u9FFF\w]', last)
                and re.match(r'[\u3040-\u30FF\u4E00-\u9FFF\w]', first)
                and last  not in '・―…、。！？）」'
                and first not in '・―…、。！？（「'):
            result.append(' ' + curr)
        else:
            result.append(curr)
    return ''.join(result)

def clean_line(line, relpath):
    folder = re.split(r"[\\/]", relpath)[0]
    if SIMP_CHINESE_RE.search(line):
        _EXCLUDED_LOG.append((folder, "CHINESE", line))
        return ""
    if PROMO_URL_RE.search(line) and not JAPANESE_CHARS_RE.search(line):
        _EXCLUDED_LOG.append((folder, "URL_PROMO", line))
        return ""
        
    line = HTML_TAG_RE.sub('', line)
    line = SQUARE_BRACKETS_RE.sub('', line)
    line = re.sub(r'^-\s*(?=[（\(])', '', line)
    result = []
    skip = 0
    for char in line:
        if char == '{': skip += 1
        elif char == '}':
            if skip > 0: skip -= 1
        elif skip == 0: result.append(char)
    cleaned_text = _postprocess_sentence(''.join(result).strip())
    if UNCLOSED_RUBY_RE.search(cleaned_text):
        print(f"UNCLOSED_RUBY {relpath}: {cleaned_text}".encode('cp932', 'replace').decode('cp932'))
    return cleaned_text

def is_music_only_line(line):
    s = re.sub(r'[\u202A-\u202E\u200B-\u200F\u2066-\u2069  ]', '', line)
    return bool(re.fullmatch(r'[♪♬〜～]+', s))

def is_line_number(line): return line.strip().isdigit()
def is_timecode(line): return bool(TIMECODE_RE.fullmatch(line.strip())) or bool(TIMECODE_RE.search(line))

def is_punctuation_line(line):
    s = line.strip()
    if bool(re.fullmatch(r'[-–—><\s,]+', s)): return True
    if bool(re.fullmatch(r'[-–—]+[A-Za-z\s]+[-–—]+', s)): return True
    return False

def _merge_group(group_texts):
    merged_lines = []
    paren_buffer = ""
    for text in group_texts:
        if is_paren_only_line(text):
            paren_buffer += text
        else:
            if paren_buffer:
                merged_lines.append(paren_buffer + text)
                paren_buffer = ""
            else:
                merged_lines.append(text)
    if paren_buffer:
        merged_lines.append(paren_buffer)
    return smart_join(merged_lines)

def assess_and_clean_wakati(lines, relpath=""):
    total_spaced = 0
    chunk_ratio_sum = 0.0

    for line in lines:
        spaces = line.count(' ')
        if spaces == 0:
            continue
        chars = len(line) - spaces
        total_spaced += 1
        chunk_ratio_sum += chars / (spaces + 1)

    if total_spaced >= 20:
        mean_chunk_chars = chunk_ratio_sum / total_spaced
        if mean_chunk_chars <= 3.0:
            cleaned_lines = [line.replace(' ', '') for line in lines]
            if relpath:
                _WAKATI_LOG.append((relpath, len(lines), mean_chunk_chars))
            return cleaned_lines, True

    return lines, False

def extract_lines_grouped_by_timecode(srt_content, relpath):
    lines = []
    group_texts = []
    current_timecode = None

    def flush():
        if current_timecode is not None and group_texts:
            joined = _merge_group(group_texts)
            if not is_music_only_line(joined):
                if not lines or lines[-1] != joined:
                    lines.append(joined)

    for raw_line in srt_content.splitlines():
        line = raw_line.strip()
        if not line: continue
        if is_line_number(line): continue
        if is_timecode(line):
            flush()
            current_timecode = line
            group_texts = []
            continue
        
        if is_punctuation_line(line):
            folder = re.split(r"[\\/]", relpath)[0]
            _EXCLUDED_LOG.append((folder, "SYMBOL/JUNK", line))
            continue
            
        cleaned = clean_line(line, relpath)
        if cleaned: group_texts.append(cleaned)

    flush()
    cleaned_lines, _ = assess_and_clean_wakati(lines, relpath)
    return cleaned_lines

def get_clean_text_for_mecab(line: str) -> str:
    return RUBY_RE.sub(r'\1\2', line)


def analyze_with_sudachi(clean_text: str):
    base_forms = []
    readings = []
    for word in tokenizer_obj.tokenize(clean_text, mode):
        base_forms.append(word.normalized_form())
        
        kana = word.reading_form()
        if kana:
            readings.append(katakana_to_hiragana(kana))
        else:
            readings.append(word.surface())
            
    return " ".join(base_forms), " ".join(readings)

def compute_file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def run_indexer(force=False):
    print(f"Starting indexer on {ROOT_DIR} (force={force})...")
    _EXCLUDED_LOG.clear()
    if ROOT_DIR is None:
        print("ROOT_NOT_SET subs")
        print("No subtitle folder is set (Library tab). Nothing was changed.")
        return
    if not os.path.isdir(ROOT_DIR):
        print(f"ROOT_MISSING {ROOT_DIR}")
        print("Indexing aborted: the subtitle folder does not exist. Nothing was changed.")
        return
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        
        if force:
            print("Force rebuild requested. Dropping existing tables...")
            conn.execute("DROP TABLE IF EXISTS subtitles")
            conn.execute("DROP TABLE IF EXISTS sources")
            conn.commit()
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relpath TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        try:
            cur = conn.execute("SELECT sql FROM sqlite_master WHERE name='subtitles'")
            row = cur.fetchone()
            if row and ("context" in row[0] or "file UNINDEXED" not in row[0]):
                raise sqlite3.OperationalError("Old schema detected")
        except sqlite3.OperationalError:
            print("Old database schema detected. Rebuilding FTS table...")
            conn.execute("DROP TABLE IF EXISTS subtitles")
            conn.execute("DELETE FROM sources")
            
        conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS subtitles USING fts5(
                source_id UNINDEXED,
                file UNINDEXED,
                line UNINDEXED,
                clean_text UNINDEXED,
                base_forms,
                readings,
                tokenize = "unicode61"
            )
        ''')

        cur = conn.execute("SELECT id, relpath, file_hash FROM sources")
        existing_files = {row['relpath']: {'id': row['id'], 'hash': row['file_hash']} for row in cur}
        
        current_disk_files = set()
        new_or_updated = 0
        skipped = 0

        srt_paths, loose, ignored = [], [], 0
        for dirpath, _, filenames in os.walk(ROOT_DIR):
            at_root = os.path.abspath(dirpath) == os.path.abspath(ROOT_DIR)
            for fname in filenames:
                if not fname.lower().endswith('.srt'):
                    if not fname.startswith('.'):
                        ignored += 1
                    continue
                (loose if at_root else srt_paths).append((dirpath, fname))
        for _, fname in loose:
            print(f"SKIPPED_LOOSE {fname}")
        if ignored:
            print(f"IGNORED_OTHER {ignored}")
        if PROGRESS:
            print(f"TOTAL {len(srt_paths)}", flush=True)

        for n, (dirpath, fname) in enumerate(srt_paths, 1):
            path = os.path.join(dirpath, fname)
            relpath = norm_relpath(path, ROOT_DIR)
            if PROGRESS:
                print(f"PROGRESS {n}/{len(srt_paths)} {relpath}", flush=True)
            current_disk_files.add(relpath)
                
            file_hash = compute_file_hash(path)
                
            if relpath in existing_files:
                if existing_files[relpath]['hash'] == file_hash:
                    skipped += 1
                    continue
                else:
                    source_id = existing_files[relpath]['id']
                    conn.execute("DELETE FROM subtitles WHERE source_id = ?", (source_id,))
                    conn.execute("UPDATE sources SET file_hash = ?, indexed_at = ? WHERE id = ?", 
                                 (file_hash, datetime.now(), source_id))
            else:
                cur = conn.execute("INSERT INTO sources (relpath, file_hash) VALUES (?, ?)", 
                                   (relpath, file_hash))
                source_id = cur.lastrowid
                
            new_or_updated += 1

            try:
                with open(path, encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception as e:
                print(f"Error reading {path}: {e}")
                continue

            flat_lines = extract_lines_grouped_by_timecode(content, relpath)
                
            rows_to_insert = []
            for i, line in enumerate(flat_lines):
                clean_text = get_clean_text_for_mecab(line)
                base_forms, readings = analyze_with_sudachi(clean_text)
                    
                extracted_rubies = [m[2] for m in RUBY_RE.findall(line)]
                if extracted_rubies:
                    base_forms, readings = ruby_index_extras(line, RUBY_RE, base_forms, readings)
                    line = RUBY_RE.sub(clean_ruby_match, line)
                    
                rows_to_insert.append((source_id, relpath, line, clean_text, base_forms, readings))
                
            conn.executemany(
                "INSERT INTO subtitles(source_id, file, line, clean_text, base_forms, readings) VALUES (?, ?, ?, ?, ?, ?);",
                rows_to_insert
            )
            print(f"Indexed: {relpath.encode('cp932', 'replace').decode('cp932')}")
        
        deleted_files = set(existing_files.keys()) - current_disk_files
        for relpath in deleted_files:
            source_id = existing_files[relpath]['id']
            conn.execute("DELETE FROM subtitles WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            print(f"Removed deleted file: {relpath}")

        ident = write_tokenizer_meta(conn, new_or_updated)
        if new_or_updated:
            print(f"Tokenizer: SudachiDict-core {ident['sudachidict_version']} "
                  f"({ident['dictionary_format']}), SudachiPy {ident['sudachipy_version']}, "
                  f"system.dic {ident['system_dic_sha256'][:12]}")

        conn.commit()
    
    if _EXCLUDED_LOG:
        log_path = os.path.join(paths.logs_dir(), "excluded_lines.txt")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log_f:
            for folder, reason, text in _EXCLUDED_LOG:
                log_f.write(f"[{folder}] [{reason}] {text}\n")

    if _WAKATI_LOG:
        log_path = os.path.join(paths.logs_dir(), "wakati_normalized.txt")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log_f:
            for relp, count, mean_c in _WAKATI_LOG:
                log_f.write(f"[{relp}] {count} lines normalized (mean chunk: {mean_c:.2f} chars)\n")

    print(f"Indexing complete! Skipped {skipped} unchanged files. Indexed {new_or_updated} new/updated files. Removed {len(deleted_files)} deleted files.")
    if _WAKATI_LOG:
        total_wakati_lines = sum(c for _, c, _ in _WAKATI_LOG)
        print(f"Wakati-Gaki Normalization: Encountered and normalized {len(_WAKATI_LOG)} file(s) ({total_wakati_lines} lines).")
    else:
        print("Wakati-Gaki Normalization: 0 wakati-spaced files encountered.")

if __name__ == "__main__":
    import sys
    force_rebuild = "--force" in sys.argv
    run_indexer(force=force_rebuild)