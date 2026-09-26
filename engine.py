import os
import sqlite3
import re
import math
import functools

import random
import threading
from array import array
from collections import OrderedDict
thread_local = threading.local()

GLOBAL_FOLDER_COUNTS = {}
GLOBAL_DB_TOTALS = {}


def db_fingerprint(*conns):
    out = []
    for conn in conns:
        if conn is None:
            out.append(None)
            continue
        try:
            path = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
        except sqlite3.Error:
            path = ""
        entry = [path]
        for p in (path, path + "-wal"):
            try:
                st = os.stat(p)
                entry.append((st.st_mtime_ns, st.st_size))
            except (OSError, ValueError):
                entry.append(None)
        out.append(tuple(entry))
    return tuple(out)


def get_db_total(db_subs, db_epub, media='all'):
    global GLOBAL_DB_TOTALS
    key = (media, db_fingerprint(db_subs, db_epub))
    if key in GLOBAL_DB_TOTALS:
        return GLOBAL_DB_TOTALS[key]
    lib = _media_library_cached(key[1])
    if lib is not None:
        total = sum(it["lines"] for it in lib if media in ('all', it["media"]))
        GLOBAL_DB_TOTALS[key] = total
        return total

    total = 0
    if media in ('all', 'subs') and db_subs is not None:
        try:
            total += db_subs.execute("SELECT COUNT(rowid) FROM subtitles").fetchone()[0]
        except Exception:
            pass
    if media in ('all', 'epub') and db_epub is not None:
        try:
            total += db_epub.execute("SELECT COUNT(rowid) FROM epubs").fetchone()[0]
        except Exception:
            pass

    GLOBAL_DB_TOTALS[key] = total
    return total

def get_tagger():
    if not hasattr(thread_local, 'tokenizer_obj'):
        from sudachipy import tokenizer, dictionary
        thread_local.dict_obj = dictionary.Dictionary(dict="core")
        thread_local.tokenizer_obj = thread_local.dict_obj.create()
        thread_local.mode = tokenizer.Tokenizer.SplitMode.A
    return thread_local.tokenizer_obj, thread_local.mode

from utils import (
    KANA_RE, KANJI_CHARS, KANJI_PATTERN,
    ALPHA_CHARS, ALPHA_PATTERN, RUBY_BASE_RE, RUBY_RE, ruby_re_for,
    SPACED_RUBY_PREV_RE, build_ruby_lexicon, ruby_reading, split_spaced_ruby,
    load_ruby_decisions, load_ruby_merges, load_ruby_trims, ruby_merge_key,
    GlossRuby,
)

_RUBY_LEXICON = None
_RUBY_LEXICON_LOCK = threading.Lock()
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
import paths
_DB_PATHS = (('subs', paths.subs_db, 'subtitles'), ('epub', paths.epub_db, 'epubs'))


def work_key(media: str, file: str) -> tuple:
    return (media, file.split('\\', 1)[0])


def get_ruby_lexicon():
    global _RUBY_LEXICON
    with _RUBY_LEXICON_LOCK:
        if _RUBY_LEXICON is None:
            rows = []
            for media, db_path, table in _DB_PATHS:
                path = db_path()
                if not os.path.exists(path):
                    continue
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                try:
                    rows += [(work_key(media, f), line) for f, line in conn.execute(
                        f"SELECT file, line FROM {table} WHERE instr(line, ?) > 0", ('(',))]
                finally:
                    conn.close()
            _RUBY_LEXICON = build_ruby_lexicon(rows)
    return _RUBY_LEXICON


_SUDACHI_READINGS = {}

def _sudachi_reading(word: str) -> str:
    if word not in _SUDACHI_READINGS:
        from sudachipy import tokenizer
        tokenizer_obj, _ = get_tagger()
        _SUDACHI_READINGS[word] = katakana_to_hiragana("".join(
            t.reading_form() for t in tokenizer_obj.tokenize(word, tokenizer.Tokenizer.SplitMode.C)))
    return _SUDACHI_READINGS[word]


def ruby_evidence(work):
    by_work, corpus = get_ruby_lexicon()

    def evidence(word):
        own = by_work.get((work, word), set())
        for r in sorted(own):
            yield r, 0
        for r in sorted(corpus.get(word, set()) - own):
            yield r, 1
        s = _sudachi_reading(word)
        if s and s != word:
            yield s, 2
    return evidence


RUBY_DECISIONS_PATH = paths.data_file('ruby', 'ruby_decisions.tsv')
RUBY_MERGES_PATH = paths.data_file('ruby', 'ruby_dict_merge.tsv')
RUBY_WHOLE_PATH = paths.data_file('ruby', 'ruby_whole.tsv')
RUBY_TRIM_PATH = paths.data_file('ruby', 'ruby_trim.tsv')
GLOSS_RUBY_PATH = paths.data_file('ruby', 'gloss_ruby.tsv')
GLOSS_NAMES_PATH = paths.data_file('ruby', 'gloss_names.tsv')

_RUBY_DECISIONS = None


def ruby_decisions() -> dict:
    global _RUBY_DECISIONS
    if _RUBY_DECISIONS is None:
        _RUBY_DECISIONS = {**load_ruby_decisions(GLOSS_NAMES_PATH), **load_ruby_decisions(RUBY_DECISIONS_PATH)}
    return _RUBY_DECISIONS

def spaced_ruby_split(prev, base, furi, work):
    decided = ruby_decisions()
    key = (prev, base, furi)
    if key in decided:
        return decided[key]
    return split_spaced_ruby(prev, base, furi, ruby_evidence(work))


def iter_spaced_ruby_candidates():
    for media, db_path, table in _DB_PATHS:
        path = db_path()
        if not os.path.exists(path):
            continue
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(f"SELECT rowid, file, line FROM {table} WHERE instr(line, ?) > 0 OR instr(line, ?) > 0",
                                ('(', '（' if media == 'epub' else '(')).fetchall()
        finally:
            conn.close()
        for rowid, f, line in rows:
            work, last = work_key(media, f), 0
            for m in display_ruby_re(media).finditer(line):
                gap, last = line[last:m.start()], m.end()
                pm = None if m.group(1) else SPACED_RUBY_PREV_RE.search(gap)
                if pm:
                    prev, base, furi = pm.group(1), m.group(2), ruby_reading(m)
                    yield (media, rowid, work, prev, base, furi, spaced_ruby_split(prev, base, furi, work))

HTML_ESCAPE_MAP = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;'}

_DISPLAY_PUNCT_RE = re.compile(r'[ 　。…―～！？”“!?]')
_DISPLAY_NONWORD_RE = re.compile(r'[^\w、\.,]')

def calculate_display_length(line: str, media: str = 'subs') -> int:
    if not line: return 0
    processed = display_ruby_re(media).sub(r'\1\2', line)
    processed = _DISPLAY_PUNCT_RE.sub('', processed)
    processed = _DISPLAY_NONWORD_RE.sub('', processed)
    return len(processed)

def detect_script(query):
    has_kanji = bool(re.search(rf'[{KANJI_CHARS}]', query))
    has_hiragana = bool(re.search(r'[\u3040-\u309F]', query))
    has_katakana = bool(re.search(r'[\u30A0-\u30FF]', query))
    
    if has_kanji: return 'kanji'
    if has_katakana and not has_hiragana: return 'katakana'
    if has_hiragana: return 'hiragana'
    return 'romaji'

from utils import katakana_to_hiragana

def split_negated_terms(query: str):
    tokens = []
    current = ""
    in_quotes = False
    for char in query:
        if char in ('"', '“', '”'):
            in_quotes = not in_quotes
            current += char
        elif not in_quotes and char.isspace():
            if current:
                tokens.append(current)
            current = ""
        else:
            current += char
    if current:
        tokens.append(current)
        
    positives = []
    negatives = []
    for token in tokens:
        if len(token) > 1 and (token.startswith('-') or token.startswith('－')):
            term = token[1:].strip('""“”')
            if term:
                negatives.append(term)
        else:
            positives.append(token)
            
    positive_query = " ".join(positives)
    return positive_query, negatives

@functools.lru_cache(maxsize=256)
def bound_auxiliary(q):
    if detect_script(q) != 'hiragana':
        return None
    tokenizer_obj, mode = get_tagger()
    toks = list(tokenizer_obj.tokenize(q, mode))
    if len(toks) != 1 or toks[0].part_of_speech()[0] != '助動詞':
        return None
    kana = toks[0].reading_form()
    return toks[0].normalized_form(), katakana_to_hiragana(kana) if kana else q


def _space_tokens(column):
    out, parts, i = [], column.split(' '), 0
    while i < len(parts):
        if parts[i] == '' and i + 1 < len(parts) and parts[i + 1] == '':
            out.append(' ')
            i += 2
        else:
            out.append(parts[i])
            i += 1
    return out


def bound_auxiliary_in_row(bound, base_forms, readings):
    if not base_forms or not readings:
        return None
    bases, reads = _space_tokens(base_forms), _space_tokens(readings)
    lemma, reading = bound
    if len(bases) != len(reads):
        return lemma in bases and any(r.startswith(reading) for r in reads)
    return any(b == lemma and r.startswith(reading) for b, r in zip(bases, reads))


