"""Flux theme system. Multiple themes from color palettes + shared QSS template."""

from typing import Dict, List, Tuple


# --- Color Palettes ---

PALETTES: Dict[str, dict] = {
    "dark": {
        "name": "Flux Dark",
        "bg": "#0c0e14", "bg_card": "#12141c", "bg_hover": "#181b27",
        "bg_active": "#1a1e2e", "bg_input": "#0f1119", "bg_alt": "#0e1018",
        "border": "#1e2233", "border_light": "#262b3d",
        "text": "#e2e4ea", "text_muted": "#6b7089", "text_dim": "#4a4e64",
        "accent": "#3b82f6", "accent_hover": "#2563eb",
        "green": "#22c55e", "red": "#ef4444", "orange": "#f59e0b",
        "purple": "#a855f7", "cyan": "#06b6d4",
        "scrollbar": "#262b3d", "scrollbar_hover": "#363c54",
    },
    "midnight": {
        "name": "Midnight Blue",
        "bg": "#0a0f1e", "bg_card": "#0f1628", "bg_hover": "#152035",
        "bg_active": "#1a2840", "bg_input": "#0c1222", "bg_alt": "#0d1325",
        "border": "#1a2444", "border_light": "#243158",
        "text": "#d4daf0", "text_muted": "#5b6a8f", "text_dim": "#3e4d72",
        "accent": "#6366f1", "accent_hover": "#4f46e5",
        "green": "#34d399", "red": "#f87171", "orange": "#fbbf24",
        "purple": "#c084fc", "cyan": "#22d3ee",
        "scrollbar": "#243158", "scrollbar_hover": "#344570",
    },
    "dracula": {
        "name": "Dracula",
        "bg": "#282a36", "bg_card": "#2d303e", "bg_hover": "#343746",
        "bg_active": "#3a3d4e", "bg_input": "#21222c", "bg_alt": "#2a2c39",
        "border": "#44475a", "border_light": "#555870",
        "text": "#f8f8f2", "text_muted": "#6272a4", "text_dim": "#515a82",
        "accent": "#bd93f9", "accent_hover": "#a87bf0",
        "green": "#50fa7b", "red": "#ff5555", "orange": "#ffb86c",
        "purple": "#ff79c6", "cyan": "#8be9fd",
        "scrollbar": "#44475a", "scrollbar_hover": "#555870",
    },
    "nord": {
        "name": "Nord",
        "bg": "#2e3440", "bg_card": "#3b4252", "bg_hover": "#434c5e",
        "bg_active": "#4c566a", "bg_input": "#2e3440", "bg_alt": "#353b49",
        "border": "#434c5e", "border_light": "#4c566a",
        "text": "#eceff4", "text_muted": "#7b88a1", "text_dim": "#5e6b82",
        "accent": "#88c0d0", "accent_hover": "#81a1c1",
        "green": "#a3be8c", "red": "#bf616a", "orange": "#d08770",
        "purple": "#b48ead", "cyan": "#88c0d0",
        "scrollbar": "#4c566a", "scrollbar_hover": "#5e6b82",
    },
    "solarized": {
        "name": "Solarized Dark",
        "bg": "#002b36", "bg_card": "#073642", "bg_hover": "#0a3f4e",
        "bg_active": "#0d4a5a", "bg_input": "#002028", "bg_alt": "#003340",
        "border": "#0a4050", "border_light": "#1a5060",
        "text": "#eee8d5", "text_muted": "#657b83", "text_dim": "#586e75",
        "accent": "#268bd2", "accent_hover": "#1a7abb",
        "green": "#859900", "red": "#dc322f", "orange": "#cb4b16",
        "purple": "#6c71c4", "cyan": "#2aa198",
        "scrollbar": "#0a4050", "scrollbar_hover": "#1a5060",
    },
    "monokai": {
        "name": "Monokai Pro",
        "bg": "#1e1f1c", "bg_card": "#272822", "bg_hover": "#2e2f2a",
        "bg_active": "#363731", "bg_input": "#1a1b18", "bg_alt": "#22231f",
        "border": "#3e3f3a", "border_light": "#4e4f4a",
        "text": "#f8f8f2", "text_muted": "#75715e", "text_dim": "#5c5b4f",
        "accent": "#a6e22e", "accent_hover": "#94cc28",
        "green": "#a6e22e", "red": "#f92672", "orange": "#fd971f",
        "purple": "#ae81ff", "cyan": "#66d9ef",
        "scrollbar": "#3e3f3a", "scrollbar_hover": "#4e4f4a",
    },
}


