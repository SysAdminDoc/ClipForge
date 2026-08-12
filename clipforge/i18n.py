"""Small, strict translation layer shared by the desktop UI.

ClipForge intentionally keeps English as the built-in default.  The catalog is
keyed by stable message ids, validates named placeholders, and exposes a
pseudo-locale used by layout tests.  Keeping the formatter here means a future
catalog can be added without allowing malformed translations to reach widgets.
"""

from __future__ import annotations

import os
import re
import string
from collections.abc import Mapping
from typing import Any

DEFAULT_LOCALE = "en"
PROOF_LOCALE = "en-XA"
LOCALE_ENV = "CLIPFORGE_LOCALE"

MESSAGES: dict[str, str] = {
    "app.name": "ClipForge",
    "app.window_title": "ClipForge — Local Video Editor",
    "nav.edit": "EDIT",
    "nav.tools": "TOOLS",
    "nav.recent": "RECENT",
    "nav.trim": "Trim",
    "nav.crop": "Crop & Rotate",
    "nav.ai": "AI Enhance",
    "nav.convert": "Convert",
    "nav.filters": "Filters",
    "nav.audio": "Audio",
    "nav.streams": "Streams",
    "nav.batch": "Batch",
    "nav.trim_tip": "Cut segments from video",
    "nav.crop_tip": "Crop, rotate, flip video",
    "nav.ai_tip": "Upscale resolution, boost frame rate",
    "nav.convert_tip": "Codec, format, resolution, speed",
    "nav.filters_tip": "Color, stabilize, denoise, subtitles",
    "nav.audio_tip": "Extract, replace, or remove audio",
    "nav.streams_tip": "Inspect media info, remux streams",
    "nav.batch_tip": "Process multiple files at once",
    "theme.high_contrast": "High contrast",
    "theme.high_contrast_tip": "Switch between the standard and high-contrast color themes",
    "status.gpu_checking": "GPU: Checking capabilities…",
    "console.title": "Console",
    "console.filter_tip": "Filter log messages by level",
    "console.cancel_jobs": "Cancel active jobs",
    "console.cancel_jobs_tip": "Cancel every active media, inspection, preview, or install job",
    "console.export_diagnostics": "Export Diagnostics",
    "console.export_diagnostics_tip": "Save bounded job diagnostics with file paths redacted; media is never included",
    "console.copy_markdown": "Copy as Markdown",
    "console.copy_markdown_tip": "Copy console output formatted as Markdown (for bug reports)",
    "console.clear": "Clear",
    "console.clear_tip": "Clear console output",
    "console.placeholder": "FFmpeg output will appear here",
    "status.loading": "Loading…",
    "status.ready": "Ready",
    "status.engine_unavailable": "Engine unavailable",
    "status.ffmpeg_found": "FFmpeg: Found",
    "status.ffmpeg_missing": "FFmpeg: Missing",
    "status.gpu_unavailable": "GPU: FFmpeg unavailable",
    "status.gpu_cancelled": "GPU: Capability check cancelled",
    "status.gpu_no_encoders": "GPU: No advertised encoders",
    "message.unknown_error": "Unknown error",
    "message.imported_files": "Imported {count} file(s)",
    "message.loaded_file": "Loaded: {name}",
}

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _placeholder_names(template: str) -> set[str]:
    names: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(template):
        if field_name is None:
            continue
        if not _IDENTIFIER_RE.match(field_name):
            raise ValueError(f"unsupported placeholder {field_name!r}")
        names.add(field_name)
    return names


def _pseudo_localize(template: str) -> str:
    """Make English strings longer while preserving named placeholders."""

    pieces: list[str] = []
    for literal, field_name, format_spec, conversion in string.Formatter().parse(template):
        expanded = "".join(
            f"{char}{char.lower()}" if char.isalpha() else char
            for char in literal
        )
        pieces.append(expanded)
        if field_name is not None:
            token = "{" + field_name
            if conversion:
                token += "!" + conversion
            if format_spec:
                token += ":" + format_spec
            pieces.append(token + "}")
    return f"⟦{''.join(pieces)}⟧"