def analyze_query(q):
    content_bases = []
    sql_bases = []
    readings = []
    base_groups = []

    script_type = detect_script(q)
    
    if script_type == 'katakana':
        tokenizer_obj, mode = get_tagger()
        content_bases = [q]
        hiragana_readings = []
        for word in tokenizer_obj.tokenize(q, mode):
            norm = word.normalized_form()
            if norm != q:
                content_bases.append(norm)
            kana = word.reading_form()
            if kana:
                hiragana_readings.append(katakana_to_hiragana(kana))
        readings = hiragana_readings

        base_parts = [f'base_forms:"{q}"']
        for norm in content_bases:
            if norm != q:
                base_parts.append(f'base_forms:"{norm}"')
        sql_bases = [f'({"|" .join(base_parts).replace("|", " OR ")})']
        base_groups = [[b] for b in content_bases]
    else:
        tokenizer_obj, mode = get_tagger()
        for chunk in q.split():
            sa_verb_match = re.fullmatch(r'(屯|たむろ)(する|し|して|した|してる|している|してるんだ|します|しません|しよう)', chunk)
            ru_verb_match = re.fullmatch(r'(屯|たむろ)(る|り|れ|ろ|ら|って|った|っ|ってる|っている|ってて|ってた|ってろ|ってない|ってたい)', chunk)
            stem_match = re.fullmatch(r'(屯|たむろ)', chunk)
            
            if sa_verb_match:
                sql_bases.append('(readings:"たむろする" OR readings:("たむろ" "する") OR readings:("たむろ" "し"))')
                content_bases.extend(['屯', '為る', '屯する'])
                base_groups.extend([['屯'], ['為る'], ['屯する']])
                readings.extend(['たむろ', 'する', 'たむろっ'])
                continue
                
            if ru_verb_match:
                sql_bases.append('(readings:("たむろ" "る") OR readings:("たむろ" "って") OR readings:("たむろ" "った") OR readings:"たむろっ")')
                content_bases.extend(['屯', 'り', 'って', 'った', '屯する'])
                base_groups.extend([['屯'], ['り'], ['って'], ['った'], ['屯する']])
                readings.extend(['たむろ', 'する', 'たむろっ'])
                continue
                
            if stem_match:
                sql_bases.append('("屯 為る" OR "屯する" OR "屯")')
                content_bases.extend(['屯', '為る', '屯する'])
                base_groups.extend([['屯'], ['為る'], ['屯する']])
                readings.extend(['たむろ', 'する', 'たむろっ'])
                continue
                
            chunk_bases = []
            chunk_readings = []
            stripped_particles = 0
            for word in tokenizer_obj.tokenize(chunk, mode):
                pos = word.part_of_speech()[0]
                base = word.normalized_form()
                surface = word.surface()
                
                if surface == 'いい' and base == '言う':
                    base = '良い'
                
                kana = word.reading_form()
                reading = katakana_to_hiragana(kana) if kana else surface
                
                readings.append(reading)
                
                is_symbol = (pos == '補助記号' and not re.search(rf'[{KANJI_CHARS}\u3040-\u30FFa-zA-Z0-9ａ-ｚＡ-Ｚ０-９]', surface))
                if (pos not in ('助動詞', '助詞') and not is_symbol) or script_type in ('hiragana', 'katakana'):
                    content_bases.append(base)
                    chunk_bases.append(base)
                    if script_type == 'hiragana':
                        content_bases.append(reading)
                        chunk_readings.append(reading)
                else:
                    stripped_particles += 1
                        
            if chunk_bases:
                if script_type in ('hiragana', 'katakana') or stripped_particles == 0:
                    phrase = '"' + " ".join(chunk_bases) + '"'
                    base_groups.append(list(chunk_bases))
                    if chunk_readings and chunk_readings != chunk_bases:
                        base_groups.append(list(chunk_readings))
                else:
                    base_groups.extend([[b] for b in chunk_bases])
                    base_groups.extend([[r] for r in chunk_readings])
                    if len(chunk_bases) > 1:
                        args = " ".join(f'"{b}"' for b in chunk_bases)
                        phrase = f"NEAR({args}, {stripped_particles + 2})"
                    else:
                        phrase = f'"{chunk_bases[0]}"'
                    
                if script_type in ('hiragana', 'katakana'):
                    unbroken = katakana_to_hiragana(chunk) if script_type == 'katakana' else chunk
                    if f'"{unbroken}"' != phrase:
                        sql_bases.append(f'(({phrase}) OR "{unbroken}")')
                    else:
                        sql_bases.append(f"({phrase})")
                    if unbroken not in content_bases:
                        content_bases.append(unbroken)
                        base_groups.append([unbroken])
                elif len(chunk_bases) > 1 and '"' not in chunk:
                    sql_bases.append(f'(({phrase}) OR "{chunk}")')
                    if chunk not in content_bases:
                        content_bases.append(chunk)
                        base_groups.append([chunk])
                else:
                    sql_bases.append(f"({phrase})")
                
        if not content_bases:
            content_bases = readings
            if readings:
                phrase = '"' + " ".join(readings) + '"'
                sql_bases = [f"({phrase})"]
                base_groups = [list(readings)]
            
    return content_bases, sql_bases, readings, base_groups

RUN_ANCHOR_POS = ('動詞', '形容詞', '形状詞')

RUN_ANCHOR_NOUN_POS2 = '形状詞可能'

RUN_TAIL_POS = ('助動詞', '助詞', '接尾辞')

RUN_TAIL_KEIJOUSHI_POS1 = '助動詞語幹'


TAIL_PARTICLE_WHITELIST = frozenset({'て', 'で', 'ば', 'たり', 'だり',
                                     'ちゃ', 'ながら', 'つつ', 'たって'})

TAIL_REF_POS = ('動詞', '形容詞', '助動詞', '形状詞', '接尾辞')

TAIL_NA_DROP_KATSUYOU = '意志推量形'

TAIL_SUFFIX_NA_POS1 = '形状詞的'

PHONETIC_SKIP_POS = ('助詞', '助動詞', '補助記号', '空白')


def merge_mono_ruby(clean_text: str, spans: list) -> list:
    if len(spans) < 2:
        return spans
    tokenizer_obj, mode = get_tagger()

    def one_word(a, b):
        base = clean_text[spans[a]['start']:spans[b]['end']]
        furi = ''.join(sp['furi'] for sp in spans[a:b + 1])
        toks = list(tokenizer_obj.tokenize(base, mode))
        return len(toks) == 1 and katakana_to_hiragana(toks[0].reading_form()) == katakana_to_hiragana(furi)

    out, i = [], 0
    while i < len(spans):
        j = i
        while j + 1 < len(spans) and spans[j + 1]['start'] == spans[j]['end']:
            j += 1
        k = next((k for k in range(j, i, -1) if one_word(i, k)), None)
        if k is None:
            out.append(spans[i])
            i += 1
        else:
            out.append({'start': spans[i]['start'], 'end': spans[k]['end'],
                        'furi': ''.join(sp['furi'] for sp in spans[i:k + 1])})
            i = k + 1
    return out


_RUBY_MERGES = None

def merge_dict_ruby(clean_text: str, spans: list) -> list:
    global _RUBY_MERGES
    if _RUBY_MERGES is None:
        _RUBY_MERGES = load_ruby_merges(RUBY_MERGES_PATH)
    if len(spans) < 2 or not _RUBY_MERGES:
        return spans

    def listed(a, b):
        base = clean_text[spans[a]['start']:spans[b]['end']]
        return (base, ruby_merge_key(''.join(sp['furi'] for sp in spans[a:b + 1]))) in _RUBY_MERGES

    out, i = [], 0
    while i < len(spans):
        j = i
        while j + 1 < len(spans) and spans[j + 1]['start'] == spans[j]['end']:
            j += 1
        k = next((k for k in range(j, i, -1) if listed(i, k)), None)
        if k is None:
            out.append(spans[i])
            i += 1
        else:
            out.append({'start': spans[i]['start'], 'end': spans[k]['end'],
                        'furi': ''.join(sp['furi'] for sp in spans[i:k + 1])})
            i = k + 1
    return out


_RUBY_WHOLE = None
_RUBY_TRIMS = None

def ruby_base_trim(base: str, furi: str) -> int:
    global _RUBY_WHOLE, _RUBY_TRIMS
    if _RUBY_TRIMS is None:
        _RUBY_TRIMS = load_ruby_trims(RUBY_TRIM_PATH)
    key = (base, ruby_merge_key(furi))
    if key in _RUBY_TRIMS:
        return _RUBY_TRIMS[key]
    if _RUBY_WHOLE is None:
        _RUBY_WHOLE = load_ruby_merges(RUBY_WHOLE_PATH)
    if key in _RUBY_WHOLE:
        return 0
    hfuri = katakana_to_hiragana(furi)
    tokenizer_obj, mode = get_tagger()
    tokens = list(tokenizer_obj.tokenize(base, mode))
    if len(tokens) > 1:
        tail_reading = ""
        tail_len = 0
        for token in reversed(tokens):
            kana = token.reading_form()
            tail_reading = (katakana_to_hiragana(kana) if kana else token.surface()) + tail_reading
            tail_len += len(token.surface())
            if tail_reading == hfuri:
                return len(base) - tail_len

        last = tokens[-1].surface()
        if len(last) < len(base) and len(furi) <= max(3, len(last) * 3):
            head = katakana_to_hiragana("".join(t.reading_form() or t.surface() for t in tokens[:-1]))
            if not (head and len(hfuri) > len(head) and hfuri.startswith(head)):
                tail = katakana_to_hiragana(tokens[-1].reading_form() or last)
                if len(hfuri) > len(tail) and hfuri.endswith(tail):
                    tail_len, first_long = len(last), None
                    for token in reversed(tokens[:-1]):
                        tail = katakana_to_hiragana(token.reading_form() or token.surface()) + tail
                        tail_len += len(token.surface())
                        if len(tail) >= len(hfuri):
                            if tail[0] == hfuri[0]:
                                return len(base) - tail_len
                            first_long = first_long or tail_len
                    return len(base) - (first_long or tail_len)
                return len(base) - len(last)

    if len(base) > 1 and len(furi) == 1 and '一' <= base[-1] <= '鿿':
        return len(base) - 1

    if detect_script(furi) == 'katakana':
        prefix_match = re.match(r'^(歴代全|歴代|全|元|第[０-９0-9一二三四五六七八九十]+期)', base)
        if prefix_match and len(base) > len(prefix_match.group(1)):
            return len(prefix_match.group(1))
    return 0


def _sudachi_normalized_reading(word: str) -> str:
    tokenizer_obj, mode = get_tagger()
    norm = "".join(t.normalized_form() for t in tokenizer_obj.tokenize(word, mode))
    return _sudachi_reading(norm) if norm != word else ""


def gloss_sudachi_evidence(base: str, gloss: str, okuri: str = ""):
    h = ruby_merge_key(gloss)
    cut = ruby_base_trim(base, gloss)
    spans = [base] + ([base[cut:]] if 0 < cut < len(base) else [])
    for s in spans:
        for k in range(len(okuri) + 1):
            written, reading = s + okuri[:k], h + okuri[:k]
            if (ruby_merge_key(_sudachi_reading(written)) == reading
                    or ruby_merge_key(_sudachi_normalized_reading(written)) == reading):
                return written
    return None


_GLOSS_RUBY = None
_GLOSS_DECIDED = None
_GLOSS_KANA_RE = re.compile(r'[ァ-ヺー・]+(?:[ 　]+[ァ-ヺー・]+)*|[ぁ-ゖー・]+(?:[ 　]+[ぁ-ゖー・]+)*')
_OKURI_RE = re.compile(r'[ぁ-ゖ]{1,4}')


@functools.lru_cache(maxsize=65536)
def gloss_span(base: str, gloss: str, okuri: str = ""):
    global _GLOSS_RUBY, _GLOSS_DECIDED
    if not _GLOSS_KANA_RE.fullmatch(gloss):
        return None
    if _GLOSS_RUBY is None:
        _GLOSS_RUBY = load_ruby_merges(GLOSS_RUBY_PATH)
    if _GLOSS_DECIDED is None:
        _GLOSS_DECIDED = frozenset((b, r) for _, b, r in ruby_decisions())
    key, bare = ruby_merge_key(gloss), re.sub(r'[ 　]', '', gloss)
    cuts = [c for c in range(len(base))
            if (base[c:], key) in _GLOSS_RUBY or (base[c:], bare) in _GLOSS_DECIDED][:1]
    written = gloss_sudachi_evidence(base, gloss, okuri)
    if written:
        cuts += [next(c for c in range(len(base) + 1) if written.startswith(base[c:]))]
    cuts = [c for c in cuts if c < len(base)]
    return min(cuts) if cuts else None


