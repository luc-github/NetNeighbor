# Contributing Icons

## Type → default asset basenames (`config/icons.json`)

Per logical device type (slug), the **ordered** list of icon **basenames** lives in
**`config/icons.json`** under the ``types`` object. Each name is resolved as::

    assets/icons/netneighbor/{N}x{N}/<basename>.png

(first existing size wins; see ``ui/icons.bundled_freedesktop_png_side_sizes``).

- Edit **`config/icons.json`** in the repository to change defaults shipped with the app.
- Optional user override: **`~/.config/netneighbor/icons.json`** with the same ``types`` structure (per-slug lists replace or extend shipped entries for that slug). Documented in [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).

There is **no** Freedesktop theme, **no** Windows Shell stock, and **no** ``QStyle`` fallback for types. If no basename matches, **`assets/icons/unknown.png`** is used when present.

## Multi-size pack layout

Square folders (`16x16` … `1024x1024`, etc.) under ``netneighbor/`` are auto-discovered.

## Per-device JSON icons (`data/device_types.json`)

The ``icon`` field may still name a file under ``assets/icons/`` (e.g. ``router.png``). If that file exists, it is shown before the type pack (device row logic in the UI).

## User custom PNGs

``~/.config/netneighbor/custom_icons/*.png`` — picked in GTK/Qt device icon dialogs (ids ``custom:filename.png``).

## Registering a new device type slug

1. Add the slug and basename list under ``types`` in ``config/icons.json``.
2. Add matching PNGs under ``netneighbor/{N}x{N}/``.
3. Wire the type in discovery / ``device_types.json`` as today.

Ensure ``fallback.icon`` remains set to ``unknown.png`` where applicable in ``device_types.json``.
