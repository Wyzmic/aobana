import hashlib
import os
import re
import unicodedata
from datetime import datetime

_HW_DAKU_MAP = {
    'ｶﾞ': 'ガ', 'ｷﾞ': 'ギ', 'ｸﾞ': 'グ', 'ｹﾞ': 'ゲ', 'ｺﾞ': 'ゴ',
    'ｻﾞ': 'ザ', 'ｼﾞ': 'ジ', 'ｽﾞ': 'ズ', 'ｾﾞ': 'ゼ', 'ｿﾞ': 'ゾ',
    'ﾀﾞ': 'ダ', 'ﾁﾞ': 'ヂ', 'ﾂﾞ': 'ヅ', 'ﾃﾞ': 'デ', 'ﾄﾞ': 'ド',
    'ﾊﾞ': 'バ', 'ﾋﾞ': 'ビ', 'ﾌﾞ': 'ブ', 'ﾍﾞ': 'ベ', 'ﾎﾞ': 'ボ',
    'ﾊﾟ': 'パ', 'ﾋﾟ': 'ピ', 'ﾌﾟ': 'プ', 'ﾍﾟ': 'ペ', 'ﾎﾟ': 'ポ',
    'ｳﾞ': 'ヴ'
}

_HW_MAP = str.maketrans({
    'ｧ': 'ァ', 'ｱ': 'ア', 'ｨ': 'ィ', 'ｲ': 'イ', 'ｩ': 'ゥ', 'ｳ': 'ウ',
    'ｪ': 'ェ', 'ｴ': 'エ', 'ｫ': 'ォ', 'ｵ': 'オ', 'ｶ': 'カ', 'ｷ': 'キ',
    'ｸ': 'ク', 'ｹ': 'ケ', 'ｺ': 'コ', 'ｻ': 'サ', 'ｼ': 'シ', 'ｽ': 'ス',
    'ｾ': 'セ', 'ｿ': 'ソ', 'ﾀ': 'タ', 'ﾁ': 'チ', 'ｯ': 'ッ', 'ﾂ': 'ツ',
    'ﾃ': 'テ', 'ﾄ': 'ト', 'ﾅ': 'ナ', 'ﾆ': 'ニ', 'ﾇ': 'ヌ', 'ﾈ': 'ネ',
    'ﾉ': 'ノ', 'ﾊ': 'ハ', 'ﾋ': 'ヒ', 'ﾌ': 'フ', 'ﾍ': 'ヘ', 'ﾎ': 'ホ',
    'ﾏ': 'マ', 'ﾐ': 'ミ', 'ﾑ': 'ム', 'ﾒ': 'メ', 'ﾓ': 'モ', 'ｬ': 'ャ',
    'ﾔ': 'ヤ', 'ｭ': 'ュ', 'ﾕ': 'ユ', 'ｮ': 'ョ', 'ﾖ': 'ヨ', 'ﾗ': 'ラ',
    'ﾘ': 'リ', 'ﾙ': 'ル', 'ﾚ': 'レ', 'ﾛ': 'ロ', 'ﾜ': 'ワ', 'ｦ': 'ヲ',
    'ﾝ': 'ン', 'ｰ': 'ー', 'ﾞ': '゛', 'ﾟ': '゜'
})

def convert_hw_katakana(text: str) -> str:
    for hw, fw in _HW_DAKU_MAP.items():
        text = text.replace(hw, fw)
    return text.translate(_HW_MAP)

