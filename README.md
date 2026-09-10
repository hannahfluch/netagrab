# netagrab

Exports the supplied CyberOps Associate course as PDF, Markdown, or structured JSON with images,
tables, expanded tabs, command examples, video transcripts, and appended PDF lab
handouts. It also saves per-module PDFs, offline HTML, videos, and linked activity
files. The scraper supports NetAcad's Adapt `courses/content/mN` course layout.

## Run with Nix

```sh
nix develop
python netacad_pdf.py 'https://www.netacad.com/launch?id=YOUR_COURSE_ID'
```

Or use your `ns` wrapper:

```sh
ns python3 -c nix develop --command python netacad_pdf.py 'https://www.netacad.com/launch?id=YOUR_COURSE_ID'
```

`flake.nix` and `flake.lock` pin Python, Playwright, Chromium, and the PDF tools.
There is no pip install or separate Playwright browser download.

Pass the course launch link as the first argument, or use `--url 'COURSE_URL'`.
Quote the link so the shell treats any `&` characters literally. Without a link,
the script asks for it on stdin:

```sh
python netacad_pdf.py --format markdown
# NetAcad course URL: (paste your link and press Enter)
```

There is no hardcoded course URL. A valid `.session/auth.json` skips the login
prompts. If the session is missing or expired, the script asks for your email and
password, then saves the authenticated session for next time. An unreadable session
file starts a fresh login. The password prompt does not echo.

`--offline` uses the downloaded manifest and does not ask for a URL or credentials.
When the default `output/` already contains another course, a new link uses
`output/course-<id-hash>/` to keep cached lessons separate. An explicitly selected
`--output` folder must belong to that course or be empty; use the same folder when
rebuilding with `--offline`.

## Choose an export format

PDF remains the default. Use `--format` to choose one or several formats:

```sh
python netacad_pdf.py --format pdf
python netacad_pdf.py --format markdown
python netacad_pdf.py --format json
python netacad_pdf.py --format markdown json
python netacad_pdf.py --format all
```

For the course already downloaded in `output/`, convert it without logging in or
downloading it again:

```sh
nix develop --command python netacad_pdf.py --offline --format markdown json
```

Markdown is a good starting point for reading, copying into an AI conversation,
or uploading as a document. Use the individual module files if the whole course
is too large for your tool. JSON is intended for software and AI retrieval tools:
`course.json` has a versioned schema (`schema_version: 1`), source URL, language,
ordered modules and sections, stable section/component IDs, Markdown content per
section, attachments with extracted text per PDF page, and export limitations.
Per-module JSON files use the same module structure.

Both text formats include video transcripts, expanded tabs, tables, code blocks,
local image links, and available diagram descriptions and labels. Image pixels
are not embedded in Markdown or JSON: keep `assets/` beside the exports, and supply
images separately to an AI tool if it needs to inspect them. Lab PDFs are linked
and their text is extracted into the combined exports; no OCR is performed, so
consult the originals for diagrams, scanned pages, and precise layout.

Offline Markdown/JSON exports do not launch Chromium. Online exports still use
Chromium for authentication and course discovery. HTML is generated in all modes.

For MFA or another login step:

```sh
python netacad_pdf.py --headed
```

Complete login in Chromium when prompted. Session cookies and browser login state
are saved in `.session/auth.json` with private permissions and excluded from Git.
Credentials are not embedded in the scraper. Delete `.session/auth.json` to remove
the saved login. `NETACAD_USERNAME` and `NETACAD_PASSWORD` environment variables
are also supported; an interactive password prompt avoids shell history exposure.

## Output

- `output/course.pdf`: combined course with module bookmarks and lab handouts.
- `output/course.md`: combined Markdown course and extracted lab text (`--format markdown`).
- `output/index.md`, `output/labs.md`: Markdown index and lab text.
- `output/course.json`: structured course and lab text (`--format json`).
- `output/module-01.md` / `output/module-01.json`, etc.: individual modules in the selected formats.
- `output/index.html`: offline HTML table of contents; keep the whole output folder.
- `output/module-01.pdf`, etc.: individual modules.
- `output/assets/`: local images, videos, captions, and activity downloads.
- `output/source/`: cached course JSON used to rebuild the export.
- `output/report.json`: module coverage, component counts, attachments, missing
  assets, and activities represented as static extracts.

Videos play as separate downloaded files; their available captions are printed as
transcripts. Diagrams retain their labels. Tabs are expanded. Animations and some
interactive activities use their descriptions/text rather than working controls.
Packet Tracer activities require Packet Tracer. External checkpoint and final
exams are not launched or submitted. Ordinary course navigation during discovery
may record a lesson visit in NetAcad.

The PDF is reformatted for reading, rather than a pixel-perfect replica of the
online player. Review `report.json` before relying on an export as complete.

## Options and rebuilding

```sh
python netacad_pdf.py --no-videos          # keep captions/posters, skip video downloads
python netacad_pdf.py --modules 1,2        # partial export only; use a separate output folder if needed
python netacad_pdf.py --output study      # choose output folder
python netacad_pdf.py --offline           # rebuild with no network or login
python netacad_pdf.py --url 'COURSE_URL'   # another enrolled course with the same layout
```

Rerunning reuses downloaded files and retries missing assets, then rebuilds the selected formats.
`--offline` reports missing cached resources instead of fetching them. A partial
run replaces the selected combined exports and report with that subset; module files
from earlier runs can remain on disk. Delete the output folder for a fresh download
if the source course has changed. Keep saved login state and course exports private.
Files from formats not selected on the current run remain untouched; the coverage
report lists the formats and modules generated by the current run.

## Local verification

```sh
nix develop --command python -m unittest -v
```

The tests check format selection, structured section IDs, lab text extraction,
Markdown tables/code/images, expanded nested tabs, embedded PDF images, merging,
and bookmarks without contacting NetAcad.
