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

    for w in words_data:
        surface = w['surface']
        base = token_base(w)
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


def format_episode_title(relpath):
    import utils
    parts = re.split(r"[\\/]", relpath)
    folder_name = parts[0]
    filename = parts[-1]
    if filename.endswith('.srt'): filename = filename[:-4]
    season_ep = ''
    title = ''
    match_se = re.search(r'\bS(\d+)E(\d+)\b', filename, re.IGNORECASE)
    if match_se:
        s = int(match_se.group(1))
        e = int(match_se.group(2))
        season_ep = f'{to_fullwidth(s)}期{to_fullwidth(e, 2)}話'
        title_part = filename[match_se.end():]
        title_part = re.split(r'\.(?:WEBRip|WEB-DL|BD|1080p|720p)', title_part, flags=re.IGNORECASE)[0]
        title_part = title_part.strip(' ._')
        
        m_ep = re.match(r'^([#＃第])?([一二三四五六七八九十百0-9０-９]+)(話|幕|首)?[\.\s]*', title_part)
        if m_ep:
            ep_num_str = m_ep.group(2)
            rest = title_part[m_ep.end():].strip(' ._')
            if re.match(r'^[0-9０-９]+$', ep_num_str):
                ep_num = int(ep_num_str)
                if ep_num == e:
                    title_part = rest
                else:
                    if not rest:
                        title_part = f"{to_fullwidth(ep_num)}話"
                    else:
                        title_part = f"{to_fullwidth(ep_num)}話｜{rest}"
            else:
                title_part = rest
                
        title_part = re.sub(r'^(?:最終話|最終章(?:\.前編|\.後編)?)[\.\s]*', '', title_part)
        title_part = re.sub(r'^【.*?】', '', title_part)
        title_part = re.split(r'(?:WEBRip|WEB-DL|BD|1080p|720p)', title_part, flags=re.IGNORECASE)[0]
        title = title_part.strip(' ._')
        title = title.replace('.', ' ').replace('_', ' ')
    else:
        s_str = ''
        match_s = re.search(r'([０-９0-9]+)(?:st|nd|rd|th)\b', filename, re.IGNORECASE)
        if match_s:
            trans = str.maketrans('０１２３４５６７８９', '0123456789')
            s = int(match_s.group(1).translate(trans))
            s_str = f'{to_fullwidth(s)}期'
            filename = filename[:match_s.start()] + filename[match_s.end():]

        match_jp_se = re.search(r'第([０-９0-9]+)(?:シリーズ|期)(.*?)[（\(]([０-９0-9]{2,3})[）\)]', filename)
        if match_jp_se:
            s = int(match_jp_se.group(1))
            extra = match_jp_se.group(2).strip()
            e = int(match_jp_se.group(3))
            season_ep = f'{to_fullwidth(s)}期{to_fullwidth(e, 2)}話'
            title_part = filename[match_jp_se.end():].strip()
            title_part = re.split(r'(?: - | \[)', title_part)[0]
            title = title_part.strip()
            if extra:
                folder_name += extra
        else:
            match_ova = re.search(r'(OVA\s+.*?)(?:\s*\(|$)', filename, re.IGNORECASE)
            if match_ova:
                title = match_ova.group(1).strip()
            match_ep = re.search(r'(?: - |[#＃]|第|\[|(?i:\bEP?)|[（\(])(\d{1,3})(?:([～\-])(\d{1,3}))?(?:話| |\.|$|\]|[）\)])', filename)
            if match_ep:
                e1 = int(match_ep.group(1))
                if match_ep.group(2):
                    e2 = int(match_ep.group(3))
                    season_ep = f'{to_fullwidth(e1, 2)}{match_ep.group(2)}{to_fullwidth(e2, 2)}話'
                else:
                    season_ep = f'{to_fullwidth(e1, 2)}話'
                    
                if s_str: season_ep = s_str + season_ep
                
                title_part = filename[match_ep.end():].strip(' ._-')
                if not title_part:
                    title_part = filename[:match_ep.start()].strip(' ._-')
                title_part = re.split(r'(?: - | \[)', title_part)[0]
                title = title_part.strip()
                
            if not match_ep:
                match_word_ep = re.search(r'\b(?:Karte|Stage|Phase|Episode|ACT)\s+(\d{1,3}|I{1,3}|IV|V|VI{1,3}|IX|X{1,2}|XI{1,2})\b', filename, re.IGNORECASE)
                if match_word_ep:
                    val_str = match_word_ep.group(1).upper()
                    if val_str.isdigit():
                        e1 = int(val_str)
                    else:
                        roman_map = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10, 'XI': 11, 'XII': 12}
                        e1 = roman_map.get(val_str, 1)
                        
                    season_ep = f'{to_fullwidth(e1, 2)}話'
                    if s_str: season_ep = s_str + season_ep
                    
                    title_part = filename[match_word_ep.end():].strip(' ._-')
                    title_part = re.sub(r'^OVA\s+', '', title_part, flags=re.IGNORECASE)
                    title_part = re.split(r'(?: - | \[)', title_part)[0]
                    title = title_part.strip()
                    
            if not title and not season_ep:
                title = filename
    if title:
        title = re.sub(r'[\[\(][^\]\)]*?(?:1080p|720p|FHD|HEVC|AAC|x264|WEBRip|WEB-DL|BD|FFF|Hi10|JPN|TX|AMZN|NF|WEB|H\.264|DDP|SubtitleTools|Sakurato)[^\]\)]*?[\]\)]', '', title, flags=re.IGNORECASE).strip()
        title = re.split(r'(?: - | \[|\()?(?:DUAL|1080p|720p|WEB\b|WEBRip|WEB-DL|BD|HEVC|Netflix|Amazon|Hulu)', title, flags=re.IGNORECASE)[0].strip()
        title = re.sub(r'\[字\]', '', title).strip()
        title = re.sub(r'\[映\]', '', title).strip()
        title = title.strip(' ._[]()')
        title = re.sub(r'\s+-\s*$', '', title)
        title = re.sub(r'^\s*-\s+', '', title)
        title = title.replace('.', ' ').replace('_', ' ')
        
        title = re.sub(r'(?i)\bChapter\s*\d+\b', '', title)
        title = re.sub(r'(?i)\s+(?:ja|jp)$', '', title)
        title = re.sub(r'\b\d{3,5}-(DLC)', r'\1', title, flags=re.IGNORECASE)
        
        if title:
            title = utils.convert_hw_katakana(title)
            for old, new in utils.SUBS_STR_REPLACEMENTS:
                title = title.replace(old, new)
            title = title.replace('(', '（').replace(')', '）')
            
            if title:
                return f'{folder_name}｜{title}｜{season_ep}' if season_ep else f'{folder_name}｜{title}'
    if season_ep:
        return f'{folder_name}｜{season_ep}'
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