KANA_RE = r'[\u3040-\u309F\u30A0-\u30FF\u31F0-\u31FF\uFF65-\uFF9F\u3031-\u3035\U0001B000-\U0001B16Fa-zA-Z0-9ａ-ｚＡ-Ｚ０-９ー・･ﾞﾟﾞ゛゜.･･\s\-/／＼]+'
KANJI_CHARS = r"\u4E00-\u9FFF\u3400-\u4DBF\uF900-\uFAFF\U00020000-\U000323AF\U0002F800-\U0002FA1F\u3005\U0001B000-\U0001B16F"
KANJI_PATTERN = rf"[{KANJI_CHARS}０-９0-9]+[\u3040-\u309F]*"
ALPHA_CHARS = r"a-zA-Zａ-ｚＡ-Ｚ0-9０-９α-ωΑ-Ω\'\.\u00C0-\u024F\u2160-\u217F"
ALPHA_PATTERN = rf"[{ALPHA_CHARS}]+(?:[ \t\-　]+[{ALPHA_CHARS}]+)*"
RUBY_BASE_RE = rf"(?:{KANJI_PATTERN}|{ALPHA_PATTERN})"
_SPACED_KANJI_BASE = rf"[{KANJI_CHARS}]+(?:[ 　][{KANJI_CHARS}]+)+"
RUBY_RE = re.compile(rf'(?:[｜|]({_SPACED_KANJI_BASE}|[^()\n\r\t 　]+?)|({RUBY_BASE_RE}))\(({KANA_RE})\)')
BOOK_RUBY_RE = re.compile(rf'(?:｜([^()｜\n\r\t]+?)|(?!)({RUBY_BASE_RE}))\(({KANA_RE})\)')


def ruby_re_for(media: str):
    return BOOK_RUBY_RE if media == 'epub' else RUBY_RE


BOOK_DISPLAY_RUBY_RE = re.compile(
    rf'(?:｜([^()｜\n\r\t]+?)\(|([{KANJI_CHARS}]+)[（(])({KANA_RE})[）)]')
GLOSS_KANA_RE = re.compile(r'[ぁ-ゖァ-ヺー・]+(?:[ 　]+[ぁ-ゖァ-ヺー・]+)*')


class GlossRuby:

    def __init__(self, accept):
        self.accept = accept

    def ok(self, m) -> bool:
        whole = m.group(0)
        if m.group(1) is not None:
            return whole.endswith(')')
        opened = whole[len(m.group(2))]
        return (whole[-1] == (')' if opened == '(' else '）')
                and not (m.start() and m.string[m.start() - 1] == '｜')
                and GLOSS_KANA_RE.fullmatch(m.group(3)) is not None
                and self.accept(m))

    def finditer(self, text):
        return (m for m in BOOK_DISPLAY_RUBY_RE.finditer(text) if self.ok(m))

    def sub(self, repl, text):
        def one(m):
            if not self.ok(m):
                return m.group(0)
            return m.expand(repl) if isinstance(repl, str) else repl(m)
        return BOOK_DISPLAY_RUBY_RE.sub(one, text)


_KANJI_WORD_RE = re.compile(rf'[{KANJI_CHARS}]{{2,}}')


def ruby_index_extras(line: str, regex, base_forms: str, readings: str):
    matches = [m for m in regex.finditer(line) if len(m.group(3).strip()) >= 2]
    if not matches:
        return base_forms, readings
    normalized = [re.sub(r'[ 　]', '', m.group(3)).replace('･', '・') for m in matches]
    readings = readings + " " + " ".join(katakana_to_hiragana(r) for r in normalized)
    base_forms = base_forms + " " + " ".join(normalized)
    bases = ruby_index_bases(matches, base_forms)
    if bases:
        base_forms = base_forms + " " + " ".join(bases)
    return base_forms, readings


def ruby_index_bases(matches, base_forms: str) -> list:
    tokens = set(base_forms.split())
    bases = []
    for m in matches:
        base = re.sub(r'[ 　]', '', m.group(1) or m.group(2))
        if _KANJI_WORD_RE.fullmatch(base) and base not in tokens:
            tokens.add(base)
            bases.append(base)
    return bases

