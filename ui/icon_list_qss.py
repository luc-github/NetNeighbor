# File icon_list_qss.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Shared QSS for icon-mode ``QListWidget`` tiles (flat list + grouped sections).

Uses ``palette(...)`` roles so light/dark and high-contrast themes propagate from
``QApplication``.  The 1px transparent border is kept in all states to prevent
layout shifts (border-box sizing stays constant).

Hover/selected color contract (mirrors the rest of the UI):
  hover    → palette(alternate-base)  light blue tint
  selected → palette(alternate-base)  same tint, stays lit after click (no border)
"""

# 1px transparent border in every state avoids layout jumps on state changes.
ICON_MODE_LIST_QSS = (
    "QListWidget { background-color: palette(base); outline: none; show-decoration-selected: 0; }"
    "QListWidget::item {"
    " padding: 2px;"
    " border: 1px solid transparent;"
    " font-size: smaller;"
    "}"
    "QListWidget::item:hover {"
    " background-color: palette(alternate-base);"
    "}"
    "QListWidget::item:selected,"
    "QListWidget::item:selected:active,"
    "QListWidget::item:selected:!active {"
    " background-color: palette(base);"
    " color: palette(text);"
    " border: 1px solid transparent;"
    " outline: none;"
    "}"
    "QListWidget::item:selected:hover {"
    " background-color: palette(alternate-base);"
    " color: palette(text);"
    " border: 1px solid transparent;"
    " outline: none;"
    "}"
    "QListWidget::item:focus { border: 1px solid transparent; outline: none; }"
)