def gloss_is_ruby(base: str, gloss: str, okuri: str = "") -> bool:
    return gloss_span(base, gloss, okuri) is not None


def _gloss_okuri(m) -> str:
    after = _OKURI_RE.match(m.string, m.end())
    return after.group(0) if after else ""


def _accept_gloss(m) -> bool:
    return gloss_is_ruby(m.group(2), m.group(3), _gloss_okuri(m))


_BOOK_DISPLAY_RUBY = GlossRuby(_accept_gloss)


def display_ruby_re(media: str):
    return _BOOK_DISPLAY_RUBY if media == 'epub' else RUBY_RE


_TOKEN_PAREN_RE = re.compile(r'(?<=.)[（(]')
_PAREN_POS = ('補助記号', '括弧開', '*', '*', '*', '*')


def highlight_and_furigana(text: str, content_bases: list, q: str, mark: bool = True, bold: bool = False, base_groups: list = None, readings: list = None, work: tuple = None) -> str:
    if not text: return text
    tag = "mark" if mark else "b"
    if bold: tag = "b"
    
    ruby_spans = []
    clean_text = ""
    last_idx = 0
    for m in display_ruby_re(work[0] if work else 'subs').finditer(text):
        gap = text[last_idx:m.start()]
        base = m.group(1) or m.group(2)
        furi = ruby_reading(m)

        split = None
        if not m.group(1):
            pm = SPACED_RUBY_PREV_RE.search(gap)
            split = pm and spaced_ruby_split(pm.group(1), base, furi, work)
            if split:
                clean_text += gap[:pm.start()]
                ruby_spans.append({'start': len(clean_text), 'end': len(clean_text) + len(pm.group(1)),
                                   'furi': split[0]})
                clean_text += pm.group(1)
                gap = gap[pm.end(1):]
                furi = split[1]

        clean_text += gap
        base_start = len(clean_text)

        if not m.group(1) and not split:
            if work and work[0] == 'epub':
                cut = gloss_span(base, m.group(3), _gloss_okuri(m))
            else:
                cut = ruby_base_trim(base, furi)
            clean_text += base[:cut]
            base_start = len(clean_text)
            base = base[cut:]

        clean_text += base
        base_end = len(clean_text)
        ruby_spans.append({
            'start': base_start,
            'end': base_end,
            'furi': furi
        })
        last_idx = m.end()

    clean_text += text[last_idx:]
    ruby_spans = merge_mono_ruby(clean_text, ruby_spans)
    ruby_spans = merge_dict_ruby(clean_text, ruby_spans)

    extended_bases = set(content_bases)
    exact_matches = []
    phonetic_matches = []
    furi_matches = []

    if q:
        idx = 0
        while True:
            start = clean_text.find(q, idx)
            if start == -1: break
            exact_matches.append((start, start + len(q)))
            idx = start + 1

    tokenizer_obj, mode = get_tagger()
    words_data = []
    idx = 0
    for token in tokenizer_obj.tokenize(clean_text, mode):
        surface = token.surface()
        start = clean_text.find(surface, idx)
        if start != -1:
            end = start + len(surface)
            pos_full = token.part_of_speech()
            paren = _TOKEN_PAREN_RE.search(surface)
            bracket = None
            if paren:
                bracket = {'surface': surface[paren.start():], 'lemma': '', 'dict_form': '',
                           'reading': '', 'pos': '補助記号', 'pos_full': _PAREN_POS,
                           'start': start + paren.start(), 'end': end}
                surface = surface[:paren.start()]
            words_data.append({
                'surface': surface,
                'lemma': token.normalized_form(),
                'dict_form': token.dictionary_form(),
                'reading': katakana_to_hiragana(token.reading_form() or ''),
                'pos': pos_full[0],
                'pos_full': pos_full,
                'start': start,
                'end': start + len(surface)
            })
            if bracket:
                words_data.append(bracket)
            idx = end

    if readings and q and not re.search(rf'[{KANJI_CHARS}]', q):
        kana_bases = {katakana_to_hiragana(b) for b in content_bases
                      if b and re.fullmatch(r'[぀-ゟ゠-ヿー]+', b)}
        reading_set = {r for r in set(readings) | kana_bases if r and len(r) >= 2}
        if reading_set:
            for w in words_data:
                if w['pos'] in PHONETIC_SKIP_POS:
                    continue
                if w['reading'] and w['reading'] in reading_set:
                    phonetic_matches.append((w['start'], w['end']))
            for r in ruby_spans:
                if r['furi'] and katakana_to_hiragana(r['furi']) in reading_set:
                    furi_matches.append((r['start'], r['end']))

    for m_start, m_end in phonetic_matches + furi_matches:
        for w in words_data:
            if w['start'] >= m_start and w['end'] <= m_end:
                w['reading_match'] = True

    char_hl = ['none'] * len(clean_text)

    def token_base(w):
        base = w['lemma'] if w['lemma'] else w['surface']
        return base.split('-')[0] if '-' in base else base

    if base_groups is None:
        single_bases = extended_bases
        sequences = []
    else:
        single_bases = {b for g in base_groups if len(g) == 1 for b in g}
        sequences = [g for g in base_groups if len(g) > 1]

    bound = bound_auxiliary(q) if q else None
    for w in words_data:
        surface = w['surface']
        base = token_base(w)
        if bound:
            w['is_match'] = base == bound[0] and katakana_to_hiragana(w['reading'] or surface).startswith(bound[1])
        else:
            w['is_match'] = (base in single_bases or surface in single_bases or surface == q
                             or w.get('reading_match', False))
        w['hl'] = "main" if w['is_match'] else "none"

    seq_words = [w for w in words_data if w['pos'] != '空白']
    for seq in sequences:
        n = len(seq)
        for i in range(len(seq_words) - n + 1):
            run = seq_words[i:i + n]
            if all(token_base(w) == b or w['surface'] == b for w, b in zip(run, seq)):
                for w in run:
                    w['is_match'] = True
                    w['hl'] = "main"

    def anchor_kind(w):
        p = w['pos_full']
        if p[0] in RUN_ANCHOR_POS:
            return 'full'
        if p[0] == '名詞' and len(p) > 2 and p[2] == RUN_ANCHOR_NOUN_POS2:
            return 'copula'
        return None

    def is_tail(w):
        p = w['pos_full']
        if p[0] in RUN_TAIL_POS:
            return True
        return p[0] == '形状詞' and len(p) > 1 and p[1] == RUN_TAIL_KEIJOUSHI_POS1

    for idx, w in enumerate(words_data):
        if w['hl'] != "main":
            continue
        kind = anchor_kind(w)
        if kind is None:
            continue
        for next_idx in range(idx + 1, len(words_data)):
            next_w = words_data[next_idx]
            if next_w['hl'] == "main":
                break
            if kind == 'copula' and next_idx == idx + 1 and next_w['pos'] != '助動詞':
                break
            if not is_tail(next_w):
                break
            next_w['hl'] = "tail"

    def is_na_adjective(p):
        if p[0] == '形状詞':
            return True
        if p[0] == '名詞' and len(p) > 2 and p[2] == RUN_ANCHOR_NOUN_POS2:
            return True
        return p[0] == '接尾辞' and len(p) > 1 and p[1] == TAIL_SUFFIX_NA_POS1

    def find_reference(j):
        for k in range(j - 1, -1, -1):
            p = words_data[k]['pos_full']
            if p[0] == '助詞':
                continue
            if p[0] in TAIL_REF_POS or is_na_adjective(p):
                return words_data[k]
            return None
        return None

    def classify_tail(w, ref):
        p = w['pos_full']
        if p[0] == '助詞':
            return "tail" if w['surface'] in TAIL_PARTICLE_WHITELIST else "tail-p"
        if p[0] == '接尾辞':
            return "tail"
        if ref is None:
            return "tail-p"
        if is_na_adjective(ref['pos_full']):
            if p[0] == '助動詞' and len(p) > 5 and p[5] == TAIL_NA_DROP_KATSUYOU:
                return "tail-p"
            return "tail"
        return "tail" if ref['surface'] != ref['dict_form'] else "tail-p"

    dropped = False
    for j, w in enumerate(words_data):
        if w['hl'] == "main":
            dropped = False
        elif w['hl'] == "tail":
            if dropped:
                w['hl'] = "tail-p"
            else:
                w['hl'] = classify_tail(w, find_reference(j))
                dropped = w['hl'] == "tail-p"
        else:
            dropped = False

    for w in words_data:
        w_start = w['start']
        w_end = w['end']
        if w['hl'] != "none":
            for i in range(w_start, w_end):
                char_hl[i] = w['hl']
            
    for start, end in exact_matches + phonetic_matches + furi_matches:
        for i in range(start, end):
            char_hl[i] = "main"
            
    in_hl = False
    for i in range(len(char_hl)):
        if char_hl[i] == 'main':
            in_hl = True
        elif char_hl[i] in ('tail', 'tail-p') and in_hl:
            pass
        else:
            in_hl = False
            char_hl[i] = 'none'

    result_html = ""
    i = 0
    current_hl = "none"
    
    def close_hl():
        if current_hl == "main": return f"</{tag}>"
        if current_hl in ("tail", "tail-p"): return "</span>"
        return ""

    def open_hl(hl):
        if hl == "main": return f"<{tag}>"
        if hl == "tail": return '<span class="hl-tail">'
        if hl == "tail-p": return '<span class="hl-tail-p">'
        return ""
        
    while i < len(clean_text):
        ruby = next((r for r in ruby_spans if r['start'] == i), None)
        if ruby:
            result_html += close_hl()
            current_hl = "none"
            
            inner_html = ""
            for j in range(ruby['start'], ruby['end']):
                hl = char_hl[j]
                if hl != current_hl:
                    inner_html += close_hl()
                    inner_html += open_hl(hl)
                    current_hl = hl
                c = clean_text[j]
                inner_html += HTML_ESCAPE_MAP.get(c, c)
            inner_html += close_hl()
            current_hl = "none"
            
            base_str = clean_text[ruby['start']:ruby['end']]
            is_alpha = bool(re.search(ALPHA_CHARS, base_str))
            cls = ' class="alpha"' if is_alpha else ''
            
            furi_escaped = "".join(HTML_ESCAPE_MAP.get(c, c) for c in ruby["furi"])
            result_html += f'<ruby{cls}>{inner_html}<rt>{furi_escaped}</rt></ruby>'
            i = ruby['end']
            continue
            
        hl = char_hl[i]
        if hl != current_hl:
            result_html += close_hl()
            result_html += open_hl(hl)
            current_hl = hl
            
        c = clean_text[i]
        result_html += HTML_ESCAPE_MAP.get(c, c)
        i += 1
        
    result_html += close_hl()
    
    if not (work and work[0] == 'epub'):
        result_html = result_html.replace('(', '（').replace(')', '）')
    
    return result_html