SUBS_STR_REPLACEMENTS = [
    (">>", " "),
    ("?　", "？"), ("? ",  "？"), ("?",   "？"),
    ("？　", "？"), ("？ ", "？"),
    ("!　", "！"), ("! ",  "！"), ("!",   "！"),
    ("！　", "！"), ("！ ", "！"),
    ("｡", "。"), ("。　", "。"), ("。 ", "。"), (" 。", "。"),
    ("､", "、"), ("、 ", "、"), (" 、", "、"),
    ("……",    "…"), ("...",   "…"), ("････", "…"), ("･･･", "…"), ("･･", "："), ("…　",   "…"),
    ("… ",    "…"), (" …",    "…"), ("　…",   "…"),
    ("➡　", "――"), ("➡ ",  "――"), ("➡",   "――"),
    ("➨　", "――"), ("➨ ",  "――"), ("➨",   "――"),
    (" ―",  "――"), ("　―", "――"), (" —",  "――"), ("　—", "――"),
    ("｢",      "「"), ("｣",     "」"), ("「　",  "「"),
    (" 「",   "「"), ("」　",  "」"), ("」 ",   "」"),
    ("　(", "("), (" ( ", "("), ("( ",  "("), ("(　", "("),
    (" )",  ")"), ("　)", ")"), ("[", "（"), ("]", "）"),
    ("<",    "＜"), (">",    "＞"), ("＞ ",  "＞"), ("＞　", "＞"),
    ("＜ ",  "＜"), ("＜　", "＜"),
    ("）　", "）"), ("） ",  "）"), ("　（", "（"), (" （",  "（"),
    ("~ ",  "~"), (" ~",  "~"),
    ("〜",  "～"),
    (" : ", "："), (":",   "："),
    (" ･ ", "･"), (" ･",  "･"), ("･ ",  "･"), ("･",   "・"),
    (" ・","・"), ("・ ","・"),
    ('"',   '”'), ("→",   ""),
]

EPUB_STR_REPLACEMENTS = [
    ("｢", "「"), ("｣", "」"),
    ("｡", "。"), ("､", "、"),
    ("･", "・"),

    ("〜", "～"),

    ("？　", "？"), ("？ ", "？"),
    ("！　", "！"), ("！ ", "！"),
    ("。　", "。"), ("。 ", "。"), (" 。", "。"),
    ("、　", "、"), ("、 ", "、"), (" 、", "、"),
    ("「　", "「"), (" 「", "「"), ("」　", "」"), ("」 ", "」"),
    ("『　", "『"), (" 『", "『"), ("』　", "』"), ("』 ", "』"),
    ("（　", "（"), (" （", "（"), ("）　", "）"), ("） ", "）"),
    ("　（", "（"), ("　）", "）"),
    ("〈　", "〈"), (" 〈", "〈"), ("〉　", "〉"), ("〉 ", "〉"),
    ("《　", "《"), (" 《", "《"), ("》　", "》"), ("》 ", "》"),
    ("【　", "【"), (" 【", "【"), ("】　", "】"), ("】 ", "】"),
    (" ・", "・"), ("・ ", "・"),
    ("~ ", "~"), (" ~", "~"),

    (" ―", "――"), ("　―", "――"), (" —", "――"), ("　—", "――"),

    ("……", "…"),
    ("...", "…"), ("･･･", "…"),
    ("…　", "…"), ("… ", "…"), (" …", "…"), ("　…", "…"),
]

def katakana_to_hiragana(text: str) -> str:
    return text.translate(str.maketrans(
        'ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ',
        'ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖ'
    ))


SPACED_RUBY_PREV_RE = re.compile(rf"([{KANJI_CHARS}]+)[ 　]$")
_RUBY_SEP = "・･ 　"


def ruby_reading(m) -> str:
    return re.sub(r'[ 　]', '', m.group(3))


def build_ruby_lexicon(rows):
    by_work, corpus = {}, {}
    for work, line in rows:
        for m in ruby_re_for(work[0]).finditer(line):
            base, r = m.group(1) or m.group(2), ruby_reading(m)
            by_work.setdefault((work, base), set()).add(r)
            corpus.setdefault(base, set()).add(r)
    return by_work, corpus


def split_spaced_ruby(prev: str, base: str, reading: str, evidence):
    whole = katakana_to_hiragana(reading)
    base_readings, best = None, None
    for r_prev, rank in evidence(prev):
        head = katakana_to_hiragana(r_prev)
        if len(head) < 2 or len(whole) <= len(head) or not whole.startswith(head):
            continue
        r_base = reading[len(head):].lstrip(_RUBY_SEP)
        if not r_base:
            continue
        if base_readings is None:
            base_readings = {katakana_to_hiragana(r) for r, _ in evidence(base)}
        proven = katakana_to_hiragana(r_base) in base_readings
        if len(prev) < 2 and not proven:
            continue
        key = (proven, -rank, len(head))
        if best is None or key > best[0]:
            best = (key, reading[:len(head)], r_base)
    return (best[1], best[2]) if best else None


