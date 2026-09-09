# netagrab

Exports the supplied CyberOps Associate course as a searchable PDF with images,
tables, expanded tabs, command examples, video transcripts, and appended PDF lab
handouts. It also saves per-module PDFs, offline HTML, videos, and linked activity
files. The scraper supports NetAcad's Adapt `courses/content/mN` course layout.

## Run with Nix

```sh
nix develop
python netacad_pdf.py
```

Or use your `ns` wrapper:

```sh
ns python3 -c nix develop --command python netacad_pdf.py
```

`flake.nix` and `flake.lock` pin Python, Playwright, Chromium, and the PDF tools.
There is no pip install or separate Playwright browser download.

The supplied course URL is the default. Login prompts for your email and password;
the password prompt does not echo. For MFA or another login step:

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

Rerunning reuses downloaded files and retries missing assets, then rebuilds PDFs.
`--offline` reports missing cached resources instead of fetching them. A partial
run replaces the combined PDF and report with that selected subset; module files
from earlier runs can remain on disk. Delete the output folder for a fresh download
if the source course has changed. Keep saved login state and course exports private.

## Local verification

```sh
nix develop --command python -m unittest -v
```

The integration test checks expanded nested tabs, embedded images, table text,
offline asset paths, PDF merging, and bookmarks without contacting NetAcad.