def sentence_score(line, query, is_exact, length=None):
    if length is None:
        length = calculate_display_length(line)
    
    ideal = 27
    scale = 15
    score = math.exp(-0.5 * ((length - ideal) / scale) ** 2)
    
    if is_exact:
        score += 0.5
        
    return score

def to_fullwidth(num_str, pad=1):
    trans = str.maketrans('0123456789', '０１２３４５６７８９')
    return str(int(num_str)).zfill(pad).translate(trans)


GLOBAL_TITLES = {}
GLOBAL_BOOK_TITLES = {}

def get_formatted_title(db_conn, relpath):
    global GLOBAL_TITLES
    if relpath not in GLOBAL_TITLES:
        GLOBAL_TITLES[relpath] = format_episode_title(relpath)
    return GLOBAL_TITLES[relpath]


FW_TO_HW_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')
_KANJI_DIGITS = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def parse_int_or_kanji(s):
    s = s.translate(FW_TO_HW_DIGITS)
    if s.isdigit():
        return int(s)
    total = curr = 0
    for ch in s:
        if ch in _KANJI_DIGITS:
            curr = _KANJI_DIGITS[ch]
        elif ch == '十':
            total += (curr or 1) * 10
            curr = 0
        elif ch == '百':
            total += (curr or 1) * 100
            curr = 0
        else:
            return None
    total += curr
    return total or None


SUB_EXT_RE = re.compile(r"\.(?:srt|ass|ssa)$", re.IGNORECASE)
SUB_LANG_SUFFIX_RE = re.compile(
    r"[\._\-](?:ja|jp|jpn|jap|ja[\-_]jp|ja[\-_]en|jpn[\-_]en|jp[\-_]en|chs|cht|zh|en|eng)"
    r"(?:[\._\-](?:sdh|cc|hi|forced|full))?(?:\[(?:cc|sdh|hi|forced)\])?$",
    re.IGNORECASE,
)
SUB_CRC_RE = re.compile(r"\s*[\[\(（]\s*[0-9A-Fa-f]{8}\s*[\]\)）]")
SUB_LEADING_GROUP_RE = re.compile(r"^\s*\[([^\]]*)\]\s*")
SUB_PURE_EP_BRACKET_RE = re.compile(r"^\d{1,3}(?:v\d+)?(?:[～\-・/]\d{1,3}(?:v\d+)?)?$", re.IGNORECASE)
SUB_SEASON_IN_BRACKET_RE = re.compile(
    r"(?:\d+(?:st|nd|rd|th)\s+Season|Season\s+\d+|第[0-9０-９一二三四五六七八九十]+(?:期|季|シリーズ))",
    re.IGNORECASE,
)
SUB_SE_RE = re.compile(
    r"(?:^|(?<![A-Za-z0-9]))S(\d{1,2})[\s\._\-]*E(?:P)?(\d{1,4})"
    r"(?:[\s\._\-]*(?:[\-~～]|E(?:P)?)(\d{1,4}))?(?:v\d+)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
SUB_SXE_RE = re.compile(r"(?:^|(?<![A-Za-z0-9x×]))(\d{1,2})x(\d{2,3})(?:v\d+)?(?![A-Za-z0-9x×])", re.IGNORECASE)
SUB_SPE_RE = re.compile(r"(?:^|[\s\.\-_\[（\(])SPE(\d{1,2})(?:\b|[\s\.\-_\]）\)])", re.IGNORECASE)
SUB_EP_MULTI_PREFIX_RE = re.compile(
    r"(?: - |[#＃]|第|\[|(?i:(?:^|(?<![A-Za-z0-9]))EP?[\s\._]*)|[（\(])"
    r"([0-9０-９]{1,3})(?:v\d+)?"
    r"(?:([～\-・/])(?:[#＃]|第|(?i:EP?))?([0-9０-９]{1,3})(?:v\d+)?)?"
    r"(?:話|回|幕| |\.|_|$|\]|[）\)]|(?=[「『【]))"
)
SUB_DAI_KANJI_EP_RE = re.compile(r"第\s*([0-9０-９一二三四五六七八九十百]{1,5})\s*(話|回|幕|首)(?:[\s\._\-]*|$|(?=[「『【]))")
SUB_FOUR_DIGIT_EP_RE = re.compile(r"\s+-\s+(0\d{3}|1[0-8]\d{2})(?:\s+-\s+|\b)")
SUB_BOGUS_EP_NUMS = frozenset({480, 576, 720, 1080, 1440, 1920, 2160})
SUB_SECONDARY_EP_RE = re.compile(
    r"^(?:"
    r"第([一二三四五六七八九十百0-9０-９]+)(話|幕|首|回|章)(?:[\.\s_]*|(?=[「『【!！末：:])|$)|"
    r"[#＃]?([一二三四五六七八九十百0-9０-９]+)(話|幕|首|回)(?:[\.\s_]+|(?=[「『【!！])|$)|"
    r"[#＃]([0-9０-９]{1,4})(?:[\.\s_]+|(?=[「『【])|$)|"
    r"([0-9０-９]{1,3})[\.\s_]+(?![0-9]|\s*(?:p|i|bit|fps|ch)\b)"
    r")"
)
SUB_BRACKET_JUNK_RE = re.compile(
    r"[\[\(][^\]\)]*?(?<!"
    r"[A-Za-z0-9])(?:"
    r"1080[pi]|720p|480p|576p|2160p|4K|FHD|HDTV|HEVC|AVC|AAC|FLAC|AC3|EAC3|DTS|MP3|Opus|"
    r"x264|x265|H\.?264|H\.?265|XviD|DivX|WMV9|MPEG2|RMVB|MKV|AVI|MP4|10bit|8bit|Hi10p?|"
    r"WEBRip|WEB-DL|WEBDL|WEB|BDRip|BDREMUX|BDSUP|BDSUB|BD|BluRay|Blu-ray|DVDRip|DVD-Rip|DVD|TVRip|"
    r"FFF|JPN|JAP|CHS|CHT|ENG|JPSC|JPTC|SC[\s_]*JP|JP[\s_]*SC|Japanese[\s_]*Origin|"
    r"TX|AMZN|NF|DSNP|CR|DDP|Amazon|Netflix|Hulu|Disney|Crunchyroll|Bilibili|Abema|dAnime|"
    r"SubtitleTools|Sakurato|Retimed|Re-Timed|OCR\s*unchecked|Tesseract\s*OCR|Zoro\.to|SoftSub|HardSub|big5|gbk|utf-?8|ass|srt|ssa|"
    r"1920x1080|1280x720|720x480|640x480|848x480|1440x1080|"
    r"AT-X|BS11|BSP|NHK|NHKG|NHKE|TOKYO\s*MX|MBS|TBS"
    r")(?![A-Za-z0-9])[^\]\)]*?[\]\)]",
    re.IGNORECASE,
)
SUB_EXACT_BRACKET_JUNK_RE = re.compile(
    r"\s*(?:[\[\(（](?:TV|BD|DVD|WEB|720|1080|480|AT-X|BS11|BSP|NHK|TOKYO\s*MX|MBS|TBS|JP|JA|JPN|SS|cc|sdh|hi|forced|jp_cn|ch_jp|jpn,\s*chi|日本語字幕|吹き替え|日本語|字|BS4K同時放送)[\]\)）]|\[(?:二|多|解|デ|閉|初|再|新|終)\])",
    re.IGNORECASE,
)
SUB_SPLIT_JUNK_RE = re.compile(
    r"(?: - |\s*\[|\(|\s+|[\._]|^|(?<=[」』】\)）]))(?:"
    r"DUAL|DualAudio|1080[pi]|720p|480p|576p|2160p|HD1080[pi]|HD720p|1080pNF|720pNF|Horriblesubs720p|Horriblesubs1080p|"
    r"WEB(?=$|[\s\._\-\]\)）])|WEBRip|WEB-DL|WEBDL|BDRip|BluRay|Blu-ray|BDSUP|BDSUB|BDsub|ITBD|BD(?=$|[\s\._\-\]\)）])|DVDRip|"
    r"HDTV|HEVC|hevc10|x264|x265|H\.?264|H\.?265|10bit|8bit|Hi10p?|AAC|FLAC|AC3|JPSC|JPTC|Re-?Timed\s+for|"
    r"Netflix|Amazon|AMZN|NF(?=$|[\s\._\-\[\(（\)）])|Hulu|UNCENSORED|REPACK|PROPER"
    r")(?=$|[\s\._\-\[\(（\)）])",
    re.IGNORECASE,
)
SUB_TECH_BRACKET_RE = re.compile(
    r"[\[\(（【][^\]\)）】]*?(?:@|(?<![A-Za-z0-9])(?:hevc\d*|crf|\d{3,4}[x×]\d{3,4})(?![A-Za-z0-9]))[^\]\)）】]*[\]\)）】]",
    re.IGNORECASE,
)
SUB_TECH_TAIL_RE = re.compile(
    r"[\s\._\-]*(?<![A-Za-z0-9])(?:\d{3,4}-\d{3,4}@|\S*@KFMVFR|hevc\d+(?![A-Za-z])|\d{3,4}[x×]\d{3,4}(?![0-9]))",
    re.IGNORECASE,
)
SUB_SEASON_WORD_RE = re.compile(
    r"シーズン\s*([0-9０-９]{1,2})(?:\s*(?=[#＃第]|EP?\s*\d)|[-_]([0-9０-９]{1,3})[-_ ])", re.IGNORECASE
)
SUB_SPECIAL_MARKER_RE = re.compile(r"(総集編|特別編|特別篇|番外編|完結編|スペシャル)\s*(?=[#＃第]|EP?\s*\d| - )", re.IGNORECASE)
SUB_ZH_WORD_RE = re.compile(r"字幕|简|繁|BIG5|中日|双语|雙語|汉化|卖萌", re.IGNORECASE)
SUB_JPTVCLUB_RE = re.compile(r"[\s_\-]*\d{4}-\d{2}-\d{2}[\s_\-]*JPTVclub$", re.IGNORECASE)
SUB_BARE_LANG_RE = re.compile(
    r"^(?:ja|jp|jpn|jap|sc|tc|jpsc|jptc|ja[\-_ ]jp|ja[\-_ ]en|jpn[\-_ ]en|chs|cht|en|eng|big5|retimed)$",
    re.IGNORECASE,
)
SUB_TRAILING_LANG_RE = re.compile(r"(?i)(?:^|\s+)(?:ja|jp|jpn|jap|jpsc|jptc|ja[\-_]jp|ja[\-_]en)(?:\s*\[(?:cc|sdh|hi)\])?$")
_ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10, 'XI': 11, 'XII': 12}


