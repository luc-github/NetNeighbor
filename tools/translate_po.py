# File translate_po.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Translate untranslated entries in a .po file using the Anthropic API.

Usage:
    python tools/translate_po.py locale/fr/LC_MESSAGES/netneighbor.po

.env file (project root or current directory):
    ANTROPIC=sk-ant-...
    MODEL=claude-haiku-4-5

The script:
  - Reads ANTROPIC key and MODEL from .env (MODEL defaults to claude-haiku-4-5)
  - Detects the target language from the .po file header
  - Skips entries that are already translated or marked fuzzy
  - Translates in batches of 20 to minimise API calls
  - Saves the file after every batch so progress is not lost on interruption
  - Handles singular and plural forms (msgid_plural / msgstr[0..N])

Requirements:
    pip install anthropic polib
"""

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal .env loader — no python-dotenv dependency
# ---------------------------------------------------------------------------

def _load_dotenv(*search_dirs: Path) -> None:
    for d in search_dirs:
        p = d / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
            return


# ---------------------------------------------------------------------------
# Translation core
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a professional software localisation translator.
Translate UI strings from English to {language}.
Rules:
- Keep format placeholders EXACTLY as-is: %s %d %f %(name)s {{0}} {{name}} <b> </b> &amp; \\n \\t
- Keep accelerator underscores (_File) unchanged
- Keep ellipsis (…) unchanged
- Match the register of the source (imperative button labels stay imperative, etc.)
- Reply ONLY with a valid JSON array of translated strings in the same order as the input.
  No explanation, no markdown, no extra keys.
"""

_USER_PROMPT = """\
Target language: {language}

Translate each English string below.
Return a JSON array with exactly {count} elements, one translation per source string.

Source strings (JSON array):
{sources_json}
"""


def _build_plural_sources(entry) -> list[str]:
    """Return the source strings to translate for a plural entry."""
    singular = entry.msgid
    plural = entry.msgid_plural
    # We translate both forms; the model understands plural vs singular from context.
    return [singular, plural]


