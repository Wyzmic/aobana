import os
import sys
import re
import sqlite3
import hashlib
import zipfile
import posixpath
import xml.etree.ElementTree as ET
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from sudachipy import tokenizer, dictionary
from datetime import datetime

import paths
BASE_DIR = paths.BASE_DIR
EPUB_ROOT_DIR = paths.books_dir()
DB_PATH = paths.epub_db()
PROGRESS = os.environ.get("AOBANA_PROGRESS") == "1"

tokenizer_obj = dictionary.Dictionary(dict="core").create()
mode = tokenizer.Tokenizer.SplitMode.A

from utils import (
    KANA_RE, KANJI_CHARS, KANJI_PATTERN,
    ALPHA_CHARS, ALPHA_PATTERN, RUBY_BASE_RE, BOOK_RUBY_RE,
    norm_relpath, write_tokenizer_meta, ruby_index_extras,
)

TIMESTAMP_SCENE_RE = re.compile(
    r'^\s*[-ー―‐―—\s]*[0-9０-９]+[\s:：][0-9０-９]+(?:[\s:：][0-9０-９]+)?\s*$'
)
ILLUSTRATION_PLACEHOLDER_RE = re.compile(
    r'^\s*[（(［\[【〔〈《「『]*\s*(?:挿絵|挿図|イラスト|口絵|図版|カラー口絵|口絵イラスト|挿画)\s*[）)］\]】〕〉》」』]*\s*$'
)

_EXCLUDED_LOG = []

from utils import convert_hw_katakana, EPUB_STR_REPLACEMENTS, katakana_to_hiragana

_RE_REPLACEMENTS = [
    (re.compile(r'＠ルビ.*?［(.+?)[｜|](.+?)］＠'), r'｜\1(\2)'),
    (re.compile(r'(?<![―—])[―—](?![―—])'), '――'),
    (re.compile(r'[・]{3,}'), '…'),
    (re.compile(r'…{2,}'), '…'),
    (re.compile(r'‥{2,}'), '…'),
    (re.compile(r'~~~'), '～～～'),
    (re.compile(r'~~'),  '～～'),
    (re.compile(r'~'),   '～'),
]

GENERIC_TOC_NAMES = {
    '表紙', '扉', '本文', '目次', 'GUIDE', 'NAVIGATION', 'START', 
    'COVER', 'CONTENTS', 'MAIN', '中扉', 'TABLE OF CONTENTS', 'LANDMARKS'
}

IN_FILE_CHAPTER_RE = re.compile(
    r'^(?:(?:第\s*)?[0-9０-９一二三四五六七八九十百千]+\s*[章話回篇幕首課節]|'
    r'ACT(?:\.|\s+)?(?:[0-9０-９\d]+|[IVXLCDM]+(?!\w))|'
    r'Chapter\s*(?:[0-9０-９\d]+|[IVXLCDM]+(?!\w))|'
    r'プロローグ|エピローグ|序章|終章|序幕|終幕|間奏|幕間|'
    r'Interlude|Prologue|Epilogue|Intro|Outro|'
    r'まえがき|あとがき|はじめに|おわりに|序|序文|跋|跋文|'
    r'解説|解題|凡例|年表|初出一覧|著者紹介|監修者紹介|訳者あとがき|著者あとがき|'
    r'主要参考文献|参考文献|用語集|索引|奥付|附録|付録|補章|追補|特別寄稿)'
    r'(?:.*)?$',
    re.IGNORECASE
)

IN_FILE_PART_RE = re.compile(
    r'^(?:(?:第\s*)?[0-9０-９一二三四五六七八九十百千IVX]+\s*[部巻篇]|'
    r'Part\s*(?:[0-9０-９\d]+|[IVXLCDM]+(?!\w))|PART\s*(?:[0-9０-９\d]+|[IVXLCDM]+(?!\w))|'
    r'上巻|中巻|下巻|前篇|後篇|本篇|別篇)'
    r'(?:.*)?$',
    re.IGNORECASE
)

STANDALONE_NON_PART_SECTIONS = {
    'まえがき', 'あとがき', 'はじめに', 'おわりに', '序', '序文', '跋', '跋文',
    '解説', '解題', '凡例', '年表', '初出一覧', '著者紹介', '監修者紹介',
    '訳者あとがき', '著者あとがき', '主要参考文献', '参考文献', '用語集', '索引',
    '奥付', '附録', '付録', '補章', '追補', '特別寄稿', 'ビジュアル・ギャラリー'
}

SUB_NUMBERING_RE = re.compile(
    r'^(?:[0-9０-９]+[\.\s　、・\-:：“"\'（\(]|'
    r'[0-9０-９]+\.[0-9０-９]+|'
    r'[一二三四五六七八九十]+[\s　、・\-:：“"\'（\(])'
)

def strip_ruby_markup(text: str) -> str:
    t = re.sub(r'｜?([^()\s　]+)\([^)]*\)', r'\1', text)
    return t.replace('｜', '').replace('|', '')