def _wrapped_by(s, open_ch, close_ch):
    if len(s) < 2 or s[0] != open_ch or s[-1] != close_ch:
        return False
    depth = 0
    for i, ch in enumerate(s):
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i == len(s) - 1
    return False


def _strip_outer_brackets(s):
    s = s.strip(" ._")
    while s:
        if s[0] in " ._)]":
            s = s[1:].lstrip(" ._")
        elif s[-1] in " ._([":
            s = s[:-1].rstrip(" ._")
        elif s.endswith("()") or s.endswith("[]"):
            s = s[:-2].rstrip(" ._")
        elif s.startswith("()") or s.startswith("[]"):
            s = s[2:].lstrip(" ._")
        elif _wrapped_by(s, "(", ")") or _wrapped_by(s, "[", "]"):
            s = s[1:-1].strip(" ._")
        elif s.startswith("(") and s.count("(") > s.count(")"):
            s = s[1:].lstrip(" ._")
        elif s.endswith(")") and s.count(")") > s.count("("):
            s = s[:-1].rstrip(" ._")
        elif s.startswith("[") and s.count("[") > s.count("]"):
            s = s[1:].lstrip(" ._")
        elif s.endswith("]") and s.count("]") > s.count("["):
            s = s[:-1].rstrip(" ._")
        else:
            break
    return s


def _episode_stem(filename):
    s = filename
    for _ in range(2):
        s = SUB_EXT_RE.sub("", s)
    for _ in range(2):
        s = SUB_LANG_SUFFIX_RE.sub("", s)
        s = SUB_CRC_RE.sub("", s)
    while True:
        m = SUB_LEADING_GROUP_RE.match(s)
        if not m:
            break
        inner = m.group(1).strip()
        rest = s[m.end():].lstrip()
        if not rest.strip() or SUB_PURE_EP_BRACKET_RE.match(inner) or SUB_SEASON_IN_BRACKET_RE.search(inner):
            break
        s = rest
    return s


def format_episode_title(relpath):
    import utils
    parts = re.split(r"[\\/]", relpath)
    folder_name = parts[0]
    raw_filename = parts[-1]
    filename = _episode_stem(raw_filename)
    orig_stem = utils.clean_sub_stem(raw_filename)
    stem_cleaned_more = filename != orig_stem

    season_ep = ""
    title = ""

    match_se = SUB_SE_RE.search(filename)
    match_sxe = None if match_se else SUB_SXE_RE.search(filename)
    if match_sxe:
        s_cand, e_cand = int(match_sxe.group(1)), int(match_sxe.group(2))
        if not (1 <= s_cand <= 30 and 1 <= e_cand <= 250) or (s_cand == 3 and e_cand == 3 and "3x3" in filename.lower()):
            match_sxe = None

    if match_se or match_sxe:
        if match_se:
            s = int(match_se.group(1))
            e = int(match_se.group(2))
            e2_raw = int(match_se.group(3)) if match_se.group(3) else None
            if e2_raw is not None and e < e2_raw <= e + 12:
                e2 = e2_raw
                se_end = match_se.end()
            else:
                e2 = None
                se_end = match_se.end(2)
                if filename[se_end:match_se.end()].lower().startswith("v"):
                    se_end = match_se.end()
        else:
            s = int(match_sxe.group(1))
            e = int(match_sxe.group(2))
            e2 = None
            se_end = match_sxe.end()

        if e2 is not None:
            season_ep = f"{to_fullwidth(s)}期{to_fullwidth(e, 2)}～{to_fullwidth(e2, 2)}話"
        else:
            season_ep = f"{to_fullwidth(s)}期{to_fullwidth(e, 2)}話"

        title_part = filename[se_end:]
        is_fractional = bool(re.match(r"^\.\d+\b", title_part))
        title_part = re.split(r"\.(?:WEBRip|WEB-DL|WEBDL|BDRip|BluRay|DVDRip|BD|2160p|1080[pi]|720p|576p|480p)",
                              title_part, flags=re.IGNORECASE)[0]
        title_part = title_part.strip(" ._")

        m_ep = None if is_fractional else SUB_SECONDARY_EP_RE.match(title_part)
        if m_ep:
            ep_num_str = m_ep.group(1) or m_ep.group(3) or m_ep.group(5) or m_ep.group(6)
            counter = m_ep.group(2) or m_ep.group(4) or ""
            rest = title_part[m_ep.end():].strip(" ._")
            ep_hw = ep_num_str.translate(FW_TO_HW_DIGITS)
            if ep_hw.isdigit():
                ep_num = int(ep_hw)
                if ep_num == e:
                    title_part = rest
                elif counter not in ("回", "章"):
                    title_part = f"{to_fullwidth(ep_num)}話｜{rest}" if rest else f"{to_fullwidth(ep_num)}話"
            else:
                title_part = rest

        title_part = re.sub(r"^(?:最終話|最終章(?:\.前編|\.後編)?)[\.\s]*", "", title_part)
        title_part = re.sub(r"^【.*?】", "", title_part)
        title_part = re.split(r"(?:WEBRip|WEB-DL|WEBDL|BDRip|BluRay|DVDRip|BD|2160p|1080[pi]|720p|576p|480p)",
                              title_part, flags=re.IGNORECASE)[0]
        title = title_part.strip(" ._").replace(".", " ").replace("_", " ")
    else:
        s_str = ""
        match_s = re.search(r"([０-９0-9]+)(?:st|nd|rd|th)\b", filename, re.IGNORECASE)
        if match_s:
            s_str = f"{to_fullwidth(match_s.group(1).translate(FW_TO_HW_DIGITS))}期"
            filename = filename[:match_s.start()] + filename[match_s.end():]
        else:
            match_s = SUB_SEASON_WORD_RE.search(filename)
            if match_s:
                s_str = f"{to_fullwidth(match_s.group(1).translate(FW_TO_HW_DIGITS))}期"
                ep_here = f" ＃{match_s.group(2)} " if match_s.group(2) else " "
                filename = filename[:match_s.start()] + ep_here + filename[match_s.end():]

        match_jp_se = re.search(r"第([０-９0-9]+)(?:シリーズ|期)(.*?)[（\(]([０-９0-9]{2,3})[）\)]", filename)
        if match_jp_se:
            s = int(match_jp_se.group(1).translate(FW_TO_HW_DIGITS))
            extra = match_jp_se.group(2).strip()
            e = int(match_jp_se.group(3).translate(FW_TO_HW_DIGITS))
            season_ep = f"{to_fullwidth(s)}期{to_fullwidth(e, 2)}話"
            title_part = filename[match_jp_se.end():].strip()
            title = re.split(r"(?: - | \[)", title_part)[0].strip()
            if extra:
                folder_name += extra
        else:
            match_ova = re.search(r"\b(OVA\s+.*?)(?:\s*\(|$)", filename, re.IGNORECASE)
            if match_ova:
                title = match_ova.group(1).strip()

            match_spe = SUB_SPE_RE.search(filename)
            match_4d = None if match_spe else SUB_FOUR_DIGIT_EP_RE.search(filename)
            if match_4d and "0080" in match_4d.group(1) and "Gundam" in filename:
                match_4d = None

            valid_ep_match = None
            for m_cand in SUB_EP_MULTI_PREFIX_RE.finditer(filename):
                v1 = int(m_cand.group(1).translate(FW_TO_HW_DIGITS))
                if m_cand.group(0)[:1] in "[(（" and (v1 in SUB_BOGUS_EP_NUMS or 1900 <= v1 <= 2030):
                    continue
                valid_ep_match = m_cand
                break

            match_kanji_dai = None if (match_spe or match_4d or valid_ep_match) else SUB_DAI_KANJI_EP_RE.search(filename)

            if match_spe or match_4d:
                m = match_spe or match_4d
                season_ep = s_str + f"{to_fullwidth(int(m.group(1)), 2)}話"
                title_part = filename[m.end():].strip(" ._-")
                title = re.split(r"(?: - | \[)", title_part)[0].strip()
            elif valid_ep_match:
                e1 = int(valid_ep_match.group(1).translate(FW_TO_HW_DIGITS))
                sep = valid_ep_match.group(2)
                if valid_ep_match.group(3):
                    e2 = int(valid_ep_match.group(3).translate(FW_TO_HW_DIGITS))
                    norm_sep = "～" if sep in ("～", "・", "/") else "-"
                    season_ep = f"{to_fullwidth(e1, 2)}{norm_sep}{to_fullwidth(e2, 2)}話"
                else:
                    season_ep = f"{to_fullwidth(e1, 2)}話"
                season_ep = s_str + season_ep

                title_part = filename[valid_ep_match.end():].strip(" ._-")
                if not title_part and orig_stem.endswith(filename[valid_ep_match.start():]):
                    title_part = filename[:valid_ep_match.start()].strip(" ._-")
                    if re.fullmatch(r"(?:\s*\[[^\]]*\])+", title_part) or (
                            stem_cleaned_more and title_part.lower() == folder_name.strip(" ._-").lower()):
                        title_part = ""
                title = re.split(r"(?: - | \[)", title_part)[0].strip()
            elif match_kanji_dai:
                e1 = parse_int_or_kanji(match_kanji_dai.group(1))
                if e1 is not None:
                    season_ep = s_str + f"{to_fullwidth(e1, 2)}話"
                    title_part = filename[match_kanji_dai.end():].strip(" ._-")
                    title = re.split(r"(?: - | \[)", title_part)[0].strip()

            if season_ep:
                m_sp = SUB_SPECIAL_MARKER_RE.search(filename)
                if m_sp and m_sp.group(1) not in title:
                    title = m_sp.group(1) + title

            if not season_ep:
                match_word_ep = re.search(
                    r"\b(?:Karte|Stage|Phase|Episode|ACT)\s+(\d{1,3}|I{1,3}|IV|V|VI{1,3}|IX|X{1,2}|XI{1,2})\b",
                    filename, re.IGNORECASE)
                if match_word_ep:
                    val = match_word_ep.group(1).upper()
                    e1 = int(val) if val.isdigit() else _ROMAN.get(val, 1)
                    season_ep = s_str + f"{to_fullwidth(e1, 2)}話"
                    title_part = filename[match_word_ep.end():].strip(" ._-")
                    title_part = re.sub(r"^OVA\s+", "", title_part, flags=re.IGNORECASE)
                    title = re.split(r"(?: - | \[)", title_part)[0].strip()

            if not title and not season_ep:
                title = filename

    if title:
        t = SUB_BRACKET_JUNK_RE.sub("", title).strip()
        t = SUB_EXACT_BRACKET_JUNK_RE.sub("", t).strip()
        parts_junk = SUB_SPLIT_JUNK_RE.split(t, maxsplit=1)
        t = parts_junk[0].strip()
        if t and len(parts_junk) > 1:
            for inner in re.findall(r"\[([^\[\]]+)\]", parts_junk[-1]):
                if (re.search(r"[぀-ヿ㐀-鿿]{2}", inner)
                        and not SUB_BRACKET_JUNK_RE.search(f"[{inner}]")
                        and not SUB_TECH_BRACKET_RE.search(f"[{inner}]")
                        and not SUB_ZH_WORD_RE.search(inner)):
                    t = f"{t}（{inner.strip()}）"
                    break
        t = SUB_JPTVCLUB_RE.sub("", t).strip()
        t = re.sub(r"^【テレビ東京オンデマンド】[\.\s]*", "", t).strip()
        t = re.sub(r"\[字\]", "", t).strip()
        t = re.sub(r"\[映\]", "", t).strip()
        t = SUB_TECH_BRACKET_RE.sub("", t).strip()
        t = SUB_TECH_TAIL_RE.split(t)[0].strip()

        title = _strip_outer_brackets(t)
        title = re.sub(r"\s+-\s*$", "", title)
        title = re.sub(r"^\s*-\s+", "", title)
        if re.fullmatch(r"-\s*", title):
            title = ""
        title = title.replace(".", " ").replace("_", " ")

        title = re.sub(r"(?i)\bChapter\s*\d+\b", "", title)
        after_lang = SUB_TRAILING_LANG_RE.sub("", title)
        if SUB_BARE_LANG_RE.match(after_lang.strip()):
            after_lang = ""
        old_after_lang = re.sub(r"(?i)\s+(?:ja|jp)$", "", title)
        title = after_lang.strip() if after_lang != old_after_lang else old_after_lang
        title = re.sub(r"\b\d{3,5}-(DLC)", r"\1", title, flags=re.IGNORECASE)

        if title:
            title = utils.convert_hw_katakana(title)
            for old, new in utils.SUBS_STR_REPLACEMENTS:
                title = title.replace(old, new)
            title = title.replace("(", "（").replace(")", "）")
            if title:
                return f"{folder_name}｜{title}｜{season_ep}" if season_ep else f"{folder_name}｜{title}"
    if season_ep:
        return f"{folder_name}｜{season_ep}"
    return folder_name or filename


