# Contributing Icons

## Required format
- PNG required, SVG optional
- Minimum size: 64x64
- Recommended size: 256x256
- Transparent background

## Naming
- Use `{type_id}.png` naming, for example `esp32.png`

## Registering an icon
1. Add the icon file under `assets/icons/`.
2. Add or update corresponding mapping in `data/device_types.json`.
3. Ensure `fallback.icon` remains set to `unknown.png`.