def _hex_to_rgb(hex_color: str) -> str:
    """Convert '#RRGGBB' to 'R, G, B' for rgba() usage."""
    h = hex_color.lstrip("#")
    return f"{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}"


def get_theme_names() -> List[Tuple[str, str]]:
    """Return list of (key, display_name) tuples."""
    return [(k, v["name"]) for k, v in PALETTES.items()]


def get_palette(theme_key: str = "dark") -> dict:
    """Return the palette dict for the given theme."""
    return PALETTES.get(theme_key, PALETTES["dark"])


# --- Active Theme State ---

_active_key: str = "dark"
_active_palette: dict = PALETTES["dark"]


def set_current(theme_key: str):
    """Set the active theme. Call this when theme changes."""
    global _active_key, _active_palette
    _active_key = theme_key if theme_key in PALETTES else "dark"
    _active_palette = PALETTES[_active_key]


def current() -> dict:
    """Return the active theme palette dict."""
    return _active_palette


def active_key() -> str:
    """Return the active theme key string."""
    return _active_key


def c(key: str) -> str:
    """Shortcut: return a single color from the active palette."""
    return _active_palette.get(key, "#ff00ff")


def state_color(state) -> str:
    """Return theme-appropriate color for a TorrentState enum value."""
    from flux.core.torrent import TorrentState
    p = _active_palette
    mapping = {
        TorrentState.DOWNLOADING: p["accent"],
        TorrentState.SEEDING: p["green"],
        TorrentState.PAUSED: p["text_muted"],
        TorrentState.QUEUED: p["purple"],
        TorrentState.CHECKING: p["orange"],
        TorrentState.ERROR: p["red"],
        TorrentState.STALLED: p["orange"],
        TorrentState.COMPLETED: p["cyan"],
        TorrentState.METADATA: p["purple"],
        TorrentState.MOVING: p["orange"],
    }
    return mapping.get(state, p["text_muted"])


def get_stylesheet(theme_key: str = "dark") -> str:
    """Generate full QSS stylesheet for the given theme."""
    p = dict(PALETTES.get(theme_key, PALETTES["dark"]))
    # Pre-compute all RGB variants for rgba() usage in QSS template
    for key in ("accent", "red", "green", "cyan", "orange", "purple"):
        if key in p:
            p[f"{key}_rgb"] = _hex_to_rgb(p[key])
    return _QSS_TEMPLATE.format(**p)


