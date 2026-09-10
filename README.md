# netagrab

Download NetAcad courses as PDF, Markdown, or structured JSON for offline study.

## Run via Nix

```sh
nix run .#
```

Without Nix, install uv, then run `uv sync` and `uv run playwright install chromium`.

```sh
uv run netagrab                              # prompt for the course URL
uv run netagrab --format all     # pdf, markdown, json, or all
uv run netagrab --offline --format markdown json
```

Login prompts appear when needed; authentication is cached in `.session/auth.json`.
Use `--headed` for browser login/MFA, `--output DIR` to choose a folder, and
`--modules 1,2` for selected modules. See `uv run netagrab --help` for all options.

Exports go to `output/`, with separate folders for additional courses. Keep `assets/`
beside Markdown/JSON files. Videos are separate files; transcripts and lab text are
included. Interactive activities use static extracts; external exams stay online.

## Development

Running: 

```sh
nix develop
uv sync
uv run netagrab
```

Testing:

```sh
uv run python -m unittest discover -s tests -v
uv build
uv export --no-dev --no-emit-project --no-hashes --output-file requirements.txt
```