def clean_heading_label(text: str) -> str:
    if '｜' in text or '|' in text:
        parts = [clean_heading_label(p) for p in re.split(r'[｜|]', text)]
        return '｜'.join(p for p in parts if p)
    t = strip_ruby_markup(text.strip())
    t = t.replace('/', '／').replace('\\', '＼')
    t = t.replace('\u201c', '"').replace('\u201d', '"')
    t = t.replace('\u300e', '"').replace('\u300f', '"')
    t = t.replace('\u301d', '"').replace('\u301e', '"')
    t = t.replace('\u301f', '"')
    t = re.sub(r'[\s　]+', ' ', t)
    m = re.match(r'^((?:第\s*)?[0-9０-９一二三四五六七八九十百千IVX]+\s*[部巻篇章話回幕首課節])(?:\s*(.*))?$', t)
    if m:
        prefix = re.sub(r'\s+', '', m.group(1))
        rest = m.group(2).strip() if m.group(2) else ""
        return f"{prefix} {rest}".strip()
    return normalize_cjk_spacing(t)


def is_valid_heading_block(_tag_name: str, text: str) -> bool:
    clean_t = normalize_cjk_spacing(strip_ruby_markup(text.strip()))
    if not clean_t or len(clean_t) > 40:
        return False
    if TIMESTAMP_SCENE_RE.match(clean_t) or ILLUSTRATION_PLACEHOLDER_RE.match(clean_t):
        return False
    if clean_t.startswith(('「', '『', '（', '【', '※', '・', '…', '―')):
        return False
    if '。' in clean_t or '？' in clean_t or '！' in clean_t:
        return False
    if any(ind in clean_t for ind in ('の第', 'による', 'について', 'から', 'における', '──詩')):
        return False
    return bool(IN_FILE_CHAPTER_RE.match(clean_t) or IN_FILE_PART_RE.match(clean_t))


def format_merged_chapter_name(current_part: str, chapter_title: str) -> str:
    ch_clean = clean_heading_label(chapter_title)
    if not current_part:
        return ch_clean
    if TIMESTAMP_SCENE_RE.match(ch_clean) or ILLUSTRATION_PLACEHOLDER_RE.match(ch_clean):
        return clean_heading_label(current_part)
    part_clean = clean_heading_label(current_part)
    if ch_clean.startswith(part_clean):
        return ch_clean
    if ch_clean in STANDALONE_NON_PART_SECTIONS or any(ch_clean.startswith(s) for s in STANDALONE_NON_PART_SECTIONS):
        return ch_clean
    return f"{part_clean}｜{ch_clean}"

def is_part_divider(label: str) -> bool:
    return bool(IN_FILE_PART_RE.match(normalize_cjk_spacing(strip_ruby_markup(label.strip()))))

def is_main_chapter_heading(label: str) -> bool:
    clean = normalize_cjk_spacing(strip_ruby_markup(label.strip()))
    if TIMESTAMP_SCENE_RE.match(clean) or ILLUSTRATION_PLACEHOLDER_RE.match(clean):
        return False
    return bool(IN_FILE_CHAPTER_RE.match(clean) or IN_FILE_PART_RE.match(clean))

def is_structural_chapter_heading(label: str) -> bool:
    clean = normalize_cjk_spacing(strip_ruby_markup(label.strip()))
    if TIMESTAMP_SCENE_RE.match(clean) or ILLUSTRATION_PLACEHOLDER_RE.match(clean):
        return False
    if clean in STANDALONE_NON_PART_SECTIONS or any(clean.startswith(s) for s in STANDALONE_NON_PART_SECTIONS):
        return False
    return bool(IN_FILE_CHAPTER_RE.match(clean) or IN_FILE_PART_RE.match(clean))

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

VOLUME_MARKER_RE = re.compile(
    r'\s*[\(\（\[\［\【\〔〈《]\s*'
    r'(上|中|下|前|後|前編|中編|後編|上巻|中巻|下巻|[0-9０-９]+|[一二三四五六七八九十]+|第[0-9０-９一二三四五六七八九十]+巻)'
    r'\s*[\)\）\]\］\】\〕〉》]\s*$')

def normalize_cjk_spacing(text: str) -> str:
    t = text.strip()
    if not t:
        return ""
    
    t = re.sub(r'[ \t　]+', ' ', t).strip()
    tokens = t.split(' ')
    if len(tokens) <= 1:
        return t
        
    cjk_single_re = re.compile(r'^[\u4E00-\u9FFF\u3040-\u30FF\u3005\u30A0-\u30FF0-9０-９]$')
    
    new_tokens = []
    buf = []
    
    for tok in tokens:
        if cjk_single_re.match(tok):
            buf.append(tok)
            buf_str = ''.join(buf)
            if re.match(r'^第[一二三四五六七八九十百0-9０-９]+[部章巻節話篇]$', buf_str):
                new_tokens.append(buf_str)
                buf = []
        else:
            if buf:
                new_tokens.append(''.join(buf))
                buf = []
            new_tokens.append(tok)
            
    if buf:
        new_tokens.append(''.join(buf))
        
    return ' '.join(new_tokens).strip()