_QSS_TEMPLATE = """
/* ===== GLOBAL ===== */
QWidget {{
    background-color: {bg};
    color: {text};
    font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 13px;
    selection-background-color: {accent};
    selection-color: #ffffff;
}}
QMainWindow {{ background-color: {bg}; }}

/* ===== MENU BAR ===== */
QMenuBar {{
    background-color: {bg_card};
    border-bottom: 1px solid {border};
    padding: 2px 8px; font-size: 12px;
}}
QMenuBar::item {{
    background: transparent; padding: 6px 12px;
    border-radius: 6px; color: {text_muted};
}}
QMenuBar::item:selected, QMenuBar::item:pressed {{
    background-color: {bg_hover}; color: {text};
}}
QMenu {{
    background-color: {bg_card};
    border: 1px solid {border_light};
    border-radius: 10px; padding: 4px;
}}
QMenu::item {{
    padding: 8px 32px 8px 12px;
    border-radius: 6px; font-size: 12px;
}}
QMenu::item:selected {{ background-color: {bg_hover}; color: {text}; }}
QMenu::separator {{ height: 1px; background-color: {border}; margin: 4px 8px; }}

/* ===== TOOLBAR ===== */
QToolBar {{
    background-color: {bg_card}; border: none;
    border-bottom: 1px solid {border};
    spacing: 4px; padding: 4px 8px;
}}
QToolBar::separator {{ width: 1px; background-color: {border}; margin: 4px 6px; }}
QToolButton {{
    background-color: transparent; border: none;
    border-radius: 8px; padding: 6px 12px;
    color: {text_muted}; font-size: 12px; font-weight: 500;
}}
QToolButton:hover {{ background-color: {bg_hover}; color: {text}; }}
QToolButton:pressed {{ background-color: {bg_active}; }}
QToolButton:checked {{ background-color: rgba({accent_rgb}, 0.15); color: {accent}; }}
QToolButton#accentButton {{ background-color: {accent}; color: #ffffff; font-weight: 600; }}
QToolButton#accentButton:hover {{ background-color: {accent_hover}; }}

/* ===== SPLITTER ===== */
QSplitter::handle {{ background-color: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ===== SIDEBAR ===== */
QTreeView#sidebar, QListWidget#sidebar {{
    background-color: {bg_card}; border: none;
    border-right: 1px solid {border}; outline: none; font-size: 12px;
}}
QTreeView#sidebar::item, QListWidget#sidebar::item {{
    padding: 7px 10px; border-radius: 8px;
    margin: 0px 6px 1px 6px; border: none;
}}
QTreeView#sidebar::item:hover, QListWidget#sidebar::item:hover {{
    background-color: {bg_hover};
}}
QTreeView#sidebar::item:selected, QListWidget#sidebar::item:selected {{
    background-color: rgba({accent_rgb}, 0.15); color: {accent};
}}
QTreeView#sidebar::branch {{ background-color: {bg_card}; border: none; }}

/* ===== TABLE / TREE ===== */
QTableView, QTreeWidget, QTableWidget {{
    background-color: {bg}; alternate-background-color: {bg_alt};
    border: none; gridline-color: {border}; outline: none; font-size: 12px;
}}
QTableView::item, QTreeWidget::item, QTableWidget::item {{
    padding: 8px 8px; border-bottom: 1px solid {border};
}}
QTableView::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background-color: rgba({accent_rgb}, 0.15); color: {text};
}}
QTableView::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover {{
    background-color: {bg_hover};
}}

/* ===== HEADER ===== */
QHeaderView {{ background-color: {bg}; border: none; }}
QHeaderView::section {{
    background-color: {bg}; color: {text_dim};
    border: none; border-bottom: 1px solid {border};
    border-right: 1px solid {border};
    padding: 8px 12px; font-size: 10px;
    font-weight: 700; text-transform: uppercase;
}}
QHeaderView::section:last {{ border-right: none; }}
QHeaderView::section:hover {{ color: {accent}; }}
QHeaderView::section:pressed {{ background-color: {bg_hover}; }}
QHeaderView::up-arrow {{
    width: 8px; height: 8px; image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid {accent};
    margin-left: 4px;
}}
QHeaderView::down-arrow {{
    width: 8px; height: 8px; image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {accent};
    margin-left: 4px;
}}

/* ===== SCROLLBARS ===== */
QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 0; border: none;
}}
QScrollBar::handle:vertical {{
    background: {scrollbar}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {scrollbar_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    height: 0; background: transparent; border: none;
}}
QScrollBar:horizontal {{
    background: transparent; height: 8px; margin: 0; border: none;
}}
QScrollBar::handle:horizontal {{
    background: {scrollbar}; border-radius: 4px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {scrollbar_hover}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    width: 0; background: transparent; border: none;
}}

/* ===== TABS ===== */
QTabWidget::pane {{
    border: none; background-color: {bg_card};
    border-top: 1px solid {border};
}}
QTabBar {{ background-color: {bg_card}; }}
QTabBar::tab {{
    background-color: transparent; color: {text_muted};
    border: none; border-bottom: 2px solid transparent;
    padding: 10px 16px; font-size: 11px;
    font-weight: 600; text-transform: uppercase;
}}
QTabBar::tab:hover {{ color: {text}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}

/* ===== BUTTONS ===== */
QPushButton {{
    background-color: {bg_hover}; color: {text};
    border: 1px solid {border_light}; border-radius: 8px;
    padding: 8px 20px; font-weight: 500; font-size: 12px;
}}
QPushButton:hover {{ background-color: {bg_active}; border-color: {scrollbar_hover}; }}
QPushButton:pressed {{ background-color: {bg_card}; }}
QPushButton#primary {{
    background-color: {accent}; color: #ffffff;
    border: none; font-weight: 600;
}}
QPushButton#primary:hover {{ background-color: {accent_hover}; }}
QPushButton#danger {{
    background-color: transparent; color: {red}; border-color: {red};
}}
QPushButton#danger:hover {{ background-color: rgba({red_rgb}, 0.1); }}

/* ===== INPUT FIELDS ===== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {bg_input}; color: {text};
    border: 1px solid {border}; border-radius: 8px;
    padding: 8px 12px; font-size: 13px;
    selection-background-color: {accent};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {text_dim}; background-color: {bg};
}}

/* --- ComboBox dropdown --- */
QComboBox::drop-down {{
    border: none; width: 28px;
    subcontrol-origin: padding; subcontrol-position: center right;
}}
QComboBox::down-arrow {{
    width: 10px; height: 10px;
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {text_muted};
    margin-right: 8px;
}}
QComboBox::down-arrow:hover {{ border-top-color: {accent}; }}
QComboBox QAbstractItemView {{
    background-color: {bg_card}; border: 1px solid {border_light};
    border-radius: 8px; selection-background-color: {bg_hover};
    selection-color: {text};
    outline: none; padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 12px; border-radius: 4px; color: {text};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {bg_hover}; color: {text};
}}

/* --- SpinBox buttons --- */
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; border: none;
    border-left: 1px solid {border}; border-top-right-radius: 8px;
    background-color: {bg_hover};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; border: none;
    border-left: 1px solid {border}; border-bottom-right-radius: 8px;
    background-color: {bg_hover};
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {bg_active};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 8px; height: 8px; image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 4px solid {text_muted};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 8px; height: 8px; image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 4px solid {text_muted};
}}

/* ===== CHECKBOX ===== */
QCheckBox {{ spacing: 8px; color: {text}; font-size: 12px; }}
QCheckBox:disabled {{ color: {text_dim}; }}
QCheckBox::indicator {{
    width: 18px; height: 18px;
    border: 2px solid {border_light}; border-radius: 4px;
    background-color: {bg_input};
}}
QCheckBox::indicator:checked {{ background-color: {accent}; border-color: {accent}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:disabled {{ border-color: {border}; background-color: {bg}; }}

/* ===== RADIO BUTTON ===== */
QRadioButton {{ spacing: 8px; color: {text}; font-size: 12px; }}
QRadioButton:disabled {{ color: {text_dim}; }}
QRadioButton::indicator {{
    width: 18px; height: 18px;
    border: 2px solid {border_light}; border-radius: 10px;
    background-color: {bg_input};
}}
QRadioButton::indicator:checked {{ background-color: {accent}; border-color: {accent}; }}
QRadioButton::indicator:hover {{ border-color: {accent}; }}
QRadioButton::indicator:disabled {{ border-color: {border}; background-color: {bg}; }}

/* ===== GROUP BOX ===== */
QGroupBox {{
    border: 1px solid {border}; border-radius: 10px;
    margin-top: 12px; padding: 16px;
    font-weight: 600; font-size: 11px; color: {text_muted};
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 16px; padding: 0 8px;
    color: {text_dim}; text-transform: uppercase; letter-spacing: 1px;
}}

/* ===== PROGRESS BAR ===== */
QProgressBar {{
    background-color: {border}; border: none;
    border-radius: 3px; text-align: center;
    color: transparent; max-height: 6px; min-height: 6px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent}, stop:1 {cyan});
    border-radius: 3px;
}}

/* ===== STATUS BAR ===== */
QStatusBar {{
    background-color: {bg}; border-top: 1px solid {border};
    color: {text_dim}; font-size: 11px; padding: 2px 12px;
}}
QStatusBar::item {{ border: none; }}

/* ===== DIALOG ===== */
QDialog {{ background-color: {bg_card}; border: 1px solid {border}; }}
QDialog QLabel {{ background-color: transparent; }}

/* ===== MESSAGE BOX ===== */
QMessageBox {{ background-color: {bg_card}; }}
QMessageBox QLabel {{ color: {text}; font-size: 13px; background-color: transparent; }}
QMessageBox QPushButton {{ min-width: 80px; }}
QInputDialog {{ background-color: {bg_card}; }}

/* ===== TEXT EDIT ===== */
QTextEdit, QPlainTextEdit {{
    background-color: {bg_input}; color: {text};
    border: 1px solid {border}; border-radius: 8px;
    padding: 8px; font-size: 13px;
    selection-background-color: {accent};
}}
QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}

/* ===== TOOLTIP ===== */
QToolTip {{
    background-color: {bg_card}; color: {text};
    border: 1px solid {border_light}; border-radius: 6px;
    padding: 6px 10px; font-size: 11px;
}}

/* ===== LABELS ===== */
QLabel#sectionTitle {{
    color: {text_dim}; font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 1px;
}}
QLabel#statValue {{
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px; font-weight: 600;
}}
QLabel#statLabel {{ color: {text_dim}; font-size: 10px; }}
QLabel#brandName {{ font-size: 15px; font-weight: 700; letter-spacing: -0.3px; }}
QLabel#mono {{
    font-family: "Cascadia Code", "Consolas", monospace; font-size: 11px;
}}

/* ===== FRAMES ===== */
QFrame#card {{
    background-color: {bg_card}; border: 1px solid {border}; border-radius: 10px;
}}
QFrame#statsCard {{
    background-color: {bg_card}; border: 1px solid {border};
    border-radius: 10px; padding: 8px 14px;
}}

/* ===== SLIDER ===== */
QSlider::groove:horizontal {{
    border: none; height: 4px; background: {border}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {accent}; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
"""

# Backwards compatibility
STYLESHEET = get_stylesheet("dark")
COLORS = PALETTES["dark"]