def _translate_batch(
    client,
    model: str,
    language: str,
    sources: list[str],
    max_retries: int = 3,
) -> list[str] | None:
    """Send one batch to the API. Returns list of translations or None on failure."""
    system = _SYSTEM_PROMPT.format(language=language)
    user = _USER_PROMPT.format(
        language=language,
        count=len(sources),
        sources_json=json.dumps(sources, ensure_ascii=False, indent=2),
    )
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": user}],
                system=system,
            )
            text = response.content[0].text.strip()
            # Strip markdown code fences if model wrapped the JSON
            if text.startswith("```"):
                lines = text.splitlines()
                text = "\n".join(
                    l for l in lines if not l.startswith("```")
                ).strip()
            translations = json.loads(text)
            if not isinstance(translations, list) or len(translations) != len(sources):
                print(
                    f"  [warn] API returned {len(translations) if isinstance(translations, list) else '?'} "
                    f"items for {len(sources)} sources — retrying",
                    file=sys.stderr,
                )
                time.sleep(2 ** attempt)
                continue
            return [str(t) for t in translations]
        except json.JSONDecodeError as exc:
            print(f"  [warn] JSON decode error (attempt {attempt}): {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
        except Exception as exc:
            print(f"  [warn] API error (attempt {attempt}): {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # Locate and load .env from script dir or cwd
    script_dir = Path(__file__).resolve().parent
    _load_dotenv(Path.cwd(), script_dir, script_dir.parent)

    api_key = os.environ.get("ANTROPIC", "").strip()
    model = os.environ.get("MODEL", "claude-haiku-4-5").strip()

    if not api_key:
        print("Error: ANTROPIC key not found in .env", file=sys.stderr)
        print("Create a .env file with:  ANTROPIC=sk-ant-...", file=sys.stderr)
        return 1

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <path/to/file.po>", file=sys.stderr)
        return 1

    po_path = Path(sys.argv[1])
    if not po_path.exists():
        print(f"Error: file not found: {po_path}", file=sys.stderr)
        return 1

    try:
        import polib
    except ImportError:
        print("Error: polib is required.  Install it with:  pip install polib", file=sys.stderr)
        return 1

    try:
        import anthropic
    except ImportError:
        print("Error: anthropic is required.  Install it with:  pip install anthropic", file=sys.stderr)
        return 1

    # Parse the .po file
    po = polib.pofile(str(po_path), encoding="utf-8")

    # Detect target language from file metadata
    language = po.metadata.get("Language", "").strip()
    if not language:
        language = po.metadata.get("Language-Team", "").strip()
    if not language or language == "LANGUAGE <LL@li.org>":
        # Try to guess from the file path (e.g. locale/fr/LC_MESSAGES/...)
        parts = po_path.parts
        for i, part in enumerate(parts):
            if part == "locale" and i + 1 < len(parts):
                language = parts[i + 1]
                break
    if not language:
        print("Warning: could not detect language from .po header — using 'the target language'", file=sys.stderr)
        language = "the target language"

    # Collect entries that need translation:
    #   - untranslated (empty msgstr, no fuzzy flag)
    #   - fuzzy (auto-matched by msgmerge, often wrong — retranslate and remove flag)
    fuzzy_entries = [e for e in po.fuzzy_entries() if e.msgid and not e.obsolete
                     and not e.msgid_plural]
    fuzzy_plural  = [e for e in po.fuzzy_entries() if e.msgid and e.msgid_plural
                     and not e.obsolete]

    to_translate = [e for e in po if e.msgid and not e.msgstr and not e.obsolete
                    and "fuzzy" not in (e.flags or [])]
    # Also collect plural entries with missing translations
    to_translate_plural = [e for e in po if e.msgid and e.msgid_plural
                           and not e.msgstr_plural.get(0, "").strip()
                           and not e.obsolete and "fuzzy" not in (e.flags or [])]
    # Deduplicate (plural entries also appear in the main list when msgstr is empty)
    plural_ids = {id(e) for e in to_translate_plural}
    singular_to_translate = [e for e in to_translate if id(e) not in plural_ids]

    # Append fuzzy entries after the regular untranslated ones
    fuzzy_singular_ids = {id(e) for e in fuzzy_entries}
    singular_to_translate += [e for e in fuzzy_entries if id(e) not in plural_ids]
    to_translate_plural  += [e for e in fuzzy_plural  if id(e) not in plural_ids]

    total_singular = len(singular_to_translate)
    total_plural   = len(to_translate_plural)
    total          = total_singular + total_plural
    already_done   = len(po.translated_entries())
    fuzzy_count    = len(po.fuzzy_entries())

    print(f"File       : {po_path}")
    print(f"Language   : {language}")
    print(f"Model      : {model}")
    print(f"Total      : {len(po)} entries")
    print(f"Translated : {already_done}")
    print(f"Fuzzy      : {fuzzy_count} (will be retranslated)")
    print(f"To do      : {total} ({total_singular} singular + {total_plural} plural)")
    print()

    if total == 0:
        print("Nothing to translate — all entries already have translations.")
        return 0

    client = anthropic.Anthropic(api_key=api_key)

    BATCH_SIZE = 20
    done = 0
    errors = 0

    # --- Translate singular entries ---
    for batch_start in range(0, total_singular, BATCH_SIZE):
        batch = singular_to_translate[batch_start : batch_start + BATCH_SIZE]
        sources = [e.msgid for e in batch]
        print(f"Translating singular [{done + 1}–{done + len(batch)}/{total}] ...", end=" ", flush=True)

        translations = _translate_batch(client, model, language, sources)
        if translations is None:
            print("FAILED (batch skipped)")
            errors += len(batch)
        else:
            for entry, translation in zip(batch, translations):
                entry.msgstr = translation
                # Remove fuzzy flag so msgfmt includes this entry
                if "fuzzy" in entry.flags:
                    entry.flags.remove("fuzzy")
            done += len(batch)
            print(f"OK ({len(batch)} entries)")
            po.save(str(po_path))

    # --- Translate plural entries ---
    for batch_start in range(0, total_plural, BATCH_SIZE):
        batch = to_translate_plural[batch_start : batch_start + BATCH_SIZE]
        # For each plural entry send [singular, plural] → get [singular_tr, plural_tr]
        sources = []
        for entry in batch:
            sources.append(entry.msgid)
            sources.append(entry.msgid_plural)

        print(f"Translating plural  [{done + 1}–{done + len(batch)}/{total}] ...", end=" ", flush=True)

        translations = _translate_batch(client, model, language, sources)
        if translations is None:
            print("FAILED (batch skipped)")
            errors += len(batch)
        else:
            for i, entry in enumerate(batch):
                singular_tr = translations[i * 2]
                plural_tr = translations[i * 2 + 1]
                # Populate all plural forms (languages vary: 1, 2, 3+ forms)
                n_forms = len(entry.msgstr_plural) if entry.msgstr_plural else 2
                if n_forms < 2:
                    n_forms = 2
                entry.msgstr_plural[0] = singular_tr
                for idx in range(1, n_forms):
                    entry.msgstr_plural[idx] = plural_tr
                if "fuzzy" in entry.flags:
                    entry.flags.remove("fuzzy")
            done += len(batch)
            print(f"OK ({len(batch)} entries)")
            po.save(str(po_path))

    print()
    print(f"Done. Translated: {done}  Errors: {errors}")
    if errors:
        print(f"Tip: run the script again to retry the {errors} failed entries.")
    print(f"File saved: {po_path}")
    print(f"Compile:    msgfmt {po_path} -o {po_path.with_suffix('.mo')}")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