def clean_book_title(title: str) -> str:
    if not title:
        return ""
    t = title.strip()
    
    for sp in SERIES_PREFIXES:
        t = re.sub(sp, '', t, flags=re.IGNORECASE)
    t = VOLUME_MARKER_RE.sub(lambda m: ' ' + m.group(1), t)
        
    t = re.sub(r'[\(\（\[\［\【\〔][^\(\（\[\［\【\〔\)\）\]\］\】\〕]*(?:講談社文庫|講談社学術文庫|講談社現代新書|講談社文芸文庫|新潮文庫|文春文庫|中公文庫|中公新書|ちくま文庫|ちくま新書|ちくま学芸文庫|角川ソフィア文庫|角川文庫|岩波新書|岩波文庫|岩波少年文庫|扶桑社ＢＯＯＫＳ|扶桑社BOOKS|アヌーク出版|大学受験叢書|集英社文庫|電子特別版|２２世紀アート|P[\+＋]D\s*BOOKS)[^\(\（\[\［\【\〔\)\）\]\］\】\〕]*[\)\）\]\］\】\〕]', '', t, flags=re.IGNORECASE)
    t = re.sub(r'[\(\（\[\［\【\〔][^\(\（\[\［\【\〔\)\）\]\］\】\〕]*[\)\）\]\］\】\〕]', '', t)
    t = re.sub(r'\s*ビギナーズ・クラシックス\s*日本の古典.*$', '', t)
    t = re.sub(r'\s*古典現代語訳叢書.*$', '', t)
    t = re.sub(r'^\d+\s*新・古文入門', '新・古文入門', t)
    t = t.replace('/', '／')
    t = re.sub(r'[ \t　]+', ' ', t).strip()
    return normalize_cjk_spacing(t)

def extract_best_title_and_author(epub_path, opf_title, opf_author):
    fname = os.path.basename(epub_path)
    fname_clean = fname[:-5] if fname.lower().endswith('.epub') else fname
    
    file_author = ""
    file_title = ""
    m_bracket = re.match(r'^\[(.*?)\]\s*(.*)$', fname_clean)
    m_dash = re.match(r'^(.*?)\s*-\s*(.*)$', fname_clean)
    if m_bracket:
        file_author = m_bracket.group(1).strip()
        file_title = m_bracket.group(2).strip()
    elif m_dash:
        file_author = m_dash.group(1).strip()
        file_title = m_dash.group(2).strip()
    else:
        file_title = fname_clean

    author = opf_author or file_author
    if not author:
        author = file_author
        
    if not opf_title or re.search(r'^\d{4,}_|申請データ|draft|titlepage', opf_title, re.IGNORECASE):
        chosen_title = file_title
    else:
        cleaned_opf = clean_book_title(opf_title)
        cleaned_file = clean_book_title(file_title)
        chosen_title = cleaned_opf if cleaned_opf else cleaned_file
        
    chosen_title = clean_book_title(chosen_title)
    author = normalize_cjk_spacing(author)
    return author, chosen_title

def _postprocess_sentence(text: str) -> str:
    text = convert_hw_katakana(text)
    for src, dst in EPUB_STR_REPLACEMENTS:
        text = text.replace(src, dst)
    for pattern, dst in _RE_REPLACEMENTS:
        text = pattern.sub(dst, text)
    return text

GENERIC_EXACT_LOWER = {
    'image', 'img', 'figure', 'fig', 'picture', 'photo', 'illustration',
    'イラスト', '写真', '挿絵', 'ロゴ', 'logo', 'icon', 'spacer', 'dummy', 'cover'
}

def is_generic_alt(alt: str) -> bool:
    if not alt:
        return True
    a = alt.strip()
    a_low = a.lower()
    a_clean = re.sub(r'^[（(［\[【〔〈《「『\s]+|[）)］\]】〕〉》」』\s]+$', '', a_low)
    
    if a_low in GENERIC_EXACT_LOWER or a_clean in GENERIC_EXACT_LOWER:
        return True
        
    if re.match(r'^(?:fig|figure|image|img|photo)[\s_\-\.:\d]', a_low):
        return True
        
    if re.search(r'\.(?:png|jpg|jpeg|gif|webp|svg|bmp)$', a_low):
        return True
        
    if len(a) > 30:
        return True
        
    return False

GAIJI_MARK = '〓'
SPACER_MAX_BYTES = 512
IMG_BLOCK_TAGS = ['p', 'div', 'li', 'td', 'th', 'dt', 'dd', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                  'blockquote', 'figcaption']

def _has_text_before(img) -> bool:
    block = img.find_parent(IMG_BLOCK_TAGS)
    if block is None:
        return False
    for el in img.previous_elements:
        if el is block:
            return False
        if isinstance(el, str) and el.strip():
            return True
    return False

def clean_html_ruby_and_tags(soup, image_size=None):
    for tag in soup.find_all(['script', 'style', 'svg', 'audio', 'video']):
        tag.decompose()

    for img in soup.find_all('img'):
        alt = img.get('alt', '').strip()
        if is_generic_alt(alt):
            alt = ''

        if alt:
            img.replace_with(alt)
            continue
        size = image_size(img.get('src', '')) if image_size else None
        if size is not None and size <= SPACER_MAX_BYTES:
            img.decompose()
        elif img.find_parent('ruby') or _has_text_before(img):
            img.replace_with(GAIJI_MARK)
        else:
            img.decompose()

    for ruby in soup.find_all('ruby'):
        rt_tags = ruby.find_all('rt')
        furi_text = ''.join(rt.get_text(strip=True) for rt in rt_tags)
        for rt in rt_tags:
            rt.decompose()
        for rp in ruby.find_all('rp'):
            rp.decompose()
        base_text = ruby.get_text(strip=True)
        if furi_text and base_text:
            ruby.replace_with(f'｜{base_text}({furi_text})')
        elif base_text:
            ruby.replace_with(base_text)
        elif furi_text:
            ruby.replace_with(furi_text)
        else:
            ruby.decompose()

