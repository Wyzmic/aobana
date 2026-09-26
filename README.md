<p align="center">
  <img src="static/aobana.svg" alt="" width="112">
</p>

<h1 align="center">露草 Aobana</h1>

<p align="center"><b>Japanese example sentences from the subtitles and e-books you already own.</b></p>

<p align="center">
  <a href="https://github.com/Wyzmic/aobana/releases/latest"><img src="https://img.shields.io/github/v/release/Wyzmic/aobana" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="License: GPL-3.0"></a>
</p>

<p align="center">English | <a href="README.ja.md">日本語</a></p>

---

Aobana (露草, *あおばな*) is an offline search engine for Japanese example sentences, built from
the subtitle files and e-books you already own. Everything runs on your own computer and
nothing is sent to the internet: at start it only asks GitHub whether a newer version exists,
and if one does, it offers the release page.

![Aobana's search page: 会員, with furigana, in the Night theme](assets/search-night.en.png)

## Features
- **Your own library:** subtitles (`.srt`, `.ass`, `.ssa`) and e-books (`.epub`), indexed from two folders you choose. From an `.ass`, only the Japanese dialogue is read: Chinese and English lines, signs, furigana lines and drawings are left out.
- **Search that understands Japanese:** `食べる` also finds 食べた and 食べて, and the reading `たべる` works too. Exact matches, and excluding a word with `-`.
- **Furigana:** on every sentence, from the author's own ruby where the book has it. One click turns it off.
- **Context:** open the lines around any sentence, or read a whole episode or chapter in order from the Media tab.
- **Favorites, sorting and filters:** save sentences; sort them by recommended, chronological, longest, shortest or random; search subtitles, books or both, or inside a single title.
- **Japanese and English UI**, four themes, keyboard shortcuts.

## Screenshots
Searching for 帰る across subtitles and books, in the Haze theme, with the context open.

![Search results for 帰る, with the context of one result open](assets/search-haze.en.png)

The Media tab, reading an episode in chronological order:

![The Media tab with an episode open](assets/media-haze.en.png)

## Install (Windows 10 / 11, 64-bit)
1. Download `Aobana-Setup-<version>.exe` from the [latest release](https://github.com/Wyzmic/aobana/releases/latest) and run it. It is not code-signed, so Windows SmartScreen may say *"Windows protected your PC"*: click **More info**, then **Run anyway**.
2. Follow the setup. Python and everything else is included, so nothing needs to be installed first, and it works offline.
3. Start **Aobana**. It opens in your browser at `http://127.0.0.1:5000/`.

To change your folders later, run the setup again.

## Install (macOS, Apple Silicon)
1. Download `Aobana-<version>-macos-arm64.dmg` from the [latest release](https://github.com/Wyzmic/aobana/releases/latest), open it, and drag **Aobana** into **Applications**.
2. Open Aobana. It is not signed by Apple, so the first time macOS refuses to open it: open **System Settings › Privacy & Security**, scroll down, and click **Open Anyway** next to Aobana. (Or, in Terminal: `xattr -dr com.apple.quarantine /Applications/Aobana.app`.)
3. Aobana starts in a Terminal window and opens in your browser at `http://127.0.0.1:5005/` (not 5000, which macOS's AirPlay Receiver uses). Close the Terminal window to quit.

Your library goes in `Documents/Aobana/Subtitles` and `Documents/Aobana/Books`, and the databases and settings in `~/Library/Application Support/Aobana`. You can change the folders in the Library tab. Intel Macs: run from source.

## Install (Linux, x86-64)
Two downloads on the [latest release](https://github.com/Wyzmic/aobana/releases/latest), the same app in each. They are built and tested automatically (Ubuntu 22.04 and 24.04, Fedora), but not yet confirmed at a real Linux desktop: if something does not work, please [open an issue](https://github.com/Wyzmic/aobana/issues).
- **`Aobana-<version>-linux-x86_64.tar.gz`** (recommended): unpack it and run `./install.sh` in the unpacked folder. That adds **Aobana** to your applications menu and an `aobana` command. Run the new version's `install.sh` to update, and `~/.local/share/aobana-app/uninstall.sh` to remove it.
- **`Aobana-<version>-x86_64.AppImage`**: one file, nothing installed. Make it executable (`chmod +x`) and run it. If it says FUSE is missing, run it with `--appimage-extract-and-run`.

Aobana runs in a terminal window; close the window to quit. Your library goes in `~/Documents/Aobana/Subtitles` and `~/Documents/Aobana/Books`, and the databases and settings in `~/.local/share/aobana`.

## Usage
1. **Add files.** Give each show its own folder inside the subtitles folder: the folder's name becomes the show's name. A subtitle file placed directly in the subtitles folder is read as a show of its own, named after the file. Books can go anywhere in the books folder.
   ```
   Subtitles folder/
   ├─ Show A/
   │   ├─ Show A S01E01.srt
   │   └─ Show A S01E02.srt
   └─ Show B/
       └─ Season 1/
           └─ Show B 第01話.srt

   Books folder/
   └─ [Author] Title.epub
   ```
2. **Build the index.** Press **Index library** in the Library tab (subtitles and books, or only one of them). Before a run that adds files, Aobana estimates how large the index will get and how long it will take, and warns when the two databases would pass 10 GB. Later runs only process files that changed.
   **Check library**, in the same tab, finds files that are not in Japanese and duplicates (another rip of the same episode, a set of SubPlz outputs, the same book in two files). The files you tick are only left out of the index: nothing is moved or deleted, and you can put them back. Bilingual subtitles are indexed with their Japanese part only.
3. **Search.**

The Guide tab explains the rest. Aobana runs in a small console window; close it to quit.

## Android (Termux)
Aobana runs on an Android phone and opens in your browser there, so a pop-up dictionary such as Yomitan works on it in Firefox. It needs Termux and Termux:Widget, from Google Play or [F-Droid](https://f-droid.org/packages/com.termux/) (both apps from the same store: builds from different stores cannot work together), and about 1.5 GB free.

1. **Install** — paste this into Termux, and allow storage access when asked:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/Wyzmic/aobana/main/termux/install.sh | bash
   ```
   It sets up a small Ubuntu inside Termux (the Sudachi analyzer has no Android build), installs Python and the pinned packages there, puts the files Aobana runs from (nothing else) in `/storage/emulated/0/Aobana`, and adds an **Aobana** shortcut for the Termux:Widget widget. Run the same command again to update; from 1.1 on, when a new version is out, the page offers **Update now**, which does that for you and reloads.
2. **Add your library** to that folder: copy `subs.db` and `epub.db` from a computer where Aobana has indexed it (fastest, and identical results), or put files in `content/Subtitles` and `content/Books` there and press **Index library** in the Library tab (slow on a phone).
3. **Start** — add the Termux:Widget widget to your home screen and tap **Aobana**. The page opens in Firefox if it is installed, otherwise in your default browser. If no browser opens, allow Termux *Display over other apps* in Android's settings. Closing Termux stops Aobana.

**Uninstall** — removes Aobana's Ubuntu, the shortcut and the app's files; in the Aobana folder only your databases, media and settings stay, for you to delete if you no longer want them:
```bash
curl -fsSL https://raw.githubusercontent.com/Wyzmic/aobana/main/termux/uninstall.sh | bash
```

## Run from source (Windows, macOS, Linux)
Python 3.14 is what the release is built and tested with.
```bash
git clone https://github.com/Wyzmic/aobana.git
cd aobana
pip install -r requirements.txt
python launcher.py
```
From a source folder, the media folders are `content/Subtitles` and `content/Books` beside the code, and the databases are written next to it. You can point both folders elsewhere from the Library tab. The pins in `requirements.txt` matter: the Sudachi dictionary decides how every sentence is indexed, so do not upgrade it on its own.

To build the Windows installer yourself, see `release/build.py`; the macOS and Linux downloads are built by `release/build_unix.py`, which the GitHub workflow runs.

## Extra ruby data
`data/ruby/` holds a few tab-separated tables that help Aobana draw furigana over the right characters, such as words whose ruby covers the whole word, and readings a book gives in parentheses that a dictionary confirms. You can add your own rows; restart Aobana to load them.

## Suggestions and bug reports
Ideas for what to add are welcome, and so are reports of anything that looks wrong. Open an
[issue](https://github.com/Wyzmic/aobana/issues) for either.

## Acknowledgements
- [Nadeshiko](https://github.com/BrigadaSOS/Nadeshiko), whose sentence search inspired many of these features.
- [Sudachi](https://github.com/WorksApplications/sudachi.rs) and its dictionary, for the morphological analysis.
- [Noto Sans JP](https://fonts.google.com/noto/specimen/Noto+Sans+JP), the bundled font.

Third-party licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Copyright
- Aobana includes no subtitles and no books. It indexes files on your own computer; use it only with files you have the right to use.
- The screenshots quote a few short lines from the author's own library to show how the app works. The titles shown belong to their rights holders.
- If you hold the rights to something shown and want it removed, [open an issue](https://github.com/Wyzmic/aobana/issues) and the image will be replaced.
- Aobana collects nothing and sends nothing: it runs only on your computer.

## License
[GPL-3.0](LICENSE)
