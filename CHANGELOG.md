# Changelog

## 1.0 — the first public release

- A Windows installer (English and Japanese) that bundles Python 3.14 and every package, so it
  works offline with nothing preinstalled. It installs for one user or for all users, and sets
  up the two media folders (either may be left empty) and the folder for the index. Running it
  again shows your current folders, so an update can change them.
- `Aobana.exe` in the Start menu. It never starts a second server: when one is already running,
  it only opens a browser tab.
- Settings and logs live in `%LOCALAPPDATA%\Aobana`, and the index too unless you chose another
  folder. The uninstaller first asks what to delete besides the program (settings and logs, the
  index, the suggested `Documents\Aobana` media folders, each with its size), then asks to
  confirm; folders you chose yourself are never deleted. It stops a running Aobana first.
- Indexing runs only from the Library tab, and only processes files that changed.
- Extra ruby data in `data/ruby/`, so furigana is drawn over the right characters (see the README).
- Books keep their text as written: the index holds the book's own words, parentheses included.
  On screen, real ruby (`<ruby>` in the EPUB) is drawn as furigana, and so is a reading in
  parentheses such as `漢字（かんじ）` when a dictionary or Sudachi confirms it. A note such as
  `師（ブッダ）` or `sqrt(n)` stays in the text.
- Checks for a newer version at every start: it asks GitHub for the latest release, and if there
  is one, a window says which version you are on and which is the latest, with a button to the
  release page. Offline, nothing is shown.
- On a narrow screen the top bar folds into the ☰ menu a step at a time: first the shortcuts and
  language buttons, then the theme, then the tabs one by one from the right, so the rest stay one
  tap away.
- Android (Termux): the install puts only the files Aobana runs from on the phone, and works with
  proot-distro 5; the uninstall removes everything but your databases, media and settings.