def split_japanese_sentences(text: str):
    sentences = []
    buf = []
    quote_depth = 0
    bracket_map = {'「': '」', '『': '』', '（': '）', '(': ')'}
    reverse_map = {v: k for k, v in bracket_map.items()}

    for char in text:
        buf.append(char)
        if char in bracket_map:
            quote_depth += 1
        elif char in reverse_map:
            if quote_depth > 0:
                quote_depth -= 1
        elif char in ('。', '！', '？', '!', '?') and quote_depth == 0:
            s = ''.join(buf).strip()
            if s:
                sentences.append(s)
            buf = []

    if buf:
        s = ''.join(buf).strip()
        if s:
            sentences.append(s)

    return sentences

def get_clean_text_for_mecab(line: str) -> str:
    return BOOK_RUBY_RE.sub(r'\1\2', line)

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


def _parse_nav_ol(ol, nav_dir, toc_map, depth=0, parent_label=None, has_any_main=True, last_main_label=None):
    for li in ol.find_all('li', recursive=False):
        a = li.find('a', recursive=False)
        if not a:
            continue
        raw_text = a.get_text()
        has_leading_space = raw_text.startswith((' ', '　', '\t'))
        href_raw = a.get('href', '').split('#')[0]
        lbl = clean_heading_label(raw_text.strip())
        if not href_raw or not lbl:
            continue
        res_h = posixpath.normpath(posixpath.join(nav_dir, href_raw))

        if TIMESTAMP_SCENE_RE.match(lbl) or ILLUSTRATION_PLACEHOLDER_RE.match(lbl):
            target = parent_label or last_main_label
            if target and res_h not in toc_map:
                toc_map[res_h] = target
            continue

        if depth == 0:
            is_structural = is_structural_chapter_heading(lbl)
            if has_leading_space and last_main_label and last_main_label.upper() not in GENERIC_TOC_NAMES and last_main_label not in STANDALONE_NON_PART_SECTIONS:
                merged = format_merged_chapter_name(last_main_label, lbl)
                if res_h not in toc_map:
                    toc_map[res_h] = merged
                effective_parent = last_main_label
            else:
                is_sub = has_any_main and not is_structural and SUB_NUMBERING_RE.match(lbl)
                if is_sub and last_main_label:
                    merged = format_merged_chapter_name(last_main_label, lbl)
                    if res_h not in toc_map:
                        toc_map[res_h] = merged
                    effective_parent = last_main_label
                else:
                    if res_h not in toc_map or (is_structural and not is_structural_chapter_heading(toc_map.get(res_h, ''))):
                        toc_map[res_h] = lbl
                    effective_parent = lbl
                    if is_structural or (not is_sub and not has_leading_space and lbl.upper() not in GENERIC_TOC_NAMES and lbl not in STANDALONE_NON_PART_SECTIONS):
                        last_main_label = lbl
        else:
            if has_any_main and parent_label and is_structural_chapter_heading(parent_label):
                merged = format_merged_chapter_name(parent_label, lbl)
                if res_h not in toc_map:
                    toc_map[res_h] = merged
                effective_parent = parent_label
            else:
                if res_h not in toc_map:
                    toc_map[res_h] = lbl
                effective_parent = lbl

        nested_ol = li.find('ol', recursive=False)
        if nested_ol:
            _parse_nav_ol(nested_ol, nav_dir, toc_map, depth + 1, effective_parent, has_any_main, last_main_label)


def _parse_ncx_navpoints(navpoints, ncx_dir, toc_map, depth=0, parent_label=None, has_any_main=True):
    for np in navpoints:
        navlabel = np.find('navlabel')
        text_el = navlabel.find('text') if navlabel else np.find('text')
        content_el = np.find('content')
        if not text_el or not content_el:
            continue
        lbl = clean_heading_label(text_el.get_text(strip=True))
        src = content_el.get('src', '').split('#')[0]
        if not src or not lbl:
            continue
        res_h = posixpath.normpath(posixpath.join(ncx_dir, src))

        if TIMESTAMP_SCENE_RE.match(lbl) or ILLUSTRATION_PLACEHOLDER_RE.match(lbl):
            if parent_label and res_h not in toc_map:
                toc_map[res_h] = parent_label
            continue

        if depth == 0:
            is_structural = is_structural_chapter_heading(lbl)
            if res_h not in toc_map or (is_structural and not is_structural_chapter_heading(toc_map.get(res_h, ''))):
                toc_map[res_h] = lbl
            effective_parent = lbl
        else:
            if has_any_main and parent_label and is_structural_chapter_heading(parent_label):
                merged = format_merged_chapter_name(parent_label, lbl)
                if res_h not in toc_map:
                    toc_map[res_h] = merged
                effective_parent = parent_label
            else:
                if res_h not in toc_map:
                    toc_map[res_h] = lbl
                effective_parent = lbl

        child_navpoints = np.find_all('navpoint', recursive=False)
        if child_navpoints:
            _parse_ncx_navpoints(child_navpoints, ncx_dir, toc_map, depth + 1, effective_parent, has_any_main)