def load_ruby_decisions(path) -> dict:
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        next(f, None)
        for n, row in enumerate(f, 2):
            if not row.strip():
                continue
            prev, base, reading, decision = row.rstrip("\n").split("\t")[:4]
            if decision == "B":
                out[(prev, base, reading)] = None
                continue
            r_prev, sep, r_base = decision.partition("|")
            if (not sep or not r_prev or not r_base or not reading.startswith(r_prev)
                    or reading[len(r_prev):].lstrip(_RUBY_SEP) != r_base):
                raise ValueError(f"{path}:{n}: {decision!r} does not split {reading!r}")
            out[(prev, base, reading)] = (r_prev, r_base)
    return out


def ruby_merge_key(furi: str) -> str:
    return katakana_to_hiragana("".join(c for c in furi if c not in _RUBY_SEP))


def load_ruby_merges(path) -> frozenset:
    out = set()
    if not os.path.exists(path):
        return frozenset()
    with open(path, encoding="utf-8") as f:
        next(f, None)
        for row in f:
            if row.strip():
                base, reading = row.rstrip("\n").split("\t")[:2]
                out.add((base, ruby_merge_key(reading)))
    return frozenset(out)


def load_ruby_trims(path) -> dict:
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        next(f, None)
        for n, row in enumerate(f, 2):
            if not row.strip():
                continue
            base, reading, cut = row.rstrip("\n").split("\t")[:3]
            cut = int(cut)
            if not 0 <= cut < len(base):
                raise ValueError(f"{path}:{n}: cut {cut} leaves nothing of {base!r} under the ruby")
            out[(base, ruby_merge_key(reading))] = cut
    return out


def norm_relpath(path: str, root: str) -> str:
    return unicodedata.normalize("NFC", os.path.relpath(path, root))


def system_dic_path() -> str:
    try:
        import sudachidict_core
        return os.path.join(
            os.path.dirname(sudachidict_core.__file__), "resources", "system.dic")
    except Exception:
        return ""


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def tokenizer_identity(with_hash: bool = True) -> dict:
    import importlib.metadata as md

    ident = {}
    for key, dist in (("sudachidict_version", "SudachiDict-core"),
                      ("sudachipy_version", "SudachiPy")):
        try:
            ident[key] = md.version(dist)
        except Exception:
            ident[key] = "unknown"

    requires = ""
    try:
        for r in (md.metadata("SudachiDict-core").get_all("Requires-Dist") or []):
            if "sudachipy" in r.lower():
                requires = r
                break
    except Exception:
        pass
    ident["sudachidict_requires"] = requires
    ident["dictionary_format"] = "v1" if ">=0.7" in requires.replace(" ", "") else "v0"

    if with_hash:
        path = system_dic_path()
        try:
            ident["system_dic_sha256"] = _sha256_file(path)
            ident["system_dic_bytes"] = str(os.path.getsize(path))
        except Exception:
            ident["system_dic_sha256"] = "unknown"
            ident["system_dic_bytes"] = "unknown"
    return ident


def write_tokenizer_meta(conn, tokenized_rows: int) -> dict:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    if tokenized_rows <= 0:
        return dict(conn.execute("select k, v from meta").fetchall())

    prior = dict(conn.execute("select k, v from meta").fetchall())
    ident = tokenizer_identity()
    was = prior.get("system_dic_sha256")
    if prior and was != ident["system_dic_sha256"]:
        ident["mixed_with"] = (
            "%s rows predate this run and were tokenized with sudachidict=%s system_dic=%s"
            % ("some", prior.get("sudachidict_version", "?"), was or "unrecorded"))
    ident["tokenized_at"] = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO meta(k, v) VALUES (?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        sorted(ident.items()))
    return ident