SERIES_PREFIXES = [
    r'^國體詳解双書\s*',
    r'^ＮＨＫ出版\s*学びのきほん\s*',
    r'^NHK出版\s*学びのきほん\s*',
    r'^ＮＨＫ\s*「?１００分ｄｅ名著」?\s*ブックス?\s*',
    r'^NHK\s*「?100分de名著」?\s*ブックス?\s*',
    r'^別冊ＮＨＫ１００分de名著\s*',
    r'^別冊NHK100分de名著\s*',
    r'^岩波少年文庫\s*\d*\s*',
    r'^P[\+＋]D\s*BOOKS\s*',
    r'^古典現代語訳叢書\s*',
    r'^ことば選び辞典\s*',
    r'^古典文学の世界\s*',
    r'^日本語シリーズ\s*',
    r'^桑原岩雄著作復刻選\s*',
]

def clean_book_title(title: str) -> str:
    if not title:
        return ""
    t = title.strip()
    for sp in SERIES_PREFIXES:
        t = re.sub(sp, '', t, flags=re.IGNORECASE)
    t = re.sub(r'[\(\（\[\［\【\〔][^\(\（\[\［\【\〔\)\）\]\］\】\〕]*(?:講談社文庫|講談社学術文庫|講談社現代新書|講談社文芸文庫|新潮文庫|文春文庫|中公文庫|中公新書|ちくま文庫|ちくま新書|ちくま学芸文庫|角川ソフィア文庫|角川文庫|岩波新書|岩波文庫|岩波少年文庫|扶桑社ＢＯＯＫＳ|扶桑社BOOKS|アヌーク出版|大学受験叢書|集英社文庫|電子特別版|２２世紀アート|P[\+＋]D\s*BOOKS)[^\(\（\[\［\【\〔\)\）\]\］\】\〕]*[\)\）\]\］\】\〕]', '', t, flags=re.IGNORECASE)
    t = re.sub(r'[\(\（\[\［\【\〔][^\(\（\[\［\【\〔\)\）\]\］\】\〕]*[\)\）\]\］\】\〕]', '', t)
    t = re.sub(r'\s*ビギナーズ・クラシックス\s*日本の古典.*$', '', t)
    t = re.sub(r'\s*古典現代語訳叢書.*$', '', t)
    t = re.sub(r'^\d+\s*新・古文入門', '新・古文入門', t)
    t = re.sub(r'[ \t　]+', ' ', t).strip()
    return t

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
            
    book_title = clean_book_title(book_title)
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

    if ch_clean in (book_title, '本文', '本編', ''):
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


def get_search_results(db, q, folders=None, sort='recommend', folder=None, exact=False, abort_flag=None, limit=500, offset=0, file=None, db_epub=None, media='all', seed=None):
    neg_info = []
    clean_q = ""
    content_bases = []
    readings = []
    cache_key = cached = cached_page = None

    if isinstance(db, (tuple, list)):
        db_subs = db[0] if len(db) > 0 else None
        if len(db) > 1 and db_epub is None:
            db_epub = db[1]
    else:
        db_subs = db

    if db_epub is not None:
        load_book_authors(db_epub)

    targets = []
    if media in ('all', 'subs') and db_subs is not None:
        targets.append(('subtitles', db_subs, 'subs'))
    if media in ('all', 'epub') and db_epub is not None:
        targets.append(('epubs', db_epub, 'epub'))
    if not targets and db_subs is not None:
        targets.append(('subtitles', db_subs, 'subs'))

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
        cached_page = _result_cache_page(cached, offset, limit, targets) if cached is not None else None
        if cached_page is None:
            cached = None

        for table_name, db_conn, m_type in (targets if cached is None else ()):
            if db_conn is None: continue
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

    results = []
    folder_counts = {}
    global_total = 0
    valid_results = []
    
    for i, row_dict in enumerate(all_candidate_rows):
        if abort_flag and abort_flag[0] and i % 100 == 0:
            return [], {}, 0, [], False
            
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
    if key in _MEDIA_LIBRARY:
        return _MEDIA_LIBRARY[key]
    with _MEDIA_LIBRARY_LOCK:
        if key in _MEDIA_LIBRARY:
            return _MEDIA_LIBRARY[key]
        return _compute_media_library(db_subs, db_epub, key)


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
    out = sorted(items.values(), key=lambda it: _library_sort_key(it["folder"]))
    _MEDIA_LIBRARY.clear()
    _MEDIA_LIBRARY[key] = out
    return out