def _apply_split_file_carry(toc_map, zf_namelist):
    SPLIT_RE = re.compile(r'^(.+?)_split_\d+(\.[^.]+)$')
    stem_to_label = {}
    for path, label in toc_map.items():
        stem_to_label[path] = label

    for f in zf_namelist:
        m = SPLIT_RE.match(posixpath.basename(f))
        if not m:
            continue
        stem_file = posixpath.join(posixpath.dirname(f), m.group(1) + m.group(2))
        stem_norm = posixpath.normpath(stem_file)
        if f not in toc_map and stem_norm in toc_map:
            toc_map[f] = toc_map[stem_norm]
        elif f not in toc_map and stem_file in toc_map:
            toc_map[f] = toc_map[stem_file]


def _parse_mokuji_page(zf, mokuji_path, mokuji_dir, toc_map):
    try:
        raw = zf.read(mokuji_path).decode('utf-8', errors='ignore')
        soup = BeautifulSoup(raw, 'html.parser')
        for a in soup.find_all('a', href=True):
            href_raw = a.get('href', '').split('#')[0]
            if not href_raw:
                continue
            lbl = clean_heading_label(a.get_text(strip=True))
            if not lbl or len(lbl) < 2 or len(lbl) > 50:
                continue
            if TIMESTAMP_SCENE_RE.match(lbl) or ILLUSTRATION_PLACEHOLDER_RE.match(lbl):
                continue
            if re.match(r'^[\d\s　ivxlcdmIVXLCDM]+$', lbl):
                continue
            res_h = posixpath.normpath(posixpath.join(mokuji_dir, href_raw))
            if res_h not in toc_map:
                toc_map[res_h] = lbl
                stem_base = posixpath.basename(res_h)
                stem_dir = posixpath.dirname(res_h)
                stem_no_ext = posixpath.splitext(stem_base)[0]
                for candidate in [f for f in zf.namelist()
                                   if posixpath.dirname(f) == stem_dir
                                   and posixpath.basename(f).startswith(stem_no_ext + '_split_')]:
                    if candidate not in toc_map:
                        toc_map[candidate] = lbl
    except Exception as e:
        print(f"Error parsing 目次 page {mokuji_path}: {e}")


def extract_toc_map(zf, opf, content_dir):
    toc_map = {}

    if opf is None:
        nav_candidates = (
            [n for n in zf.namelist() if n.endswith(('navigation-documents.xhtml', 'nav.xhtml'))]
            or [n for n in zf.namelist() if n.endswith(('toc.xhtml', 'toc.ncx'))]
        )
        if nav_candidates:
            nav_full = nav_candidates[0]
            nav_dir = posixpath.dirname(nav_full)
            try:
                soup = BeautifulSoup(zf.read(nav_full).decode('utf-8', errors='ignore'), 'html.parser')
                nav_tag = (soup.find('nav', attrs={'epub:type': 'toc'})
                           or soup.find('nav', id='toc')
                           or soup.find('nav'))
                if nav_tag:
                    root_ol = nav_tag.find('ol')
                    if root_ol:
                        all_labels = [a.get_text(strip=True) for a in nav_tag.find_all('a')]
                        has_any_main = any(is_structural_chapter_heading(l) for l in all_labels)
                        _parse_nav_ol(root_ol, nav_dir, toc_map, depth=0, parent_label=None, has_any_main=has_any_main)
            except Exception as e:
                print(f"Error parsing direct Nav TOC: {e}")
        _apply_split_file_carry(toc_map, zf.namelist())
        return toc_map

    manifest = {item.attrib['id']: item.attrib for item in opf.findall('.//{*}manifest/{*}item')}

    nav_item = next((item for item in manifest.values()
                     if 'properties' in item and 'nav' in item['properties'].split()), None)
    if nav_item:
        nav_href = nav_item['href']
        nav_full = posixpath.normpath(posixpath.join(content_dir, nav_href)) if content_dir else nav_href
        nav_dir = posixpath.dirname(nav_full)
        try:
            raw_nav = zf.read(nav_full).decode('utf-8', errors='ignore')
            soup = BeautifulSoup(raw_nav, 'html.parser')
            nav_tag = (soup.find('nav', attrs={'epub:type': 'toc'})
                       or soup.find('nav', id='toc')
                       or soup.find('nav'))
            if nav_tag:
                root_ol = nav_tag.find('ol')
                if root_ol:
                    all_labels = [a.get_text(strip=True) for a in nav_tag.find_all('a')]
                    has_any_main = any(is_structural_chapter_heading(l) for l in all_labels)
                    _parse_nav_ol(root_ol, nav_dir, toc_map, depth=0, parent_label=None, has_any_main=has_any_main)
        except Exception as e:
            print(f"Error parsing Nav TOC {nav_full}: {e}")

    if not toc_map:
        ncx_item = next((item for item in manifest.values()
                         if item.get('media-type') == 'application/x-dtbncx+xml'
                         or item.get('id') == 'ncx'), None)
        if ncx_item:
            ncx_href = ncx_item['href']
            ncx_full = posixpath.normpath(posixpath.join(content_dir, ncx_href)) if content_dir else ncx_href
            ncx_dir = posixpath.dirname(ncx_full)
            try:
                raw_ncx = zf.read(ncx_full).decode('utf-8', errors='ignore')
                soup = BeautifulSoup(raw_ncx, 'html.parser')
                nav_map = soup.find('navmap')
                if nav_map:
                    top_navpoints = nav_map.find_all('navpoint', recursive=False)
                    all_labels = [np.find('text').get_text(strip=True) for np in nav_map.find_all('navpoint') if np.find('text')]
                    has_any_main = any(is_structural_chapter_heading(l) for l in all_labels)
                    _parse_ncx_navpoints(top_navpoints, ncx_dir, toc_map, depth=0, parent_label=None, has_any_main=has_any_main)
            except Exception as e:
                print(f"Error parsing NCX TOC {ncx_full}: {e}")

    _apply_split_file_carry(toc_map, zf.namelist())

    mokuji_path = None
    mokuji_dir = ""
    for path, label in toc_map.items():
        if label in ('目次', '目次ページ', 'Contents', 'Table of Contents'):
            mokuji_path = path
            mokuji_dir = posixpath.dirname(path)
            break
    if mokuji_path:
        try:
            candidate_paths = [mokuji_path]
            if content_dir and not mokuji_path.startswith(content_dir):
                candidate_paths.append(posixpath.join(content_dir, mokuji_path))
            for cp in candidate_paths:
                if cp in zf.namelist():
                    _parse_mokuji_page(zf, cp, mokuji_dir or posixpath.dirname(cp), toc_map)
                    break
        except Exception as e:
            print(f"Error in 目次 supplementary parse: {e}")

    return toc_map


