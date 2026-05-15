# File icon_list_qss.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Shared QSS for icon-mode ``QListWidget`` tiles (flat list + grouped sections).

Uses ``palette(...)`` roles so light/dark and high-contrast themes propagate from
``QApplication``. Selection is drawn as a frame (not a solid highlight fill) so
device icons stay visible; hover uses ``alternate-base`` like many Fusion-style
list rows. This is still stylesheet-driven, so it will not match pixel-perfect
native Win32 list hover (alpha accent), but it tracks the active Qt palette better
than hard-coded RGB fills.
"""

ICON_MODE_LIST_QSS = (
    "QListWidget { background-color: palette(base); outline: none; show-decoration-selected: 0; }"
    "QListWidget::item { padding: 2px; border: 1px solid transparent; font-size: smaller; }"
    "QListWidget::item:hover {"
    " background-color: palette(alternate-base);"
    "}"
    "QListWidget::item:selected, QListWidget::item:selected:active {"
    " background-color: palette(base);"
    " color: palette(text);"
    " border: 2px solid palette(highlight);"
    " outline: none;"
    "}"
    "QListWidget::item:selected:!active {"
    " background-color: palette(base);"
    " color: palette(text);"
    " border: 2px solid palette(mid);"
    " outline: none;"
    "}"
    "QListWidget::item:selected:hover {"
    " background-color: palette(alternate-base);"
    " color: palette(text);"
    " border: 2px solid palette(highlight);"
    " outline: none;"
    "}"
    "QListWidget::item:focus { border: 1px solid transparent; outline: none; }"
)
