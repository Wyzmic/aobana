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


def normalize_cjk_spacing(text: str) -> str:
    t = text.strip()
    if not t:
        return ""

    t = re.sub(r'[ \t　]+', ' ', t).strip()
    tokens = t.split(' ')
    if len(tokens) <= 1:
        return t

    cjk_single_re = re.compile(r'^[一-鿿぀-ヿ々゠-ヿ0-9０-９]$')

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


BOOK_SERIES_PREFIXES = [
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
BOOK_BRACKET_VOL_RE = re.compile(
    r"\s*[\(\（\[\［\【\〔〈《<]\s*"
    r"(上|中|下|前|後|前編|中編|後編|前篇|中篇|後篇|上巻|中巻|下巻|冬上|冬下|春・夏|秋|"
    r"[0-9０-９]{1,3}|[一二三四五六七八九十]{1,3}|"
    r"第?[0-9０-９一二三四五六七八九十百]+(?:巻|話|部|編|篇|章|回|冊|集|幕)|"
    r"(?:Vol|Volume|Part)\.?\s*[0-9０-９IVX]+)"
    r"\s*[\)\）\]\］\】\〕〉》>](?![のにをがはとでやもへ歳才代])",
    re.IGNORECASE,
)
BOOK_PUB_KEYWORDS_RE = re.compile(
    r"文庫|新書|ブックス|BOOKS|ノベル|NOVEL|コミックス|COMIC|出版|書房|書店|選書|叢書|シリーズ|"
    r"コレクション|レーベル|ディスカヴァー|Impress|インプレス|NextPublishing|OnDeck|"
    r"限定|特典|SS|イラスト|書き下ろし|書下ろし|電子|版|付|合本|試し読み|無料|記念",
    re.IGNORECASE,
)
BOOK_ANGLE_JUNK_RE = re.compile(r"\s*[〈《<]([^〉》<>]*)[〉》>]", re.IGNORECASE)
BOOK_ANGLE_PUB_RE = re.compile(
    r"文庫|新書|ブックス|BOOKS|ノベル|NOVELS|限定版|特別版|特典版|新装版|改訂版|増補決定版|オールカラー版|完全版|トークメーカー版|電子",
    re.IGNORECASE,
)
BOOK_TRAILING_IMPRINT_RE = re.compile(
    r"\s*(?:PHP文芸文庫|PHP文庫|徳間文庫|えちかわ文庫|プリンセス文庫|ｅマニア文庫|ステイタス文庫|鹿砦社新書|Forest2545新書|スマートブックス)$"
)
BOOK_DANGLING_EDGE_RE = re.compile(r"(?:\s+-\s*$|\s*[:：]\s*$)")
BOOK_UPLOADER_PLACEHOLDERS = frozenset({"Unknown", "unknown", "Yuri Yuru"})
BOOK_PROMO_BRACKET_RE = re.compile(r"音声|DL|付|対応|記念|限定|特典|無料|改訂|新装|完訳|語録|no[\s_]*name", re.IGNORECASE)
BOOK_SWAPPED_TITLE_AUTHOR_FILES = frozenset({
    "[てんのじ村]_難波利三.epub",
    "[大いなる助走]_筒井康隆.epub",
    "[黒パン俘虜記]_胡桃沢耕史.epub",
})
BOOK_AUTHOR_ROLE_RE = re.compile(
    r"(?:[\(\（](?:編|編集|著|訳|監修|原作|イラスト|漫画)[\)\）]|"
    r"[・\s]+(?:編|著|訳|監修)$|"
    r"(?<=編集部)編$|"
    r"(?<=[゠-ヿ])(?:著|訳|編)$)"
)
BOOK_RAW_FILE_CH_RE = re.compile(
    r"^(?:text\d+|part\d+|item\d+|sec\d+|p-\d+|ch\d+|c\d+|section\d+|page\d+)$|\.x?html$",
    re.IGNORECASE,
)
_BOOK_BRACKET = r"[\(\（\[\［\【\〔][^\(\（\[\［\【\〔\)\）\]\］\】\〕]*[\)\）\]\］\】\〕]"


def _angle_tag(m, whole):
    inner = m.group(1).strip()
    if BOOK_ANGLE_PUB_RE.search(inner):
        return ""
    if not m.group(0).lstrip().startswith("<"):
        return m.group(0)
    rest = BOOK_ANGLE_JUNK_RE.sub("", whole[:m.start()] + whole[m.end():])
    key = re.sub(r"[\s「」『』]|シリーズ$", "", inner)
    if key and key in re.sub(r"\s", "", rest):
        return ""
    return m.group(0)


def clean_book_title(title: str) -> str:
    if not title:
        return ""
    t = title.strip()
    for sp in BOOK_SERIES_PREFIXES:
        t = re.sub(sp, "", t, flags=re.IGNORECASE)
    t_vol = BOOK_BRACKET_VOL_RE.sub(
        lambda m: " " + m.group(1) + (" " if re.match(r"\w", m.string[m.end():m.end() + 1]) else ""), t)

    protected = {}

    def _protect(m):
        whole = m.group(0)
        after = t_vol[m.end():]
        if (m.start() > 0 and after and re.match(r"^[のにをがはとでやもへ歳才代]", after)
                and not BOOK_PUB_KEYWORDS_RE.search(whole[1:-1].strip())):
            key = f"__PROT_BRACKET_{len(protected)}__"
            protected[key] = whole
            return key
        return whole

    t = re.sub(_BOOK_BRACKET, _protect, t_vol)
    for _ in range(3):
        t_next = re.sub(_BOOK_BRACKET, "", t)
        if t_next == t:
            break
        t = t_next
    for k, v in protected.items():
        t = t.replace(k, v)

    t = re.sub(r"\s*ビギナーズ・クラシックス\s*日本の古典.*$", "", t)
    t = re.sub(r"\s*古典現代語訳叢書.*$", "", t)
    t = re.sub(r"^\d+\s*新・古文入門", "新・古文入門", t)
    t = BOOK_ANGLE_JUNK_RE.sub(lambda m: _angle_tag(m, t), t)
    t = t.replace("_", " ")
    t = BOOK_TRAILING_IMPRINT_RE.sub("", t)
    t = BOOK_DANGLING_EDGE_RE.sub("", t)
    t = t.replace("/", "／")
    t = re.sub(r"[ \t　]+", " ", t).strip()
    return normalize_cjk_spacing(t)


def _balanced(x):
    return all(
        sum(x.count(o) for o in op) == sum(x.count(c) for c in cl)
        for op, cl in (("(（", ")）"), ("[［【〔", "]］】〕"), ("〈《<", "〉》>"))
    )


def book_title_and_author(epub_path, opf_title, opf_author):
    fname = os.path.basename(epub_path)
    fname_clean = fname[:-5] if fname.lower().endswith(".epub") else fname

    file_author = file_title = ""
    m_bracket = re.match(r"^\[(.*?)\]\s*(.*)$", fname_clean)
    m_dash = re.match(r"^(.*?)\s*-\s*(.*)$", fname_clean)
    if m_bracket:
        file_author = m_bracket.group(1).strip()
        file_title = m_bracket.group(2).strip().lstrip("_")
    elif m_dash and _balanced(m_dash.group(1)):
        file_author = m_dash.group(1).strip()
        file_title = m_dash.group(2).strip()
        if any(BOOK_PUB_KEYWORDS_RE.search(b) for b in re.findall(_BOOK_BRACKET, file_author)):
            file_author, file_title = file_title, file_author
    else:
        file_title = fname_clean

    author = opf_author.strip() if opf_author else ""
    if author in BOOK_UPLOADER_PLACEHOLDERS:
        if file_author and not BOOK_PROMO_BRACKET_RE.search(file_author) and fname not in BOOK_SWAPPED_TITLE_AUTHOR_FILES:
            author = file_author.replace("_", " ")
        elif author.lower() == "unknown":
            author = ""
    elif not author and file_author and not BOOK_PROMO_BRACKET_RE.search(file_author):
        author = file_author
    if author:
        author = BOOK_AUTHOR_ROLE_RE.sub("", author).strip() or author
        author = re.sub(r"\s*([／、])\s*", r"\1", author)

    if not opf_title or re.search(r"^\d{4,}_|申請データ|draft|titlepage", opf_title, re.IGNORECASE):
        raw = file_title
    else:
        raw = opf_title if clean_book_title(opf_title) else file_title
    return normalize_cjk_spacing(author), clean_book_title(raw)


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


def clean_sub_stem(filename: str) -> str:
    if filename.lower().endswith(('.srt', '.ass', '.ssa')):
        filename = filename[:-4]
    filename = re.sub(r'\.(?:ja|jp|jpn|ja-en|ja-jp|jpn-en|jp-en)$', '', filename, flags=re.IGNORECASE)
    filename = re.sub(r'\s*\[[0-9A-Fa-f]{8}\]', '', filename)
    stripped = re.sub(r'^\s*\[[^\]]*\]\s*', '', filename)
    if stripped and not stripped.startswith('['):
        filename = stripped
    return filename


def sub_relpath(path: str, root: str) -> str:
    rel = norm_relpath(path, root)
    if os.sep in rel or (os.altsep and os.altsep in rel):
        return rel
    show = clean_sub_stem(rel).strip(' ._') or rel
    return os.path.join(show, rel)


FILTER_COLUMNS = ("media", "name", "reason", "keep", "date")


def filtered_rows(path: str) -> list:
    rows = []
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) < 2 or not parts[1] or parts[0] == "media":
                    continue
                parts += [""] * (len(FILTER_COLUMNS) - len(parts))
                rows.append(dict(zip(FILTER_COLUMNS, parts)))
    except OSError:
        pass
    return rows