class TranslationCatalog:
    """Resolve and strictly format a catalog for one UI locale."""

    def __init__(self, locale: str | None = None, messages: Mapping[str, str] | None = None):
        self.locale = normalize_locale(locale)
        self._messages = dict(MESSAGES)
        if messages:
            self._messages.update(messages)

    def template(self, key: str) -> str:
        source = self._messages.get(key, key)
        if self.locale == PROOF_LOCALE:
            return _pseudo_localize(source)
        return source

    def text(self, key: str, **values: Any) -> str:
        source = self._messages.get(key, key)
        template = self.template(key)
        expected = _placeholder_names(source)
        actual = set(values)
        if expected != actual:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details = []
            if missing:
                details.append(f"missing={missing}")
            if extra:
                details.append(f"extra={extra}")
            raise ValueError(f"invalid placeholders for {key!r}: {', '.join(details)}")
        return template.format(**values)

    def text_for_source(self, source: str) -> str:
        """Translate an existing English widget string without a key migration."""

        if not source or not any(char.isalpha() for char in source):
            return source
        for key, value in self._messages.items():
            if value == source:
                return self.text(key)
        if self.locale == PROOF_LOCALE:
            return _pseudo_localize(source)
        return source


def normalize_locale(locale: str | None) -> str:
    value = str(locale or DEFAULT_LOCALE).strip().replace("_", "-")
    if not value:
        return DEFAULT_LOCALE
    if value.lower() in {"pseudo", "proof", "en-xa"}:
        return PROOF_LOCALE
    return DEFAULT_LOCALE if value.lower() in {"en", "en-us", "en-gb"} else value


def catalog_for_environment() -> TranslationCatalog:
    return TranslationCatalog(os.environ.get(LOCALE_ENV, DEFAULT_LOCALE))


def _remember(widget: Any, attribute: str, value: str) -> str:
    property_name = f"_cf_i18n_{attribute}"
    source = widget.property(property_name)
    if source is None:
        source = value
        widget.setProperty(property_name, source)
    return str(source)


def localize_widget_tree(root: Any, catalog: TranslationCatalog | None = None) -> TranslationCatalog:
    """Apply translations to a Qt widget tree and return the catalog used.

    The original English value is retained as a dynamic property, so a future
    locale switch can re-apply translations without translating a translation.
    User-entered media names and console contents are intentionally untouched.
    """

    catalog = catalog or catalog_for_environment()
    try:
        from PyQt6.QtWidgets import (
            QAbstractButton,
            QComboBox,
            QLabel,
            QLineEdit,
            QTextEdit,
            QWidget,
        )
    except ImportError:  # pragma: no cover - lets catalog use stay headless
        return catalog

    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        if widget.property("_cf_i18n_skip"):
            continue
        if hasattr(widget, "windowTitle"):
            title = widget.windowTitle()
            if title:
                widget.setWindowTitle(catalog.text_for_source(_remember(widget, "window_title", title)))
        if isinstance(widget, (QLabel, QAbstractButton)):
            value = widget.text()
            if value:
                widget.setText(catalog.text_for_source(_remember(widget, "text", value)))
        if isinstance(widget, QComboBox):
            sources = widget.property("_cf_i18n_items")
            if sources is None:
                sources = [widget.itemText(index) for index in range(widget.count())]
                widget.setProperty("_cf_i18n_items", sources)
            for index, source in enumerate(sources):
                widget.setItemText(index, catalog.text_for_source(str(source)))
        if isinstance(widget, (QLineEdit, QTextEdit)):
            placeholder = widget.placeholderText()
            if placeholder:
                widget.setPlaceholderText(
                    catalog.text_for_source(_remember(widget, "placeholder", placeholder))
                )
        for attribute, getter, setter in (
            ("tooltip", widget.toolTip, widget.setToolTip),
            ("accessible_name", widget.accessibleName, widget.setAccessibleName),
            ("accessible_description", widget.accessibleDescription, widget.setAccessibleDescription),
        ):
            value = getter()
            if value:
                setter(catalog.text_for_source(_remember(widget, attribute, value)))
    return catalog


__all__ = [
    "DEFAULT_LOCALE",
    "LOCALE_ENV",
    "MESSAGES",
    "PROOF_LOCALE",
    "TranslationCatalog",
    "catalog_for_environment",
    "localize_widget_tree",
    "normalize_locale",
]