GLOBAL_BOOK_AUTHORS = {}

def load_book_authors(db_epub):
    global GLOBAL_BOOK_AUTHORS
    if db_epub is not None and not GLOBAL_BOOK_AUTHORS:
        try:
            cur = db_epub.execute("SELECT title, author FROM sources")
            for r in cur:
                if r["title"] and r["author"]:
                    GLOBAL_BOOK_AUTHORS[r["title"]] = r["author"]
        except Exception:
            pass

def format_book_title(file_key, db_epub=None):
    if file_key in GLOBAL_BOOK_TITLES:
        return GLOBAL_BOOK_TITLES[file_key]
    load_book_authors(db_epub)
    import utils
    parts = re.split(r"[\\/]", file_key)
    book_title = parts[0]
    ch_part = parts[1] if len(parts) > 1 else ""
    
    ch_clean = re.sub(r'^\d+\.', '', ch_part).strip()
    
    author = GLOBAL_BOOK_AUTHORS.get(book_title, "")
    if not author:
        m = re.match(r'^\[(.*?)\]\s*(.*)$', book_title)
        if m:
            author = m.group(1).strip()
            book_title = m.group(2).strip()
            
    book_title = utils.clean_book_title(book_title)
    book_title = utils.convert_hw_katakana(book_title)
    for old, new in utils.EPUB_STR_REPLACEMENTS:
        book_title = book_title.replace(old, new)
        
    if ch_clean:
        ch_clean = utils.convert_hw_katakana(ch_clean)
        for old, new in utils.EPUB_STR_REPLACEMENTS:
            ch_clean = ch_clean.replace(old, new)
            
    if author:
        author = utils.convert_hw_katakana(author)
        for old, new in utils.EPUB_STR_REPLACEMENTS:
            author = author.replace(old, new)

    if ch_clean in (book_title, '本文', '本編', '') or utils.BOOK_RAW_FILE_CH_RE.search(ch_clean):
        ch_clean = ''
        
    if author:
        res = f"{author}｜{book_title}｜{ch_clean}" if ch_clean else f"{author}｜{book_title}"
    else:
        res = f"{book_title}｜{ch_clean}" if ch_clean else book_title
    GLOBAL_BOOK_TITLES[file_key] = res
    return res

RESULT_CACHE_ENABLED = True
RESULT_CACHE_MAX_ROWS = 4_000_000
_RESULT_CACHE = OrderedDict()
_RESULT_CACHE_LOCK = threading.Lock()
_MEDIA_CODES = {"subs": 0, "epub": 1}
_MEDIA_NAMES = ("subs", "epub")


def _result_folder(media_type, file):
    if media_type == "epub":
        return file.rsplit('\\', 1)[0] if '\\' in file else re.split(r"[\\/]", file)[0]
    return re.split(r"[\\/]", file)[0]


def _result_cache_key(q, sort, seed, media, exact, folder, file, targets):
    if not RESULT_CACHE_ENABLED or (sort == "random" and seed is None):
        return None
    return (q, sort, seed if sort == "random" else None, media, bool(exact), folder or None,
            file or None, db_fingerprint(*[conn for _, conn, _ in targets]))


def _result_cache_get(key):
    if key is None:
        return None
    with _RESULT_CACHE_LOCK:
        entry = _RESULT_CACHE.get(key)
        if entry is not None:
            _RESULT_CACHE.move_to_end(key)
        return entry


def _result_cache_put(key, valid_results, folder_counts):
    if key is None:
        return
    entry = {
        "media": array("b", (_MEDIA_CODES[r["media_type"]] for r in valid_results)),
        "rowid": array("q", (r["rowid"] for r in valid_results)),
        "score": array("d", (r["score"] for r in valid_results)),
        "char_count": array("q", (r["char_count"] for r in valid_results)),
        "folder_counts": dict(folder_counts),
    }
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE[key] = entry
        _RESULT_CACHE.move_to_end(key)
        total = sum(len(e["rowid"]) for e in _RESULT_CACHE.values())
        while total > RESULT_CACHE_MAX_ROWS and len(_RESULT_CACHE) > 1:
            _, old = _RESULT_CACHE.popitem(last=False)
            total -= len(old["rowid"])


def _result_cache_page(entry, offset, limit, targets):
    idx = range(offset, min(offset + limit, len(entry["rowid"])))
    tables = {m_type: (table, conn) for table, conn, m_type in targets}
    wanted = {}
    for i in idx:
        wanted.setdefault(_MEDIA_NAMES[entry["media"][i]], []).append(entry["rowid"][i])
    rows = {}
    for m_type, rowids in wanted.items():
        if m_type not in tables:
            return None
        table, conn = tables[m_type]
        for j in range(0, len(rowids), 500):
            part = rowids[j:j + 500]
            sql = (f"SELECT rowid, line, file, clean_text, readings, base_forms FROM {table} "
                   f"WHERE rowid IN ({','.join('?' * len(part))})")
            for r in conn.execute(sql, part):
                rows[(m_type, r["rowid"])] = dict(r)
    chunk = []
    for i in idx:
        m_type = _MEDIA_NAMES[entry["media"][i]]
        row_dict = rows.get((m_type, entry["rowid"][i]))
        if row_dict is None:
            return None
        row_dict["media_type"] = m_type
        row_dict["folder"] = _result_folder(m_type, row_dict["file"])
        row_dict["score"] = entry["score"][i]
        row_dict["char_count"] = entry["char_count"][i]
        del row_dict["clean_text"]
        chunk.append(row_dict)
    return chunk


_RESULT_FLIGHT = {}
_RESULT_FLIGHT_LOCK = threading.Lock()
_ABORTED = object()


def _result_flight_join(key, abort_flag, held):
    while True:
        with _RESULT_FLIGHT_LOCK:
            ev = _RESULT_FLIGHT.get(key)
            if ev is None:
                ev = _RESULT_FLIGHT[key] = threading.Event()
                held.append((key, ev))
                return None
        while not ev.wait(0.25):
            if abort_flag and abort_flag[0]:
                return _ABORTED
        entry = _result_cache_get(key)
        if entry is not None:
            return entry


def _result_flight_release(held):
    with _RESULT_FLIGHT_LOCK:
        while held:
            key, ev = held.pop()
            if _RESULT_FLIGHT.get(key) is ev:
                del _RESULT_FLIGHT[key]
            ev.set()


SEARCH_COST = {"subtitles": 9e-6, "epubs": 13e-6, "pass": 15e-6, "like": 0.6e-6, "fixed": 0.5}
_SEARCH_PROGRESS = {}
_SEARCH_PROGRESS_LOCK = threading.Lock()


def _search_targets(db_subs, db_epub, media):
    targets = []
    if media in ('all', 'subs') and db_subs is not None:
        targets.append(('subtitles', db_subs, 'subs'))
    if media in ('all', 'epub') and db_epub is not None:
        targets.append(('epubs', db_epub, 'epub'))
    if not targets and db_subs is not None:
        targets.append(('subtitles', db_subs, 'subs'))
    return targets


def _learn_cost(name, seconds, rows):
    if rows >= 20000 and seconds > 0:
        SEARCH_COST[name] = 0.7 * SEARCH_COST[name] + 0.3 * (seconds / rows)


def search_progress(db_subs, db_epub, q, sort="recommended", seed=None, media="all", exact=False,
                    folder=None, file=None):
    import time
    key = _result_cache_key(q, sort, seed, media, exact, folder, file,
                            _search_targets(db_subs, db_epub, media))
    with _SEARCH_PROGRESS_LOCK:
        p = _SEARCH_PROGRESS.get(key)
    if p is None:
        return {"running": False}
    now = time.monotonic()
    if p.get("expected_rows") is None:
        conns = {"subtitles": db_subs, "epubs": db_epub}
        rows = {}
        for table, sql, params in p["counts"]:
            conn = conns.get(table)
            try:
                if sql is None:
                    rows[table] = ("like", conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] or 0)
                else:
                    rows[table] = ("match", conn.execute(sql, params).fetchone()[0])
            except (sqlite3.Error, AttributeError):
                rows[table] = ("match", 0)
        p["expected_rows"] = rows
    rows = p["expected_rows"]
    matched = sum(n for kind, n in rows.values() if kind == "match")
    sql_left = 0.0
    if p["phase"] == "query":
        sql_total = sum(n * SEARCH_COST["like" if kind == "like" else table] for table, (kind, n) in rows.items())
        sql_done = sum(p["sql_seconds"].values())
        in_table = now - p["table_start"]
        cur = rows.get(p["table"])
        cur_total = cur[1] * SEARCH_COST["like" if cur[0] == "like" else p["table"]] if cur else 0
        rest = sum(n * SEARCH_COST["like" if kind == "like" else t]
                   for t, (kind, n) in rows.items() if t not in p["sql_seconds"] and t != p["table"])
        sql_left = max(cur_total - in_table, 0.1 * cur_total) + rest
        pass_left = matched * SEARCH_COST["pass"]
    elif p["phase"] == "pass":
        done, total = p["pass_done"], p["pass_total"]
        spent = now - p["pass_start"]
        rate = spent / done if done > 1000 else SEARCH_COST["pass"]
        pass_left = (total - done) * rate
    else:
        pass_left = 0.0
    return {"running": True, "elapsed": round(now - p["start"], 1),
            "remaining": round(sql_left + pass_left + SEARCH_COST["fixed"], 1)}