def is_tautological_chapter(ch_title: str, sentences: list) -> bool:
    if len(sentences) == 0:
        return True
    if len(sentences) == 1:
        s = sentences[0]
        s_clean = re.sub(r'[\s　]+', '', strip_ruby_markup(s))
        ch_clean = re.sub(r'[\s　]+', '', strip_ruby_markup(ch_title))
        sub_ch_clean = ch_clean.split('｜')[-1]
        if s_clean == ch_clean or s_clean == sub_ch_clean or ch_clean.endswith(s_clean) or s_clean.endswith(ch_clean):
            return True
    return False


def extract_epub_content(epub_path):
    with zipfile.ZipFile(epub_path, 'r') as zf:
        opf = None
        content_dir = ""
        rootfile_path = ""
        try:
            container_xml = zf.read('META-INF/container.xml')
            container = ET.fromstring(container_xml)
            rootfile_path = container.find('.//{*}rootfile').attrib['full-path']
            opf_data = zf.read(rootfile_path)
            opf = ET.fromstring(opf_data)
            content_dir = posixpath.dirname(rootfile_path)
        except Exception:
            opf_files = [n for n in zf.namelist() if n.endswith('.opf')]
            if opf_files:
                rootfile_path = opf_files[0]
                opf_data = zf.read(rootfile_path)
                opf = ET.fromstring(opf_data)
                content_dir = posixpath.dirname(rootfile_path)

        if opf is not None:
            title_elem = opf.find('.//{*}title')
            raw_title = title_elem.text.strip() if (title_elem is not None and title_elem.text) else ""
            creator_elem = opf.find('.//{*}creator')
            raw_author = creator_elem.text.strip() if (creator_elem is not None and creator_elem.text) else ""
            author, title = extract_best_title_and_author(epub_path, raw_title, raw_author)
            manifest = {item.attrib['id']: item.attrib['href'] for item in opf.findall('.//{*}manifest/{*}item')}
            spine = [itemref.attrib['idref'] for itemref in opf.findall('.//{*}spine/{*}itemref')]
            spine_files = [manifest.get(idref) for idref in spine if manifest.get(idref)]
        else:
            author, title = extract_best_title_and_author(epub_path, "", "")
            spine_files = [n for n in zf.namelist() if n.endswith(('.xhtml', '.html')) and not n.endswith(('navigation-documents.xhtml', 'nav.xhtml', 'toc.xhtml'))]
            def spine_sort_key(p):
                base = posixpath.basename(p).lower()
                if 'cover' in base: return (0, 0)
                if 'title' in base: return (0, 1)
                if 'caution' in base: return (0, 2)
                m = re.search(r'(\d+)', base)
                if m: return (1, int(m.group(1)))
                if 'atogaki' in base or 'afterword' in base: return (2, 0)
                if 'author' in base: return (2, 1)
                if 'colophon' in base: return (2, 2)
                return (1, 999)
            spine_files.sort(key=spine_sort_key)

        toc_map = extract_toc_map(zf, opf, content_dir)
        has_authoritative_toc = len(toc_map) > 0

        chapters_dict = {}
        current_part = ""
        current_chapter_title = title
        pure_title = re.sub(r'[\s　]+', '', strip_ruby_markup(title))
        pure_author = re.sub(r'[\s　]+', '', strip_ruby_markup(author))

        for href in spine_files:
            if not href:
                continue

            matched_toc_label = None
            if href in toc_map:
                matched_toc_label = toc_map[href]
            else:
                full_h = posixpath.join(content_dir, href) if content_dir else href
                if full_h in toc_map:
                    matched_toc_label = toc_map[full_h]

            if matched_toc_label:
                toc_label = clean_heading_label(matched_toc_label)
                norm_toc = normalize_cjk_spacing(toc_label)
                if IN_FILE_PART_RE.match(norm_toc):
                    current_part = toc_label
                    current_chapter_title = toc_label
                else:
                    if current_part and not (toc_label in STANDALONE_NON_PART_SECTIONS or any(toc_label.startswith(s) for s in STANDALONE_NON_PART_SECTIONS)):
                        current_chapter_title = format_merged_chapter_name(current_part, toc_label)
                    else:
                        if toc_label in STANDALONE_NON_PART_SECTIONS or any(toc_label.startswith(s) for s in STANDALONE_NON_PART_SECTIONS):
                            current_part = ""
                        current_chapter_title = toc_label

            full_path = href if (href in zf.namelist()) else (posixpath.join(content_dir, href) if content_dir else href)
            try:
                raw_xhtml = zf.read(full_path).decode('utf-8', errors='ignore')
            except Exception:
                continue

            soup = BeautifulSoup(raw_xhtml, 'html.parser')
            page_dir = posixpath.dirname(full_path)
            def image_size(src, page_dir=page_dir):
                try:
                    return zf.getinfo(posixpath.normpath(posixpath.join(page_dir, src))).file_size
                except (KeyError, ValueError):
                    return None
            clean_html_ruby_and_tags(soup, image_size)

            blocks = soup.find_all(lambda tag: tag.name in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6') or (tag.name in ('li', 'dt', 'dd', 'blockquote', 'div') and not tag.find(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div', 'ul', 'ol', 'blockquote', 'li', 'dt', 'dd'])))
            lines = [b.get_text(strip=True) for b in blocks if b.get_text(strip=True)]
            unique_lines = list(dict.fromkeys(lines))
            total_chars = sum(len(l) for l in unique_lines)

            if len(unique_lines) == 0:
                continue

            if len(unique_lines) <= 2 and total_chars <= 50:
                clean_first = normalize_cjk_spacing(strip_ruby_markup(unique_lines[0]))
                pure_first = re.sub(r'[\s　]+', '', clean_first)
                if IN_FILE_PART_RE.match(clean_first):
                    current_part = clean_heading_label(unique_lines[0])
                    continue
                if pure_first in (pure_title, pure_author, f'{pure_author}{pure_title}', '表紙', '扉', '目次', 'Cover', 'Guide', 'Navigation', 'Start', 'Contents') or any(kw in clean_first for kw in ['刊行された', '発行所', '禁無断転載', '初出']):
                    continue

            for b in blocks:
                block_text = b.get_text(strip=True)
                if not block_text:
                    continue

                clean_b_text = normalize_cjk_spacing(strip_ruby_markup(block_text))

                if is_valid_heading_block(b.name, block_text):
                    if IN_FILE_PART_RE.match(clean_b_text):
                        if not has_authoritative_toc:
                            current_part = clean_heading_label(block_text)
                        continue
                    elif IN_FILE_CHAPTER_RE.match(clean_b_text):
                        ch_name = clean_heading_label(block_text)
                        pure_ch = re.sub(r'[\s　]+', '', ch_name)
                        if ch_name.upper() not in GENERIC_TOC_NAMES and pure_ch != pure_title:
                            if has_authoritative_toc and (current_chapter_title == ch_name or current_chapter_title.endswith(f"｜{ch_name}") or current_chapter_title.startswith(ch_name)):
                                pass
                            else:
                                if current_part and not (ch_name in STANDALONE_NON_PART_SECTIONS or any(ch_name.startswith(s) for s in STANDALONE_NON_PART_SECTIONS)):
                                    current_chapter_title = format_merged_chapter_name(current_part, ch_name)
                                else:
                                    if ch_name in STANDALONE_NON_PART_SECTIONS or any(ch_name.startswith(s) for s in STANDALONE_NON_PART_SECTIONS):
                                        current_part = ""
                                    current_chapter_title = ch_name
                                continue

                for s in split_japanese_sentences(block_text):
                    s_clean = _postprocess_sentence(s)
                    if not s_clean:
                        continue

                    if TIMESTAMP_SCENE_RE.match(s_clean):
                        _EXCLUDED_LOG.append((title, "TIMESTAMP_SCENE", s_clean))
                        continue

                    if ILLUSTRATION_PLACEHOLDER_RE.match(s_clean):
                        _EXCLUDED_LOG.append((title, "ILLUSTRATION_PLACEHOLDER", s_clean))
                        continue

                    if current_chapter_title not in chapters_dict:
                        chapters_dict[current_chapter_title] = []

                    if chapters_dict[current_chapter_title]:
                        prev_clean = get_clean_text_for_mecab(chapters_dict[current_chapter_title][-1])
                        curr_clean = get_clean_text_for_mecab(s_clean)
                        if prev_clean == curr_clean:
                            if '｜' in s_clean and '｜' not in chapters_dict[current_chapter_title][-1]:
                                chapters_dict[current_chapter_title][-1] = s_clean
                            continue

                    chapters_dict[current_chapter_title].append(s_clean)

        chapters_data = []
        valid_idx = 1
        for ch_name, s_list in chapters_dict.items():
            if is_tautological_chapter(ch_name, s_list):
                _EXCLUDED_LOG.append((title, "TAUTOLOGICAL_EMPTY_CHAPTER", ch_name))
                continue
            chapters_data.append({
                'order_idx': valid_idx,
                'chapter_title': ch_name,
                'sentences': s_list
            })
            valid_idx += 1

        return author, title, chapters_data

