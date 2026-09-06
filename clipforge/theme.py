"""Application stylesheet (Catppuccin Mocha / High-Contrast)."""

from .constants import C


def stylesheet_for(colors):
    C = colors
    return f"""
QMainWindow, QWidget {{
    background-color: {C['base']};
    color: {C['text']};
    font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
    font-size: 13px;
}}

/* -- Sidebar -- */
#sidebar {{
    background-color: {C['mantle']};
    border-right: 1px solid {C['surface0']};
    min-width: 220px;
    max-width: 220px;
}}
#sidebarTitle {{
    color: {C['lavender']};
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}
#sectionLabel {{
    color: {C['overlay0']};
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    padding: 16px 16px 6px 16px;
    letter-spacing: 1.2px;
}}
.navBtn {{
    background: transparent;
    color: {C['subtext0']};
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0px;
    padding: 9px 16px 9px 13px;
    text-align: left;
    font-size: 13px;
    font-weight: 500;
    margin: 0px 0px;
}}
.navBtn:hover {{
    background-color: {C['surface0']};
    color: {C['text']};
    border-left: 3px solid {C['surface1']};
}}
.navBtn:focus {{
    border-left: 3px solid {C['blue']};
    color: {C['text']};
    outline: none;
}}
.navBtn[active="true"] {{
    background-color: {C['surface0']};
    color: {C['lavender']};
    font-weight: 600;
    border-left: 3px solid {C['lavender']};
}}

/* -- Cards / GroupBox -- */
QGroupBox {{
    border: 1px solid {C['surface0']};
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px;
    padding-top: 30px;
    font-weight: 600;
    color: {C['subtext1']};
    background-color: transparent;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: {C['lavender']};
    font-size: 12px;
}}

/* -- Buttons -- */
QPushButton {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: {C['surface1']};
    border-color: {C['surface2']};
}}
QPushButton:pressed {{
    background-color: {C['surface2']};
}}
QPushButton:focus {{
    border-color: {C['blue']};
    outline: none;
}}
QPushButton:disabled {{
    background-color: {C['surface0']};
    color: {C['overlay0']};
    border-color: {C['surface0']};
}}
QPushButton#primaryBtn {{
    background-color: {C['blue']};
    color: {C['crust']};
    border: none;
    font-weight: 600;
    padding: 8px 20px;
}}
QPushButton#primaryBtn:hover {{
    background-color: {C['lavender']};
}}
QPushButton#primaryBtn:pressed {{
    background-color: {C['sapphire']};
}}
QPushButton#primaryBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay0']};
}}
QPushButton#dangerBtn {{
    background-color: {C['red']};
    color: {C['crust']};
    border: none;
    font-weight: 600;
}}
QPushButton#dangerBtn:hover {{
    background-color: {C['flamingo']};
}}
QPushButton#dangerBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay0']};
}}
QPushButton#successBtn {{
    background-color: {C['green']};
    color: {C['crust']};
    border: none;
    font-weight: 600;
}}
QPushButton#successBtn:hover {{
    background-color: #b8eeac;
}}
QPushButton#successBtn:disabled {{
    background-color: {C['surface1']};
    color: {C['overlay0']};
}}
QPushButton.playerBtn {{
    background-color: transparent;
    border: none;
    color: {C['subtext0']};
    padding: 6px 10px;
    font-size: 13px;
    font-weight: 600;
    min-width: 34px;
    border-radius: 6px;
}}
QPushButton.playerBtn:hover {{
    color: {C['text']};
    background-color: {C['surface0']};
}}
QPushButton.playerBtn:focus {{
    color: {C['lavender']};
    outline: none;
}}

/* -- Inputs -- */
QComboBox {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 12px;
    min-height: 28px;
    font-size: 12px;
}}
QComboBox:focus {{
    border-color: {C['blue']};
}}
QComboBox:disabled {{
    color: {C['overlay0']};
    background-color: {C['mantle']};
    border-color: {C['surface0']};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {C['subtext0']};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    selection-background-color: {C['surface1']};
    selection-color: {C['lavender']};
    outline: none;
    padding: 4px;
}}
QSpinBox, QDoubleSpinBox {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 28px;
    font-size: 12px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {C['blue']};
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background-color: {C['surface1']};
    border: none;
    width: 20px;
    border-radius: 3px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {C['surface2']};
}}
QLineEdit {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 28px;
    font-size: 12px;
    selection-background-color: {C['blue']};
    selection-color: {C['crust']};
}}
QLineEdit:focus {{
    border-color: {C['blue']};
}}
QLineEdit:disabled {{
    color: {C['overlay0']};
    background-color: {C['mantle']};
}}

/* -- Sliders -- */
QSlider::groove:horizontal {{
    background: {C['surface0']};
    height: 5px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {C['lavender']};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {C['blue']};
}}
QSlider::handle:horizontal:pressed {{
    background: {C['sapphire']};
}}
QSlider::sub-page:horizontal {{
    background: {C['blue']};
    border-radius: 2px;
}}

/* -- Progress -- */
QProgressBar {{
    background-color: {C['surface0']};
    border: none;
    border-radius: 5px;
    height: 10px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {C['blue']};
    border-radius: 5px;
}}

/* -- Console -- */
#console {{
    background-color: {C['crust']};
    color: {C['overlay1']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11px;
    padding: 10px;
}}

/* -- Labels -- */
QLabel {{ color: {C['text']}; }}
.dimLabel {{ color: {C['subtext0']}; font-size: 11px; }}
.accentLabel {{ color: {C['lavender']}; font-weight: 600; font-size: 13px; }}

/* -- Splitters -- */
QSplitter::handle {{ background-color: {C['surface0']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* -- Scrollbars -- */
QScrollBar:vertical {{
    background: transparent; width: 8px; border-radius: 4px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {C['surface1']}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {C['surface2']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 8px; border-radius: 4px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {C['surface1']}; border-radius: 4px; min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{ background: {C['surface2']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* -- Checkboxes -- */
QCheckBox {{ color: {C['text']}; spacing: 8px; font-size: 12px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px;
    border: 2px solid {C['surface2']}; background: {C['surface0']};
}}
QCheckBox::indicator:hover {{
    border-color: {C['overlay0']};
}}
QCheckBox::indicator:checked {{
    background: {C['blue']}; border-color: {C['blue']};
}}
QCheckBox::indicator:disabled {{
    background: {C['mantle']}; border-color: {C['surface0']};
}}

/* -- File Info Bar -- */
#fileInfoBar {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    padding: 10px 14px;
}}

/* -- Lists -- */
QListWidget {{
    background-color: {C['mantle']};
    color: {C['text']};
    border: 1px solid {C['surface0']};
    border-radius: 6px;
    outline: none;
    padding: 4px;
    font-size: 12px;
}}
QListWidget::item {{
    padding: 7px 10px;
    border-radius: 5px;
}}
QListWidget::item:selected {{
    background-color: {C['surface0']};
    color: {C['lavender']};
}}
QListWidget::item:hover {{
    background-color: {C['surface0']};
}}

/* -- Toast -- */
#toast {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 10px;
    padding: 12px 24px;
    font-weight: 500;
    font-size: 13px;
}}

/* -- Status Bar -- */
QStatusBar {{
    background-color: {C['mantle']};
    color: {C['subtext0']};
    border-top: 1px solid {C['surface0']};
    font-size: 11px;
    padding: 3px 12px;
}}

/* -- Video Player -- */
#videoPlayer {{
    background-color: {C['crust']};
    border-radius: 8px;
}}
#playerControls {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    padding: 6px 10px;
}}
#thumbnailStrip {{
    background-color: {C['crust']};
    border: 1px solid {C['surface0']};
    border-radius: 6px;
    min-height: 48px;
    max-height: 48px;
}}

/* -- Command Preview -- */
#cmdPreview {{
    background-color: {C['crust']};
    color: {C['teal']};
    border: 1px solid {C['surface0']};
    border-radius: 8px;
    font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11px;
    padding: 10px;
    selection-background-color: {C['surface1']};
}}
#progressDetail {{
    color: {C['subtext0']};
    font-size: 11px;
    font-family: 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
}}

/* -- Streams -- */
#streamItem {{
    background-color: {C['surface0']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    padding: 8px 12px;
}}

/* -- Filter Sliders -- */
#filterSlider QSlider::groove:horizontal {{
    height: 4px;
}}

/* -- Tooltips -- */
QToolTip {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
}}
"""


STYLESHEET = stylesheet_for(C)