def get_search_results(db, q, folders=None, sort='recommend', folder=None, exact=False, abort_flag=None, limit=500, offset=0, file=None, db_epub=None, media='all', seed=None):
    held = []
    progress = []
    try:
        return _search_results(db, q, folders, sort, folder, exact, abort_flag, limit, offset,
                               file, db_epub, media, seed, held, progress)
    finally:
        with _SEARCH_PROGRESS_LOCK:
            for key in progress:
                _SEARCH_PROGRESS.pop(key, None)
        _result_flight_release(held)


def _search_results(db, q, folders, sort, folder, exact, abort_flag, limit, offset, file, db_epub, media, seed, held, progress=None):
    import time
    neg_info = []
    clean_q = ""
    content_bases = []
    readings = []
    cache_key = cached = cached_page = None
    entry = None

    if isinstance(db, (tuple, list)):
        db_subs = db[0] if len(db) > 0 else None
        if len(db) > 1 and db_epub is None:
            db_epub = db[1]
    else:
        db_subs = db

    if db_epub is not None:
        load_book_authors(db_epub)

    targets = _search_targets(db_subs, db_epub, media)

    def get_sort_key(f):
        import utils
        mixed = utils.katakana_to_hiragana(f).replace('ゔ', 'う').lower()
        return mixed.encode('shift_jis', errors='ignore')

    if not q:
        all_folders_set = set()
        folder_map = {}
        for table_name, db_conn, m_type in targets:
            if db_conn is None: continue
            try:
                if m_type == 'epub':
                    cur = db_conn.execute("SELECT relpath, title FROM sources")
                    for r in cur:
                        title = r["title"]
                        f_name = title if title else (r["relpath"][:-5] if r["relpath"].lower().endswith(".epub") else r["relpath"])
                        all_folders_set.add(f_name)
                        folder_map[f_name] = (table_name, db_conn, m_type)
                else:
                    cur = db_conn.execute("SELECT relpath FROM sources")
                    for r in cur:
                        f_name = re.split(r"[\\/]", r["relpath"])[0]
                        all_folders_set.add(f_name)
                        folder_map[f_name] = (table_name, db_conn, m_type)
            except Exception:
                pass

        all_folders = sorted(list(all_folders_set), key=get_sort_key)
        global_counts = {f: 0 for f in all_folders}
        if not folder:
            return [], global_counts, 0, all_folders, False

        target_info = folder_map.get(folder)
        if target_info:
            target_table, target_db, target_media = target_info
        else:
            target_table, target_db, target_media = targets[0]

        folder_where = "WHERE (file LIKE ? OR file LIKE ?)"
        folder_params = [folder + "\\%", folder + "/%"]
        if file:
            where_clause = "WHERE file = ?"
            query_params = [file]
        else:
            where_clause, query_params = folder_where, folder_params

        fingerprint = db_fingerprint(target_db)

        def cached_count(key, where, params):
            key = (key, fingerprint)
            if key not in GLOBAL_FOLDER_COUNTS:
                GLOBAL_FOLDER_COUNTS[key] = target_db.execute(f"SELECT COUNT(rowid) FROM {target_table} {where}", params).fetchone()[0]
            return GLOBAL_FOLDER_COUNTS[key]

        try:
            global_counts[folder] = cached_count(folder, folder_where, folder_params)
            total_in_folder = cached_count((folder, file), where_clause, query_params) if file else global_counts[folder]
            query = f"SELECT rowid, line, file, clean_text, readings, base_forms FROM {target_table} {where_clause} ORDER BY file ASC, rowid ASC LIMIT ? OFFSET ?"
            cur = target_db.execute(query, query_params + [limit, offset])
            rows = cur.fetchall()
            
            results = []
            for r in rows:
                row_dict = dict(r)
                row_dict["media_type"] = target_media
                row_dict["folder"] = folder
                row_dict["score"] = 1.0
                row_dict["char_count"] = calculate_display_length(row_dict["line"], target_media)
                if target_media == 'epub':
                    row_dict["title"] = format_book_title(row_dict["file"], db_epub=db_epub)
                else:
                    row_dict["title"] = get_formatted_title(target_db, row_dict["file"])
                    
                line = row_dict["line"]
                row_dict["display_line"] = highlight_and_furigana(line, [], "", mark=False, bold=False, work=work_key(target_media, row_dict["file"]))
                results.append(row_dict)

            has_more = (offset + len(rows)) < total_in_folder
            db_total = get_db_total(db_subs, db_epub, media)
            return results, global_counts, db_total, all_folders, has_more
        except sqlite3.OperationalError:
            db_total = get_db_total(db_subs, db_epub, media)
            return [], global_counts, db_total, all_folders, False
    else:
        pos_q, neg_terms = split_negated_terms(q)
        is_exact_phrase = exact or (pos_q.startswith('"') and pos_q.endswith('"')) or (pos_q.startswith('”') and pos_q.endswith('”'))
        clean_q = pos_q.strip('""“”')
        
        if not clean_q:
            return [], {}, 0, [], False
        
        script_type = detect_script(clean_q)
        content_bases, sql_bases, readings, base_groups = analyze_query(clean_q)
        bound = None if is_exact_phrase else bound_auxiliary(clean_q)
        
        neg_info = []
        for neg in neg_terms:
            neg_clean = neg.strip('""“”')
            if neg_clean:
                neg_cb, _, neg_rd, _ = analyze_query(neg_clean)
                neg_info.append({
                    'term': neg_clean,
                    'bases': neg_cb,
                    'readings': neg_rd
                })
        
        all_candidate_rows = []
        global_counts = {}

        cache_key = _result_cache_key(q, sort, seed, media, exact, folder, file, targets)
        cached = _result_cache_get(cache_key)
        if cached is None and cache_key is not None:
            cached = _result_flight_join(cache_key, abort_flag, held)
            if cached is _ABORTED:
                return [], {}, 0, [], False
        cached_page = _result_cache_page(cached, offset, limit, targets) if cached is not None else None
        if cached_page is None:
            cached = None

        def build_where(table_name):
            wheres = []
            where_params = []

            if is_exact_phrase:
                match_str = " AND ".join(sql_bases)
                if match_str:
                    wheres.append(f"{table_name} MATCH ?")
                    where_params.append(match_str)
                elif readings:
                    match_str = '"' + " ".join(readings) + '"'
                    if match_str != '""':
                        wheres.append(f"{table_name} MATCH ?")
                        where_params.append(f"readings: {match_str}")
                wheres.append("clean_text LIKE ?")
                where_params.append(f"%{clean_q}%")
            else:
                match_str = " AND ".join(sql_bases)
                if match_str:
                    if len(readings) >= 2 and script_type == 'hiragana':
                        r_str = '"' + " ".join(readings) + '"'
                        wheres.append(f"({table_name} MATCH ? OR {table_name} MATCH ?)")
                        where_params.extend([match_str, f"readings: {r_str}"])
                    else:
                        wheres.append(f"{table_name} MATCH ?")
                        where_params.append(match_str)
                elif readings:
                    match_str = '"' + " ".join(readings) + '"'
                    if match_str != '""':
                        wheres.append(f"{table_name} MATCH ?")
                        where_params.append(f"readings: {match_str}")
                    else:
                        wheres.append("line LIKE ?")
                        where_params.append(f"%{clean_q}%")
                else:
                    wheres.append("line LIKE ?")
                    where_params.append(f"%{clean_q}%")

            for n in neg_info:
                wheres.append("clean_text NOT LIKE ?")
                where_params.append(f"%{n['term']}%")
                wheres.append("line NOT LIKE ?")
                where_params.append(f"%{n['term']}%")
                for b in n['bases']:
                    wheres.append("base_forms NOT LIKE ?")
                    where_params.append(f"%{b}%")

            if file:
                wheres.append("file = ?")
                where_params.append(file)
            elif folder and not q:
                wheres.append("(file LIKE ? OR file LIKE ?)")
                where_params.extend([folder + "\\%", folder + "/%"])
            match = None
            if wheres and "MATCH" in wheres[0]:
                n_params = wheres[0].count("?")
                match = (f"SELECT count(*) FROM {table_name} WHERE {wheres[0]}", where_params[:n_params])
            return wheres, where_params, match

        if cached is None and cache_key is not None and progress is not None:
            now = time.monotonic()
            entry = {"start": now, "phase": "query", "table": None, "table_start": now,
                     "sql_seconds": {}, "pass_done": 0, "pass_total": 0, "pass_start": None,
                     "expected_rows": None,
                     "counts": [(t, *(build_where(t)[2] or (None, None))) for t, c, _ in targets if c is not None]}
            with _SEARCH_PROGRESS_LOCK:
                _SEARCH_PROGRESS[cache_key] = entry
            progress.append(cache_key)
        else:
            entry = None

        for table_name, db_conn, m_type in (targets if cached is None else ()):
            if db_conn is None: continue
            if abort_flag and abort_flag[0]:
                return [], {}, 0, [], False
            wheres, where_params, _ = build_where(table_name)
            if entry is not None:
                entry["table"], entry["table_start"] = table_name, time.monotonic()
                rows_before = len(all_candidate_rows)

            where_clause = f"WHERE ({' AND '.join(wheres)})"
            if sort == "chrono":
                query = f"SELECT rowid, line, file, clean_text, readings, base_forms FROM {table_name} {where_clause} ORDER BY file ASC, rowid ASC"
            else:
                query = f"SELECT MIN(rowid) as rowid, line, file, clean_text, readings, base_forms FROM {table_name} {where_clause} GROUP BY line"

            try:
                cur = db_conn.execute(query, where_params)
                for r in cur.fetchall():
                    rd = dict(r)
                    rd["media_type"] = m_type
                    all_candidate_rows.append(rd)
            except sqlite3.OperationalError as e:
                if "interrupted" in str(e):
                    raise
                fallback_wheres = ["line LIKE ?"]
                fallback_params = [f"%{clean_q}%"]
                for n in neg_info:
                    fallback_wheres.append("line NOT LIKE ?")
                    fallback_params.append(f"%{n['term']}%")
                if file:
                    fallback_wheres.append("file = ?")
                    fallback_params.append(file)
                elif folder:
                    fallback_wheres.append("(file LIKE ? OR file LIKE ?)")
                    fallback_params.extend([folder + "\\%", folder + "/%"])
                fb_clause = f"WHERE ({' AND '.join(fallback_wheres)})"
                if sort == "chrono":
                    fb_query = f"SELECT rowid, line, file, clean_text, readings, base_forms FROM {table_name} {fb_clause} ORDER BY file ASC, rowid ASC"
                else:
                    fb_query = f"SELECT MIN(rowid) as rowid, line, file, clean_text, readings, base_forms FROM {table_name} {fb_clause} GROUP BY line"
                try:
                    cur = db_conn.execute(fb_query, fallback_params)
                    for r in cur.fetchall():
                        rd = dict(r)
                        rd["media_type"] = m_type
                        all_candidate_rows.append(rd)
                except Exception:
                    pass
            if entry is not None:
                spent = time.monotonic() - entry["table_start"]
                entry["sql_seconds"][table_name] = spent
                if "MATCH" in wheres[0]:
                    _learn_cost(table_name, spent, len(all_candidate_rows) - rows_before)

    results = []
    folder_counts = {}
    global_total = 0
    valid_results = []
    
    if entry is not None:
        entry["phase"], entry["pass_total"], entry["pass_start"] = "pass", len(all_candidate_rows), time.monotonic()
    for i, row_dict in enumerate(all_candidate_rows):
        if abort_flag and abort_flag[0] and i % 100 == 0:
            return [], {}, 0, [], False
        if entry is not None and i % 2000 == 0:
            entry["pass_done"] = i
            
        line = row_dict["line"]
        clean_text = row_dict["clean_text"]
        folder_name = _result_folder(row_dict.get("media_type"), row_dict["file"])

        is_neg_hit = False
        if neg_info:
            for n in neg_info:
                if n['term'] in clean_text or n['term'] in line:
                    is_neg_hit = True
                    break
                row_bases = row_dict.get("base_forms") or ""
                for b in n['bases']:
                    if b in row_bases:
                        is_neg_hit = True
                        break
                if is_neg_hit:
                    break
        if is_neg_hit:
            continue
        
        if bound and bound_auxiliary_in_row(bound, row_dict.get("base_forms"), row_dict.get("readings")) is False:
            continue

        is_exact = clean_q in clean_text
        if not is_exact and readings:
            q_reading = "".join(readings)
            r_reading = row_dict.get("readings")
            if r_reading and q_reading in r_reading.replace(" ", ""):
                is_exact = True
                
        if len(clean_q) > 0 and not is_exact:
            valid = False
            for cb in content_bases:
                if cb in clean_text or (row_dict.get("readings") and cb in row_dict["readings"]) or (row_dict.get("base_forms") and cb in row_dict["base_forms"]):
                    valid = True
                    break
            if not valid:
                continue
                    
        char_count = calculate_display_length(line, row_dict.get("media_type") or "subs")
        score = sentence_score(line, clean_q, is_exact, char_count)

        row_dict["folder"] = folder_name
        row_dict["score"] = score
        row_dict["char_count"] = char_count
        
        del row_dict["clean_text"]
        
        folder_counts[folder_name] = folder_counts.get(folder_name, 0) + 1
        global_total += 1
        
        if not folder or folder_name == folder:
            valid_results.append(row_dict)

    if entry is not None:
        entry["phase"] = "render"
        _learn_cost("pass", time.monotonic() - entry["pass_start"], len(all_candidate_rows))
    if sort == "desc":
        valid_results.sort(key=lambda x: x["char_count"], reverse=True)
    elif sort == "asc":
        valid_results.sort(key=lambda x: x["char_count"], reverse=False)
    elif sort == "random":
        (random.Random(seed) if seed is not None else random).shuffle(valid_results)
    elif sort == "chrono":
        pass
    else:
        valid_results.sort(key=lambda x: x["score"], reverse=True)

    if cached is not None:
        paginated_chunk = cached_page
        folder_counts = dict(cached["folder_counts"])
        n_valid = len(cached["rowid"])
    else:
        _result_cache_put(cache_key, valid_results, folder_counts)
        _result_flight_release(held)
        paginated_chunk = valid_results[offset : offset + limit]
        n_valid = len(valid_results)
    
    for row_dict in paginated_chunk:
        if abort_flag and abort_flag[0]:
            return [], {}, 0, [], False
            
        if row_dict.get("media_type") == "epub":
            row_dict["title"] = format_book_title(row_dict["file"], db_epub=db_epub)
        else:
            row_dict["title"] = get_formatted_title(db_subs, row_dict["file"])
            
        line = row_dict["line"]
        row_dict["display_line"] = highlight_and_furigana(line, content_bases, clean_q, mark=False, bold=True, base_groups=base_groups, readings=readings, work=work_key(row_dict.get("media_type") or "subs", row_dict["file"]))
        results.append(row_dict)

    if q:
        global_counts = folder_counts
        all_folders = sorted(global_counts.keys(), key=lambda f: global_counts[f], reverse=True)
        global_total = sum(global_counts.values())
    else:
        global_total = get_db_total(db_subs, db_epub, media)
    has_more = (offset + limit) < n_valid
    return results, global_counts, global_total, all_folders, has_more