def run_epub_indexer(force=False):
    print(f"Starting EPUB indexer on {EPUB_ROOT_DIR} (force={force})...")
    _EXCLUDED_LOG.clear()
    if EPUB_ROOT_DIR is None:
        print("ROOT_NOT_SET epub")
        print("No books folder is set (Library tab). Nothing was changed.")
        return
    if not os.path.isdir(EPUB_ROOT_DIR):
        print(f"ROOT_MISSING {EPUB_ROOT_DIR}")
        print("EPUB indexing aborted: the books folder does not exist. Nothing was changed.")
        return
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        if force:
            print("Force rebuild requested. Dropping existing tables in epub.db...")
            conn.execute("DROP TABLE IF EXISTS epubs")
            conn.execute("DROP TABLE IF EXISTS sources")
            conn.commit()

        conn.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relpath TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS epubs USING fts5(
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

        epub_files = []
        ignored = 0
        failed = 0
        for root, _, files in os.walk(EPUB_ROOT_DIR):
            for f in files:
                if f.lower().endswith('.epub') and not f.startswith('.'):
                    full_p = os.path.join(root, f)
                    if full_p not in epub_files:
                        epub_files.append(full_p)
                elif not f.startswith('.'):
                    ignored += 1

        print(f"Found {len(epub_files)} .epub file(s) in {EPUB_ROOT_DIR}.")
        if ignored:
            print(f"IGNORED_OTHER {ignored}")
        if PROGRESS:
            print(f"TOTAL {len(epub_files)}", flush=True)

        for n, epub_path in enumerate(epub_files, 1):
            relpath = norm_relpath(epub_path, EPUB_ROOT_DIR)
            current_disk_files.add(relpath)
            if PROGRESS:
                print(f"PROGRESS {n}/{len(epub_files)} {relpath}", flush=True)

            file_hash = compute_file_hash(epub_path)

            if relpath in existing_files:
                if existing_files[relpath]['hash'] == file_hash:
                    skipped += 1
                    continue
                else:
                    source_id = existing_files[relpath]['id']
                    conn.execute("DELETE FROM epubs WHERE source_id = ?", (source_id,))
                    conn.execute("UPDATE sources SET file_hash = ?, indexed_at = ? WHERE id = ?",
                                 (file_hash, datetime.now(), source_id))
            else:
                cur = conn.execute("INSERT INTO sources (relpath, file_hash, title, author) VALUES (?, ?, '', '')",
                                   (relpath, file_hash))
                source_id = cur.lastrowid

            try:
                author, title, chapters_data = extract_epub_content(epub_path)
            except Exception as e:
                conn.execute("DELETE FROM epubs WHERE source_id = ?", (source_id,))
                conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
                failed += 1
                print(f"FAILED {relpath}: {type(e).__name__}: {e}")
                continue

            new_or_updated += 1
            conn.execute("UPDATE sources SET title = ?, author = ? WHERE id = ?", (title, author, source_id))

            folder_name = title

            rows_to_insert = []
            for ch in chapters_data:
                order_num = ch['order_idx']
                ch_name = ch['chapter_title']
                file_key = f"{folder_name}\\{order_num:02d}.{ch_name}"

                for line in ch['sentences']:
                    clean_text = get_clean_text_for_mecab(line)
                    base_forms, readings = analyze_with_sudachi(clean_text)

                    base_forms, readings = ruby_index_extras(line, BOOK_RUBY_RE, base_forms, readings)

                    rows_to_insert.append((source_id, file_key, line, clean_text, base_forms, readings))

            conn.executemany(
                "INSERT INTO epubs(source_id, file, line, clean_text, base_forms, readings) VALUES (?, ?, ?, ?, ?, ?);",
                rows_to_insert
            )
            print(f"Indexed EPUB: {title} by {author} ({len(rows_to_insert)} sentences in {len(chapters_data)} chapters)")

        deleted_files = set(existing_files.keys()) - current_disk_files
        for relpath in deleted_files:
            source_id = existing_files[relpath]['id']
            conn.execute("DELETE FROM epubs WHERE source_id = ?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            print(f"Removed deleted EPUB: {relpath}")

        for t, n, rels in conn.execute(
                "SELECT title, COUNT(*), group_concat(relpath, ' | ') FROM sources "
                "GROUP BY title HAVING COUNT(*) > 1"):
            print(f"WARNING: {n} books share the title {t}: {rels}")

        ident = write_tokenizer_meta(conn, new_or_updated)
        if new_or_updated:
            print(f"Tokenizer: SudachiDict-core {ident['sudachidict_version']} "
                  f"({ident['dictionary_format']}), SudachiPy {ident['sudachipy_version']}, "
                  f"system.dic {ident['system_dic_sha256'][:12]}")

        conn.commit()

    if _EXCLUDED_LOG:
        log_path = os.path.join(paths.logs_dir(), "excluded_epub_lines.txt")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log_f:
            for book, reason, text in _EXCLUDED_LOG:
                log_f.write(f"[{book}] [{reason}] {text}\n")

    print(f"EPUB Indexing complete! Skipped {skipped} unchanged files. Indexed {new_or_updated} new/updated books. Removed {len(deleted_files)} deleted."
          + (f" Failed {failed}." if failed else ""))

if __name__ == "__main__":
    import sys
    force_flag = "--force" in sys.argv
    run_epub_indexer(force=force_flag)
