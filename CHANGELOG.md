# Changelog

## 1.1 — macOS, Linux, `.ass` subtitles and the Library check

- **macOS (Apple Silicon)**: a `.dmg` with Aobana inside, Python included. It opens at
  `http://127.0.0.1:5005/`, since macOS's AirPlay Receiver uses 5000. **Linux (x86-64)**: a
  `.tar.gz` with an installer, and an AppImage. Both are built and tested automatically but not yet
  confirmed at a real desktop.
- **`.ass` and `.ssa` subtitles** are read: the Japanese dialogue only. Chinese and English lines,
  signs, furigana lines and drawings are left out.
- **Check library**, in the Library tab: finds subtitles that are not in Japanese, other rips of
  the same episode, sets of SubPlz outputs and the same book in two files. The files you tick are
  left out of the index, not moved or deleted, and can be put back. The first Index library offers
  to run it first, once.
- **Bilingual subtitles** are indexed with their Japanese only.
- **Indexing** uses several processes on a machine with more than two cores, can do subtitles or
  books alone, and shows an estimate of the index size and the time before a run that adds files,
  with a warning above 10 GB. A file that cannot be read is reported and the rest is indexed. A
  subtitle file placed directly in the subtitles folder is indexed as a show of its own.
- **Stop**: indexing and the library check can be stopped, after asking. What was saved before a
  stop, a crash or a closed window stays in the index, and the next run carries on from there.
- **Subtitle text**: invisible direction marks and HTML codes written out as text are removed.
- **Titles**: episode titles drop release, codec and resolution tags and read the episode number
  from more naming styles (`S02EP01`, `1x01`, `第12話`); book titles keep their volume number and
  drop publisher tags.
- **Book chapters** follow the reading order; the contents page is not cut into chapters; a book
  that has only one chapter in its table of contents is split at its numbered headings; a section
  shows the chapter it belongs to.
- **Re-indexing a 1.0 library**: files indexed by 1.0 are re-read with one click in the Library
  tab, which the first start of 1.1 mentions once. Saved sentences follow their line.
- **Big libraries**: the Media list and the search sidebar load a page at a time, and a long search
  shows the time left.
- **Furigana over a short word** is centred over it.
- **The default folders are named `Subtitles` and `Books`.** An update renames 1.0's default
  folders once; folders you chose yourself are left as they are. On Android, the install makes
  empty `content/Subtitles` and `content/Books` folders.
- **Searching**: a random sort keeps its order when you switch tabs, and a cancelled search stops
  at once.
- **Updates**: the update window can skip a version. On Android, **Update now** installs the new
  version and reloads the page.

## 1.0 — the first public release

- **Search** over your own Japanese subtitles (`.srt`) and e-books (`.epub`), offline. A word also
  finds its other forms (`食べる` finds 食べた and 食べて), a reading in kana works too, and there
  are exact matches and `-` to exclude a word.
- **Furigana** on every sentence, from the book's own ruby where it has one; a reading written in
  parentheses after a word is drawn as furigana when a dictionary confirms it. One click hides it.
- **Context**: the lines around any result, and whole episodes and chapters in order in the Media
  tab.
- **Favorites, sorting and filters**: saved sentences; recommended, chronological, longest,
  shortest or random order; subtitles, books, both, or one title.
- **Japanese and English interface**, four themes, keyboard shortcuts, and a layout that fits a
  phone screen.
- **Windows installer** (English and Japanese) with Python and every package included, so it works
  offline with nothing installed first. It installs for one user or for all users and sets up the
  media folders and the folder for the index; running it again changes them. The uninstaller asks
  what to delete besides the program, and never deletes folders you chose yourself.
- **Android** through Termux, with one install command.
- **Update check** at start: if GitHub has a newer release, a window links to it. Offline, nothing
  is shown.