def filtered_names(path: str, media: str) -> set:
    return {r["name"] for r in filtered_rows(path) if r["media"] == media}


_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")
_CJK = re.compile(r"[一-鿿]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
CHINESE_RE = re.compile(r"[们們这说說么麼吗嗎沒谁给哪呢吧啊你妳]")
_CHUNK_SPLIT = re.compile(r"([\s　]+)")


def simplified(text: str) -> int:
    n = 0
    for ch in _CJK.findall(text):
        try:
            ch.encode("cp932")
            continue
        except UnicodeEncodeError:
            pass
        try:
            ch.encode("gb2312")
        except UnicodeEncodeError:
            continue
        try:
            ch.encode("big5")
        except UnicodeEncodeError:
            n += 1
    return n


_SPEAKER_TAG = re.compile(r"[（(][^）)]*[）)]")


def is_chinese_text(text: str) -> bool:
    text = text.strip()
    if not text or _KANA.search(text):
        return False
    text = _SPEAKER_TAG.sub("", text).strip() or text
    if CHINESE_RE.search(text):
        return True
    return bool(simplified(text)) and len(_CJK.findall(text)) >= 3


_chinese_chunk = is_chinese_text


def line_kind(line: str) -> str:
    if _KANA.search(line):
        return "mix" if any(_chinese_chunk(c) for c in re.split(r"[\s　]+", line)) else "ja"
    if len(_CJK.findall(line)) >= 5 and (CHINESE_RE.search(line) or simplified(line)):
        return "zh"
    if not _CJK.search(line) and len(_LATIN_WORD.findall(line)) >= 3:
        return "en"
    return "other"


def strip_chinese_chunks(line: str):
    if not _KANA.search(line):
        return line, []
    parts = _CHUNK_SPLIT.split(line)
    removed = [p for p in parts[::2] if _chinese_chunk(p)]
    if not removed:
        return line, []
    out = []
    for i in range(0, len(parts), 2):
        chunk = parts[i]
        if not chunk or _chinese_chunk(chunk):
            continue
        if out:
            out.append(parts[i - 1])
        out.append(chunk)
    return "".join(out), removed


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


INDEX_FORMAT = {"subs": 2, "epub": 2}


def ensure_format_column(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
    if "index_format" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN index_format INTEGER NOT NULL DEFAULT 1")


def outdated_sources(conn, media) -> int:
    if conn is None:
        return 0
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
        if "index_format" not in cols:
            return conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM sources WHERE index_format < ?",
                            (INDEX_FORMAT[media],)).fetchone()[0]
    except Exception:
        return 0


def compact_index(conn, table):
    conn.commit()
    conn.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
    conn.commit()
    conn.execute("VACUUM")
    print(f"COMPACTED {table}")


def stop_requested():
    path = os.environ.get("AOBANA_STOP_FILE")
    return bool(path) and os.path.exists(path)


COMPACT_SHARE = 0.25


def compact_if_worth(conn, table, deleted, inserted):
    if not deleted:
        return
    conn.commit()
    before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - inserted + deleted
    if before > 0 and deleted / before >= COMPACT_SHARE:
        compact_index(conn, table)


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


def parallel_map(fn, items, workers, chunksize=1):
    pool = None
    if workers > 1:
        try:
            import multiprocessing
            pool = multiprocessing.get_context("spawn").Pool(workers)
        except (ImportError, OSError, NotImplementedError) as e:
            print(f"PARALLEL_OFF {type(e).__name__}: {e}", flush=True)
    if pool is None:
        yield from map(fn, items)
        return
    with pool:
        yield from pool.imap(fn, items, chunksize)
