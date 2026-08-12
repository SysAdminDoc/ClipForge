import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from clipforge.i18n import PROOF_LOCALE, TranslationCatalog, localize_widget_tree


ROOT = Path(__file__).resolve().parents[1]
_QT_APP = QApplication.instance() or QApplication([])


def test_desktop_catalog_rejects_placeholder_drift_and_supports_proof_locale():
    english = TranslationCatalog()
    proof = TranslationCatalog(PROOF_LOCALE)

    assert english.text("message.imported_files", count=3) == "Imported 3 file(s)"
    assert "⟦" in proof.text("message.imported_files", count=3)
    with pytest.raises(ValueError, match="missing"):
        english.text("message.imported_files")
    with pytest.raises(ValueError, match="extra"):
        english.text("message.imported_files", count=3, name="extra")


def test_desktop_widgets_restore_original_source_when_locale_changes():
    from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

    root = QWidget()
    label = QLabel("Select a clip on the timeline to view its properties", root)
    button = QPushButton("Export Diagnostics", root)
    label.show()
    button.show()

    localize_widget_tree(root, TranslationCatalog(PROOF_LOCALE))
    assert label.text().startswith("⟦")
    assert button.text().startswith("⟦")
    localize_widget_tree(root, TranslationCatalog())
    assert label.text() == "Select a clip on the timeline to view its properties"
    assert button.text() == "Export Diagnostics"
    root.close()


def test_browser_catalog_validates_placeholders_and_proof_strings():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser module tests")
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            """
const { createBrowserI18n, validateBrowserCatalog } = await import('./browser/i18n.mjs');
const english = createBrowserI18n('en');
const proof = createBrowserI18n('en-XA');
let missing = false;
try { english.t('importedFiles'); } catch (_) { missing = true; }
process.stdout.write(JSON.stringify({
    valid: validateBrowserCatalog(),
    english: english.t('importedFiles', { count: 2 }),
    proof: proof.t('importedFiles', { count: 2 }),
    missing,
}));
""",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert json.loads(result.stdout) == {
        "valid": True,
        "english": "Imported 2 file(s)",
        "proof": "⟦Iimmppoorrtteedd 2 ffiillee(ss)⟧",
        "missing": True,
    }
