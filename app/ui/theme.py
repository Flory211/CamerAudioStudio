from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtCore import Qt, QRectF


STYLE = """
QMainWindow, QDialog { background: #101218; color: #e8edf5; }
QWidget { color: #dce2ed; font-family: 'Segoe UI'; font-size: 12px; }
QToolTip { background: #30394b; color: white; border: 1px solid #4a5971; padding: 6px; }
QFrame#Header { background: #171a22; border-bottom: 1px solid #2a2f3c; }
QFrame#Card { background: #191d26; border: 1px solid #2b3140; border-radius: 10px; }
QLabel#Brand { font-size: 19px; font-weight: 650; color: #f3f6ff; }
QLabel#PageTitle { font-size: 17px; font-weight: 600; }
QLabel#Section { font-size: 10px; font-weight: 700; color: #9aa8be; letter-spacing: 1.4px; }
QLabel#Muted { color: #8995aa; font-size: 11px; }
QLabel#Chip { background: #23354d; color: #9cc8ff; border: 1px solid #324c6e;
             border-radius: 5px; padding: 4px 8px; font-size: 10px; }
QLabel#Alert { color: #f3b76b; }
QPushButton, QToolButton { background: #262d3b; color: #dce4f1; border: 1px solid #3a4559;
    border-radius: 6px; padding: 7px 11px; min-height: 16px; }
QPushButton:hover, QToolButton:hover { background: #323e52; border-color: #5b7294; }
QPushButton:pressed { background: #1a2230; }
QPushButton:disabled { color: #68748a; border-color: #2b3342; background: #202631; }
QPushButton:checked { color: #bcd9ff; background: #203d62; border-color: #4b91ec; }
QPushButton#Primary { background: #3479de; border: 1px solid #5494ef; color: white; font-weight: 600; }
QPushButton#Primary:hover { background: #448bf1; }
QPushButton#Record { background: #3b2330; color: #ffb9c6; border-color: #714052; }
QPushButton#Record:checked { background: #ad3657; color: white; border-color: #df587a; }
QPushButton#Tiny { padding: 2px 8px; min-height: 20px; font-size: 14px; }
QListWidget { background: transparent; border: none; outline: none; padding: 2px; }
QListWidget::item { padding: 10px 10px; border-radius: 6px; margin: 2px; border: 1px solid transparent; }
QListWidget::item:selected { background: #263d5c; border-color: #405f86; color: #d6e8ff; }
QListWidget::item:hover:!selected { background: #252d3a; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #111722; color: #e0e8f6; border: 1px solid #354259; border-radius: 5px;
    padding: 6px; selection-background-color: #3d77ba; min-height: 17px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #5b9df5; }
QComboBox QAbstractItemView { background: #202938; color: #e0e8f6; selection-background-color: #365985; }
QComboBox::drop-down { border: none; width: 18px; }
QCheckBox { spacing: 7px; padding: 3px 0; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #52627b; border-radius: 4px; background: #141c29; }
QCheckBox::indicator:checked { background: #5595ed; border-color: #84b4f4; }
QSlider::groove:horizontal { background: #303c50; height: 4px; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #6199e8; border-radius: 2px; }
QSlider::handle:horizontal { background: #d4e5ff; width: 12px; margin: -4px 0; border-radius: 6px; }
QProgressBar { border: none; background: #10151e; border-radius: 3px; color: transparent; max-height: 7px; }
QProgressBar::chunk { background: #50bc9c; border-radius: 3px; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; color: #93a0b7; padding: 10px 9px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #a8cdff; border-bottom-color: #68a5fa; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
QScrollBar::handle:vertical { background: #3c485f; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: #3c5c85; }
QMenu { background: #202938; border: 1px solid #465775; padding: 5px; }
QMenu::item { padding: 8px 22px; border-radius: 4px; }
QMenu::item:selected { background: #365985; }
QMenuBar { background: #171a22; }
QStatusBar { background: #151922; color: #95a4bd; border-top: 1px solid #2b3342; }
QGroupBox { border: 1px solid #303c50; border-radius: 6px; margin-top: 14px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 5px; color: #a6b9d4; }
"""


def studio_icon(size=64):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#397ee3'))
    painter.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), size * .25, size * .25)
    painter.setBrush(QColor('#e5f0ff'))
    painter.drawRoundedRect(QRectF(size * .19, size * .3, size * .43, size * .4), 4, 4)
    from PyQt6.QtGui import QPolygonF
    from PyQt6.QtCore import QPointF
    painter.drawPolygon(QPolygonF([QPointF(size * .65, size * .4), QPointF(size * .82, size * .3),
                                   QPointF(size * .82, size * .7), QPointF(size * .65, size * .6)]))
    painter.end()
    return QIcon(pixmap)