def reset_caches():
    global _RUBY_LEXICON
    with _RUBY_LEXICON_LOCK:
        _RUBY_LEXICON = None
    GLOBAL_BOOK_TITLES.clear()
    GLOBAL_BOOK_AUTHORS.clear()
    GLOBAL_TITLES.clear()


_MEDIA_LIBRARY = {}
_MEDIA_LIBRARY_LOCK = threading.Lock()


def _library_sort_key(name):
    import utils
    return utils.katakana_to_hiragana(name).replace('ゔ', 'う').lower().encode('shift_jis', errors='ignore')


def get_media_library(db_subs, db_epub):
    key = db_fingerprint(db_subs, db_epub)
    cached = _media_library_cached(key)
    if cached is not None:
        return cached
    with _MEDIA_LIBRARY_LOCK:
        cached = _media_library_cached(key)
        if cached is not None:
            return cached
        return _compute_media_library(db_subs, db_epub, key)


MEDIA_SECONDS_PER_GB = 3.2
_MEDIA_COMPUTE = {"started": None, "expected": None}


def _media_cache_path():
    return os.path.join(os.path.dirname(os.path.abspath(paths.subs_db())), "media_cache.json")


def _media_cache_read():
    try:
        import json
        with open(_media_cache_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _media_library_cached(key):
    if key in _MEDIA_LIBRARY:
        return _MEDIA_LIBRARY[key]
    data = _media_cache_read()
    if data and data.get("key") == repr(key) and isinstance(data.get("items"), list):
        _MEDIA_LIBRARY.clear()
        _MEDIA_LIBRARY[key] = data["items"]
        return data["items"]
    return None


def _db_bytes(*conns):
    total = 0
    for entry in db_fingerprint(*conns):
        if entry:
            total += sum(st[1] for st in entry[1:] if st)
    return total


def media_library_status(db_subs, db_epub):
    import time
    key = db_fingerprint(db_subs, db_epub)
    if _media_library_cached(key) is not None:
        return {"ready": True}
    if _MEDIA_COMPUTE["started"] is None and not _MEDIA_LIBRARY_LOCK.locked():
        threading.Thread(target=warm_media_library, daemon=True).start()
    started = _MEDIA_COMPUTE["started"] or time.monotonic()
    expected = _MEDIA_COMPUTE["expected"] or _media_expected_seconds(db_subs, db_epub)
    elapsed = time.monotonic() - started
    return {"ready": False, "elapsed": round(elapsed, 1),
            "remaining": round(max(expected - elapsed, 1.0), 1)}


def _media_expected_seconds(db_subs, db_epub):
    data = _media_cache_read() or {}
    rate = data.get("seconds_per_gb") or MEDIA_SECONDS_PER_GB
    return rate * _db_bytes(db_subs, db_epub) / 1024 ** 3


def warm_media_library():
    conns = []
    try:
        for path in (paths.subs_db(), paths.epub_db()):
            conns.append(sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                         if os.path.exists(path) else None)
        get_media_library(*conns)
    except Exception:
        pass
    finally:
        for c in conns:
            if c is not None:
                c.close()


def _compute_media_library(db_subs, db_epub, key):
    import time
    _MEDIA_COMPUTE["started"] = time.monotonic()
    _MEDIA_COMPUTE["expected"] = _media_expected_seconds(db_subs, db_epub)
    try:
        out = _count_media_library(db_subs, db_epub)
    finally:
        seconds = time.monotonic() - _MEDIA_COMPUTE["started"]
        _MEDIA_COMPUTE["started"] = _MEDIA_COMPUTE["expected"] = None
    _MEDIA_LIBRARY.clear()
    _MEDIA_LIBRARY[key] = out
    size = _db_bytes(db_subs, db_epub)
    try:
        import json
        path = _media_cache_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": repr(key), "items": out, "seconds": round(seconds, 2),
                       "seconds_per_gb": round(seconds / (size / 1024 ** 3), 3) if size else None},
                      f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass
    return out


def _count_media_library(db_subs, db_epub):
    items = {}
    if db_subs is not None:
        try:
            lines = dict(db_subs.execute(
                "SELECT source_id, COUNT(*) FROM subtitles GROUP BY source_id").fetchall())
            for sid, relpath in db_subs.execute("SELECT id, relpath FROM sources"):
                show = re.split(r"[\\/]", relpath)[0]
                it = items.setdefault(("subs", show), {
                    "media": "subs", "folder": show, "author": "", "episodes": 0, "lines": 0})
                it["episodes"] += 1
                it["lines"] += lines.get(sid, 0)
        except sqlite3.Error:
            pass
    if db_epub is not None:
        try:
            stats = {sid: (n, ch) for sid, n, ch in db_epub.execute(
                "SELECT source_id, COUNT(*), COUNT(DISTINCT file) FROM epubs GROUP BY source_id")}
            for sid, relpath, title, author in db_epub.execute(
                    "SELECT id, relpath, title, author FROM sources"):
                name = title or (relpath[:-5] if relpath.lower().endswith(".epub") else relpath)
                n, ch = stats.get(sid, (0, 0))
                it = items.setdefault(("epub", name), {
                    "media": "epub", "folder": name, "author": author or "", "episodes": 0, "lines": 0})
                it["episodes"] += ch
                it["lines"] += n
        except sqlite3.Error:
            pass
    return sorted(items.values(), key=lambda it: _library_sort_key(it["folder"]))


def media_page(items, media="all", needle="", offset=0, limit=0, folder=None):
    if folder is not None:
        rows = [it for it in items if it["folder"] == folder and media in ("all", it["media"])]
        return {"items": rows[:1], "total": len(rows[:1]), "has_more": False}
    needle = needle.strip().lower()
    rows = [it for it in items
            if media in ("all", it["media"])
            and (not needle or needle in it["folder"].lower() or needle in (it["author"] or "").lower())]
    page = rows[offset:offset + limit] if limit else rows[offset:]
    return {"items": page, "total": len(rows), "has_more": offset + len(page) < len(rows),
            "shows": sum(1 for it in items if it["media"] == "subs"),
            "books": sum(1 for it in items if it["media"] == "epub")}
