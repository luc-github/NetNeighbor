Bundled device-type icons (all platforms)
==========================================

Place PNGs under square folders named ``NxN`` (e.g. ``1024x1024``, ``64x64``,
``16x16``). Subfolder names are discovered automatically; any ladder of sizes
works.

Basenames used per device **type** are defined in **``config/icons.json``**
(ordered fallback list per slug). GTK and Qt read the same file; optional user
overlay: ``~/.config/netneighbor/icons.json`` (same ``types`` shape).

Example::

    netneighbor/512x512/printer-network.png
    netneighbor/64x64/printer-network.png

Optional: flat ``<basename>.png`` or ``.svg`` in this directory.

User-selected icons: ``~/.config/netneighbor/custom_icons/*.png`` (see app docs).
