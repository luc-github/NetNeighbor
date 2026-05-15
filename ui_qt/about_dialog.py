# File about_dialog.py for NetNeighbor version 1.0.0
# License: LGPL3
"""About dialog — same content as GTK V1 (GitHub, license link, in-dialog Credits)."""

from __future__ import annotations

import html
from gettext import gettext as _

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ui.icons import resolve_app_logo_path
from utils.about_content import (
    GITHUB_PROJECT_URL,
    about_comments,
    about_copyright_line,
    about_license_notice_html,
    about_program_name,
    about_warranty_line,
    about_website_label,
    credits_html,
)
from utils.app_version import get_app_version

_PAGE_ABOUT = 0
_PAGE_CREDITS = 1


def _centered_label(
    text: str,
    *,
    rich: bool = False,
    external_links: bool = False,
) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    if rich:
        lbl.setTextFormat(Qt.TextFormat.RichText)
    if external_links:
        lbl.setOpenExternalLinks(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return lbl


class AboutDialog(QDialog):
    """Modal About box with Credits page in the same window (GTK-style)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("About NetNeighbor"))
        self.setModal(True)
        self.resize(480, 420)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_about_page())
        self._stack.addWidget(self._build_credits_page())
        self._stack.currentChanged.connect(self._on_page_changed)

        self._btn_credits = QPushButton(_("Credits"))
        self._btn_credits.clicked.connect(self._on_credits_clicked)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(_("Close"))
            close_btn.setDefault(True)
            close_btn.setAutoDefault(True)

        footer = QHBoxLayout()
        footer.addWidget(self._btn_credits)
        footer.addStretch(1)
        footer.addWidget(buttons)

        root = QVBoxLayout(self)
        root.addWidget(self._stack, stretch=1)
        root.addLayout(footer)

        self._on_page_changed(_PAGE_ABOUT)

    def _build_about_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 8)
        outer.addStretch(1)

        logo_lbl = QLabel()
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        logo_path = resolve_app_logo_path()
        if logo_path is not None:
            pix = QPixmap(str(logo_path))
            if not pix.isNull():
                logo_lbl.setPixmap(
                    pix.scaled(
                        128,
                        128,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        outer.addWidget(logo_lbl)
        outer.addSpacing(12)

        outer.addWidget(
            _centered_label(
                f"<b>{html_escape(about_program_name())}</b>", rich=True
            )
        )
        outer.addWidget(
            _centered_label(
                _("Version {version}").format(version=html_escape(get_app_version())),
                rich=True,
            )
        )
        outer.addSpacing(8)
        outer.addWidget(
            _centered_label(html_escape(about_comments()), rich=True)
        )
        outer.addSpacing(12)
        outer.addWidget(
            _centered_label(
                '<a href="{url}">{label}</a>'.format(
                    url=html_escape_attr(GITHUB_PROJECT_URL),
                    label=html_escape(about_website_label()),
                ),
                rich=True,
                external_links=True,
            )
        )
        outer.addSpacing(10)
        outer.addWidget(_centered_label(html_escape(about_warranty_line()), rich=True))
        outer.addSpacing(6)
        outer.addWidget(
            _centered_label(about_license_notice_html(), rich=True, external_links=True)
        )
        outer.addSpacing(12)
        outer.addWidget(_centered_label(html_escape(_("Author: Luc")), rich=True))
        outer.addWidget(
            _centered_label(html_escape(about_copyright_line()), rich=True)
        )
        outer.addStretch(1)

        return page

    def _build_credits_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 8)
        heading = QLabel(f"<b>{html_escape(_('Credits'))}</b>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(credits_html())
        browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        layout.addWidget(heading)
        layout.addWidget(browser, stretch=1)
        return page

    def _on_credits_clicked(self) -> None:
        if self._stack.currentIndex() == _PAGE_CREDITS:
            self._stack.setCurrentIndex(_PAGE_ABOUT)
        else:
            self._stack.setCurrentIndex(_PAGE_CREDITS)

    def _on_page_changed(self, index: int) -> None:
        if index == _PAGE_CREDITS:
            self._btn_credits.setText(_("About"))
        else:
            self._btn_credits.setText(_("Credits"))


def html_escape(text: str) -> str:
    return html.escape(text, quote=False)


def html_escape_attr(text: str) -> str:
    return html.escape(text, quote=True)


def show_about_dialog(parent: QWidget | None = None) -> None:
    AboutDialog(parent).exec()
