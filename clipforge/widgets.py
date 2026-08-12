"""Reusable UI widgets: Toast, RangeSlider, ThumbnailStrip, CropView, VideoPlayer, FileInfoBar."""

import os
from dataclasses import asdict
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider,
    QComboBox, QFileDialog, QGraphicsView, QGraphicsScene, QProgressBar,
    QStackedWidget, QLayout, QSpacerItem, QSizePolicy,
    QAbstractSpinBox, QLineEdit, QTextEdit, QMessageBox,
)
from PyQt6.QtCore import (
    Qt, QUrl, QTimer, QPoint, QPointF, QPropertyAnimation, QEasingCurve, QRect,
    QSize,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QPolygonF,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from clipforge_utils import (
    format_duration, format_duration_short, format_size, format_bitrate,
    validate_media_path,
)
from .constants import C
from .tools import FFMPEG
from .settings import add_recent
from .diagnostics import DIAGNOSTICS
from .workers import FFmpegWorker, MediaProbeWorker, ThumbnailWorker
from .proxy import ProxyCache
from .mpv_backend import MpvWidget, probe_mpv


# ---------------------------------------------------------------------------
# Responsive layout and accessibility
# ---------------------------------------------------------------------------


class FlowLayout(QLayout):
    """A compact row that wraps controls instead of forcing horizontal scroll."""

    def __init__(self, parent=None, margin=-1, horizontal_spacing=6, vertical_spacing=6):
        super().__init__(parent)
        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)
        self._items = []
        self._horizontal_spacing = horizontal_spacing
        self._vertical_spacing = vertical_spacing

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, widget, _stretch=0, alignment=Qt.AlignmentFlag(0)):
        super().addWidget(widget)
        if alignment:
            self.setAlignment(widget, alignment)

    def addStretch(self, _stretch=0):
        self.addItem(
            QSpacerItem(
                0,
                0,
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        )

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        available = rect.adjusted(
            margins.left(),
            margins.top(),
            -margins.right(),
            -margins.bottom(),
        )
        x = available.x()
        y = available.y()
        line_height = 0
        horizontal = max(0, self._horizontal_spacing)
        vertical = max(0, self._vertical_spacing)

        for item in self._items:
            hint = item.sizeHint()
            if hint.isEmpty():
                continue
            next_x = x + hint.width() + horizontal
            if x > available.x() and next_x - horizontal > available.right() + 1:
                x = available.x()
                y += line_height + vertical
                next_x = x + hint.width() + horizontal
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


def ensure_accessible_control_names(owner):
    """Name owner controls from stable attributes when no explicit name exists."""

    control_types = (
        QComboBox,
        QAbstractSpinBox,
        QSlider,
        QLineEdit,
        QTextEdit,
        QProgressBar,
    )
    prefixes = (
        "cmb_",
        "spn_",
        "txt_",
        "chk_",
        "btn_",
        "lbl_",
    )
    suffixes = {
        "fmt": "format",
        "cmd": "command",
        "cs": "contact sheet",
        "db": "decibels",
        "dur": "duration",
    }
    for attribute, control in vars(owner).items():
        if not isinstance(control, control_types) or control.accessibleName().strip():
            continue
        name = attribute
        for prefix in prefixes:
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        words = [suffixes.get(word, word) for word in name.split("_") if word]
        if words:
            control.setAccessibleName(" ".join(words).capitalize())


# ---------------------------------------------------------------------------
# Toast
# ---------------------------------------------------------------------------


class Toast(QLabel):
    """Auto-dismissing notification overlay with slide animation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(44)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def show_message(self, text, color=None, duration=3000):
        c = color or C["green"]
        full_text = str(text)
        self.setStyleSheet(
            f"background: {C['surface0']}; color: {c}; border: 1px solid {C['surface1']}; "
            f"border-radius: 8px; padding: 10px 24px; font-weight: 600; font-size: 13px;"
        )
        if self.parent():
            pw = self.parent().width()
            sidebar_w = 220
            content_w = pw - sidebar_w
            toast_w = min(content_w - 40, 500)
            self.setFixedWidth(toast_w)
            visible_text = (
                full_text
                if len(full_text) <= 45
                else full_text[:42].rstrip() + "..."
            )
            self.setText(visible_text)
            self.setAlignment(
                Qt.AlignmentFlag.AlignCenter
                if visible_text == full_text
                else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self.setToolTip(full_text if visible_text != full_text else "")
            self.setAccessibleName(full_text)
            target_x = sidebar_w + (content_w - toast_w) // 2
            self.move(target_x, -50)
            self.show()
            self.raise_()
            # Slide-down animation
            self._anim = QPropertyAnimation(self, b"geometry")
            self._anim.setDuration(300)
            self._anim.setStartValue(QRect(target_x, -50, toast_w, 44))
            self._anim.setEndValue(QRect(target_x, 8, toast_w, 44))
            self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
            self._anim.start()
        else:
            self.setText(full_text)
            self.setAccessibleName(full_text)
            self.show()
            self.raise_()
        self._timer.start(duration)

    def _fade_out(self):
        if self.parent():
            self._anim_out = QPropertyAnimation(self, b"geometry")
            self._anim_out.setDuration(200)
            self._anim_out.setStartValue(self.geometry())
            end = QRect(self.geometry())
            end.moveTop(-50)
            self._anim_out.setEndValue(end)
            self._anim_out.setEasingCurve(QEasingCurve.Type.InQuad)
            self._anim_out.finished.connect(self.hide)
            self._anim_out.start()
        else:
            self.hide()


# ---------------------------------------------------------------------------
# RangeSlider
# ---------------------------------------------------------------------------


class RangeSlider(QWidget):
    rangeChanged = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min = 0.0
        self._max = 1.0
        self._low = 0.0
        self._high = 1.0
        self._pressed = None
        self._active_handle = "low"
        self.setMinimumHeight(36)
        self.setMinimumWidth(200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Trim range")
        self._update_accessible_description()

    def set_limits(self, minimum, maximum):
        self._min = float(minimum)
        self._max = max(float(maximum), self._min)
        self.set_range(self._min, self._max)

    def set_range(self, low, high):
        self._low = max(self._min, min(low, self._high))
        self._high = min(self._max, max(high, self._low))
        self._update_accessible_description()
        self.update()
        self.rangeChanged.emit(self._low, self._high)

    def _update_accessible_description(self):
        self.setAccessibleDescription(
            f"Start {self._low:.3f} seconds, end {self._high:.3f} seconds. "
            "Use Up or Down to choose the end or start handle, then Left or "
            "Right to adjust it."
        )

    def low(self):
        return self._low

    def high(self):
        return self._high

    def _val_to_x(self, val):
        w = self.width() - 20
        return 10 + (val - self._min) / max(self._max - self._min, 0.001) * w

    def _x_to_val(self, x):
        w = self.width() - 20
        return self._min + (x - 10) / max(w, 1) * (self._max - self._min)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        track_y = h // 2 - 3
        track_h = 6
        p.setBrush(QColor(C["surface0"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(10, track_y, self.width() - 20, track_h, 3, 3)
        x_low = self._val_to_x(self._low)
        x_high = self._val_to_x(self._high)
        p.setBrush(QColor(C["blue"]))
        p.drawRoundedRect(int(x_low), track_y, int(x_high - x_low), track_h, 3, 3)
        for handle, val in (("low", self._low), ("high", self._high)):
            x = self._val_to_x(val)
            p.setBrush(QColor(C["lavender"]))
            outline = C["yellow"] if self.hasFocus() and handle == self._active_handle else C["surface0"]
            p.setPen(QPen(QColor(outline), 2))
            p.drawEllipse(QPointF(x, h / 2), 8, 8)
        p.end()

    def mousePressEvent(self, event):
        x = event.position().x()
        if abs(x - self._val_to_x(self._low)) < abs(x - self._val_to_x(self._high)):
            self._pressed = "low"
        else:
            self._pressed = "high"
        self._active_handle = self._pressed
        self.setFocus()
        self._update_from_mouse(x)

    def mouseMoveEvent(self, event):
        if self._pressed:
            self._update_from_mouse(event.position().x())

    def mouseReleaseEvent(self, event):
        self._pressed = None

    def _update_from_mouse(self, x):
        val = max(self._min, min(self._max, self._x_to_val(x)))
        if self._pressed == "low":
            self._low = min(val, self._high - 0.001)
        elif self._pressed == "high":
            self._high = max(val, self._low + 0.001)
        self.update()
        self._update_accessible_description()
        self.rangeChanged.emit(self._low, self._high)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Up:
            self._active_handle = "high"
            self.update()
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            self._active_handle = "low"
            self.update()
            event.accept()
            return
        direction = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_PageDown: -10,
            Qt.Key.Key_PageUp: 10,
        }.get(key)
        if direction is not None:
            step = max((self._max - self._min) / 1000, 0.001)
            if self._active_handle == "low":
                self.set_range(self._low + direction * step, self._high)
            else:
                self.set_range(self._low, self._high + direction * step)
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# ThumbnailStrip
# ---------------------------------------------------------------------------


class ThumbnailStrip(QWidget):
    """Displays thumbnail filmstrip under the seek bar."""
    clicked = pyqtSignal(float)  # position ratio 0-1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("thumbnailStrip")
        self._thumbnails = []
        self._position = 0.0
        self.setMinimumHeight(48)
        self.setMaximumHeight(48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_thumbnails(self, thumbs):
        self._thumbnails = thumbs
        self.update()

    def set_position(self, ratio):
        self._position = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, event):
        if not self._thumbnails:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = self.width()
        h = self.height()
        n = len(self._thumbnails)
        tw = w / n
        for i, thumb in enumerate(self._thumbnails):
            if not thumb.isNull():
                x = int(i * tw)
                p.drawPixmap(x, 0, int(tw), h, thumb)
        # Draw position indicator
        px = int(self._position * w)
        p.setPen(QPen(QColor(C["lavender"]), 2))
        p.drawLine(px, 0, px, h)
        # Small triangle at top
        p.setBrush(QColor(C["lavender"]))
        p.setPen(Qt.PenStyle.NoPen)
        tri = QPolygonF([QPointF(px - 5, 0), QPointF(px + 5, 0), QPointF(px, 6)])
        p.drawPolygon(tri)
        p.end()

    def mousePressEvent(self, event):
        ratio = event.position().x() / max(self.width(), 1)
        self.clicked.emit(max(0.0, min(1.0, ratio)))

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            ratio = event.position().x() / max(self.width(), 1)
            self.clicked.emit(max(0.0, min(1.0, ratio)))


# ---------------------------------------------------------------------------
# CropView
# ---------------------------------------------------------------------------


class CropView(QGraphicsView):
    cropChanged = pyqtSignal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = None
        self._crop_rect = None
        self._img_w = 0
        self._img_h = 0
        self.setStyleSheet(f"background: {C['crust']}; border: 1px solid {C['surface0']}; border-radius: 6px;")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_image(self, pixmap):
        self._scene.clear()
        if pixmap and not pixmap.isNull():
            self._img_w = pixmap.width()
            self._img_h = pixmap.height()
            self._pixmap_item = self._scene.addPixmap(pixmap)
            self._scene.setSceneRect(0, 0, self._img_w, self._img_h)
            pen = QPen(QColor(C["blue"]), 2, Qt.PenStyle.DashLine)
            self._crop_rect = self._scene.addRect(0, 0, self._img_w, self._img_h, pen)
            self._crop_rect.setFlag(self._crop_rect.GraphicsItemFlag.ItemIsMovable, True)
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.update()

    def get_crop(self):
        if not self._crop_rect:
            return (0, 0, self._img_w, self._img_h)
        r = self._crop_rect.rect()
        pos = self._crop_rect.pos()
        x = max(0, int(pos.x() + r.x()))
        y = max(0, int(pos.y() + r.y()))
        w = min(int(r.width()), self._img_w - x)
        h = min(int(r.height()), self._img_h - y)
        return (x, y, w, h)

    def set_crop_rect(self, x, y, w, h):
        if self._crop_rect:
            self._crop_rect.setPos(0, 0)
            self._crop_rect.setRect(x, y, w, h)
            self.cropChanged.emit(x, y, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pixmap_item:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


# ---------------------------------------------------------------------------
# VideoPlayer
# ---------------------------------------------------------------------------


class VideoPlayer(QWidget):
    """Embedded video player with enhanced playback controls."""
    positionChanged = pyqtSignal(float)
    playbackError = pyqtSignal(str)
    proxyLog = pyqtSignal(str)
    proxyStatus = pyqtSignal(bool, str)

    def __init__(self, parent=None, proxy_cache=None):
        super().__init__(parent)
        self.setObjectName("videoPlayer")
        self._duration = 0
        self._filepath = None
        self._source_info = {}
        self._playback_path = None
        self._proxy_path = None
        self._proxy_worker = None
        self._proxy_cache = proxy_cache or ProxyCache()
        self._backend_name = "qt"
        self._mpv_widget = None
        self._mpv_capability = probe_mpv()
        DIAGNOSTICS.record_capabilities(
            "libmpv",
            asdict(self._mpv_capability),
        )
        self._thumb_worker = None
        self._background_workers = set()
        self._fps = 30.0
        self._last_player_error = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Video display
        self.video_stack = QStackedWidget()
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(200)
        self.video_widget.setStyleSheet(f"background: {C['crust']}; border-radius: 8px;")
        self.video_stack.addWidget(self.video_widget)
        if self._mpv_capability.available:
            try:
                self._mpv_widget = MpvWidget()
                self._mpv_widget.positionChanged.connect(
                    lambda seconds: self._on_position(int(seconds * 1000))
                )
                self._mpv_widget.durationChanged.connect(
                    lambda seconds: self._on_duration(int(seconds * 1000))
                )
                self._mpv_widget.pausedChanged.connect(
                    lambda paused: self.btn_play.setText("Play" if paused else "Pause")
                )
                self._mpv_widget.playbackError.connect(self._on_mpv_error)
                self.video_stack.addWidget(self._mpv_widget)
            except (OSError, RuntimeError) as error:
                self._mpv_capability = self._mpv_capability.__class__(
                    available=False,
                    wrapper_version=self._mpv_capability.wrapper_version,
                    reason=str(error),
                    library_path=self._mpv_capability.library_path,
                    library_file=self._mpv_capability.library_file,
                    native_version=self._mpv_capability.native_version,
                    native_version_status=self._mpv_capability.native_version_status,
                )
        layout.addWidget(self.video_stack)

        self.lbl_player_status = QLabel()
        self.lbl_player_status.setWordWrap(True)
        self.lbl_player_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_player_status.setAccessibleName("Player status")
        self.lbl_player_status.hide()
        layout.addWidget(self.lbl_player_status)

        # Thumbnail strip
        self.thumb_strip = ThumbnailStrip()
        self.thumb_strip.clicked.connect(self._on_thumb_click)
        layout.addWidget(self.thumb_strip)

        # Player backend
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(
            lambda position: self._on_position(position)
            if self._backend_name == "qt"
            else None
        )
        self.player.durationChanged.connect(
            lambda duration: self._on_duration(duration)
            if self._backend_name == "qt"
            else None
        )
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        # Controls bar
        controls = QWidget()
        controls.setObjectName("playerControls")
        cl = QVBoxLayout(controls)
        cl.setContentsMargins(8, 4, 8, 4)
        cl.setSpacing(4)
        transport_row = QHBoxLayout()
        transport_row.setSpacing(4)
        seek_row = QHBoxLayout()
        seek_row.setSpacing(6)

        # Frame step back
        self.btn_frame_back = QPushButton("<<")
        self.btn_frame_back.setProperty("class", "playerBtn")
        self.btn_frame_back.setToolTip("Previous frame")
        self.btn_frame_back.setAccessibleName("Previous frame")
        self.btn_frame_back.setFixedWidth(36)
        self.btn_frame_back.clicked.connect(self._frame_back)
        transport_row.addWidget(self.btn_frame_back)

        self.btn_play = QPushButton("Play")
        self.btn_play.setFixedWidth(52)
        self.btn_play.clicked.connect(self._toggle_play)
        transport_row.addWidget(self.btn_play)

        # Frame step forward
        self.btn_frame_fwd = QPushButton(">>")
        self.btn_frame_fwd.setProperty("class", "playerBtn")
        self.btn_frame_fwd.setToolTip("Next frame")
        self.btn_frame_fwd.setAccessibleName("Next frame")
        self.btn_frame_fwd.setFixedWidth(36)
        self.btn_frame_fwd.clicked.connect(self._frame_forward)
        transport_row.addWidget(self.btn_frame_fwd)

        self.lbl_time = QLabel("0:00 / 0:00")
        self.lbl_time.setProperty("class", "dimLabel")
        self.lbl_time.setFixedWidth(120)
        transport_row.addWidget(self.lbl_time)
        transport_row.addStretch()

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 10000)
        self.seek_slider.setAccessibleName("Preview position")
        self.seek_slider.sliderMoved.connect(self._seek)
        seek_row.addWidget(self.seek_slider, 1)

        # Playback speed
        self.cmb_speed = QComboBox()
        self.cmb_speed.addItems(["0.25x", "0.5x", "1x", "1.5x", "2x", "4x"])
        self.cmb_speed.setCurrentText("1x")
        self.cmb_speed.setFixedWidth(65)
        self.cmb_speed.setAccessibleName("Playback speed")
        self.cmb_speed.currentTextChanged.connect(self._on_speed_change)
        seek_row.addWidget(self.cmb_speed)

        # A-B loop
        self.btn_ab_loop = QPushButton("A-B")
        self.btn_ab_loop.setProperty("class", "playerBtn")
        self.btn_ab_loop.setToolTip("Set A-B loop points")
        self.btn_ab_loop.setFixedWidth(36)
        self.btn_ab_loop.setCheckable(True)
        self.btn_ab_loop.clicked.connect(self._toggle_ab_loop)
        seek_row.addWidget(self.btn_ab_loop)
        self._loop_a = -1
        self._loop_b = -1
        self._loop_active = False

        self.lbl_vol = QLabel("Vol:")
        self.lbl_vol.setProperty("class", "dimLabel")
        seek_row.addWidget(self.lbl_vol)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.setAccessibleName("Preview volume")
        self.vol_slider.valueChanged.connect(self._on_volume_change)
        seek_row.addWidget(self.vol_slider)

        cl.addLayout(transport_row)
        cl.addLayout(seek_row)
        layout.addWidget(controls)

        proxy_controls = QWidget()
        proxy_layout = QVBoxLayout(proxy_controls)
        proxy_layout.setContentsMargins(8, 0, 8, 0)
        proxy_layout.setSpacing(4)
        backend_row = QHBoxLayout()
        backend_row.setSpacing(6)
        proxy_action_row = QHBoxLayout()
        proxy_action_row.setSpacing(6)
        backend_row.addWidget(QLabel("Player:"))
        self.cmb_player_backend = QComboBox()
        self.cmb_player_backend.addItem("Qt Multimedia", "qt")
        if self._mpv_widget:
            self.cmb_player_backend.addItem(
                f"mpv {self._mpv_capability.wrapper_version} (experimental)",
                "mpv",
            )
        else:
            self.cmb_player_backend.setToolTip(
                "Optional mpv backend unavailable: "
                + (self._mpv_capability.reason or "install ClipForge[mpv] and libmpv")
            )
        self.cmb_player_backend.setAccessibleName("Preview player backend")
        self.cmb_player_backend.setMaximumWidth(220)
        self.cmb_player_backend.currentIndexChanged.connect(self._change_player_backend)
        backend_row.addWidget(self.cmb_player_backend)
        backend_row.addStretch()
        self.lbl_proxy_status = QLabel("Preview: original source")
        self.lbl_proxy_status.setProperty("class", "dimLabel")
        self.lbl_proxy_status.setAccessibleName("Proxy preview status")
        self.lbl_proxy_status.setWordWrap(True)
        self.proxy_progress = QProgressBar()
        self.proxy_progress.setRange(0, 100)
        self.proxy_progress.setFixedWidth(110)
        self.proxy_progress.setAccessibleName("Proxy generation progress")
        self.proxy_progress.hide()
        proxy_action_row.addStretch()
        proxy_action_row.addWidget(self.proxy_progress)
        self.btn_create_proxy = QPushButton("Create Proxy")
        self.btn_create_proxy.setAccessibleName("Create or cancel preview proxy")
        self.btn_create_proxy.clicked.connect(self._create_or_cancel_proxy)
        self.btn_create_proxy.setEnabled(False)
        proxy_action_row.addWidget(self.btn_create_proxy)
        self.btn_toggle_proxy = QPushButton("Use Proxy")
        self.btn_toggle_proxy.setAccessibleName("Switch between proxy and original preview")
        self.btn_toggle_proxy.clicked.connect(self._toggle_proxy)
        self.btn_toggle_proxy.setEnabled(False)
        proxy_action_row.addWidget(self.btn_toggle_proxy)
        proxy_layout.addLayout(backend_row)
        proxy_layout.addWidget(self.lbl_proxy_status)
        proxy_layout.addLayout(proxy_action_row)
        proxy_cache_row = QHBoxLayout()
        self.lbl_proxy_cache = QLabel()
        self.lbl_proxy_cache.setProperty("class", "dimLabel")
        self.lbl_proxy_cache.setWordWrap(True)
        self.lbl_proxy_cache.setAccessibleName("Preview proxy cache usage")
        proxy_cache_row.addWidget(self.lbl_proxy_cache, 1)
        self.btn_clear_proxy_cache = QPushButton("Purge proxy cache")
        self.btn_clear_proxy_cache.setAccessibleName("Purge preview proxy cache")
        self.btn_clear_proxy_cache.setToolTip(
            "Remove all reusable preview proxies; source media is not affected"
        )
        self.btn_clear_proxy_cache.clicked.connect(self._clear_proxy_cache)
        proxy_cache_row.addWidget(self.btn_clear_proxy_cache)
        proxy_layout.addLayout(proxy_cache_row)
        layout.addWidget(proxy_controls)

        # Timecode display
        self.lbl_timecode = QLabel("00:00:00.000 | Frame: 0")
        self.lbl_timecode.setProperty("class", "dimLabel")
        self.lbl_timecode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_timecode)
        self._refresh_proxy_cache_status()

    def _refresh_proxy_cache_status(self):
        try:
            stats = self._proxy_cache.stats()
        except OSError as error:
            self.lbl_proxy_cache.setText(f"Proxy cache unavailable: {error}")
            self.btn_clear_proxy_cache.setEnabled(False)
            return
        invalid = (
            f"; {stats['invalid_entries']} invalid entry"
            f"{'ies' if stats['invalid_entries'] != 1 else ''}"
            if stats["invalid_entries"]
            else ""
        )
        self.lbl_proxy_cache.setText(
            f"Proxy cache: {format_size(stats['bytes'])} / "
            f"{format_size(stats['max_bytes'])} ({stats['entries']} entries){invalid}"
        )
        self.btn_clear_proxy_cache.setEnabled(
            self._proxy_worker is None and stats["entries"] > 0
        )

    def _clear_proxy_cache(self):
        if self._proxy_worker and self._proxy_worker.isRunning():
            self.proxyStatus.emit(False, "Wait for proxy generation to finish before purging the cache")
            return
        answer = QMessageBox.question(
            self,
            "Purge proxy cache",
            "Remove all reusable preview proxies? Source media and projects will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        active_proxy = (
            self._proxy_path is not None
            and self._playback_path
            and Path(self._playback_path).resolve() == Path(self._proxy_path).resolve()
        )
        removed = self._proxy_cache.clear()
        self._proxy_path = None
        self.btn_toggle_proxy.setEnabled(False)
        if active_proxy and self._filepath:
            self._set_playback_source(self._filepath)
        self._refresh_proxy_cache_status()
        self.proxyStatus.emit(
            True,
            f"Purged {len(removed)} preview cache entr{'y' if len(removed) == 1 else 'ies'}",
        )

    def load(self, filepath, info=None):
        self._cancel_background_reads()
        self._filepath = filepath
        self._source_info = info or {}
        self._last_player_error = None
        if info:
            self._fps = info.get("fps", 30.0) or 30.0
        else:
            self._fps = 30.0
        self._proxy_path = self._proxy_cache.lookup(filepath)
        self._refresh_proxy_cache_status()
        estimate = self._proxy_cache.estimate_size(self._source_info)
        estimate_text = format_size(estimate) if estimate > 0 else "size unknown"
        self.btn_create_proxy.setText(f"Create Proxy (~{estimate_text})")
        self.btn_create_proxy.setEnabled(bool(FFMPEG))
        self.btn_toggle_proxy.setEnabled(bool(self._proxy_path))
        self._set_playback_source(self._proxy_path or filepath)

    def _change_player_backend(self, _index=None):
        selected = self.cmb_player_backend.currentData() or "qt"
        if selected == self._backend_name:
            return
        position = self.get_position_sec()
        if selected == "mpv" and self._mpv_widget:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.setVideoOutput(None)
            self._backend_name = "mpv"
            self.video_stack.setCurrentWidget(self._mpv_widget)
            if self._playback_path:
                playback_path = self._playback_path
                QTimer.singleShot(
                    600,
                    lambda: self._finish_mpv_backend_switch(
                        playback_path,
                        position,
                    ),
                )
            self.lbl_proxy_status.setToolTip(
                "Experimental libmpv backend: broader codec support and exact frame-step; "
                "adds python-mpv plus a separately supplied libmpv runtime."
            )
        else:
            if self._mpv_widget:
                self._mpv_widget.stop()
            self._backend_name = "qt"
            self.player.setVideoOutput(self.video_widget)
            self.video_stack.setCurrentWidget(self.video_widget)
            if self._playback_path:
                self.player.setSource(QUrl.fromLocalFile(self._playback_path))
                self.player.setPosition(int(position * 1000))
            self.lbl_proxy_status.setToolTip(
                "Qt Multimedia is bundled and has the smallest distribution footprint."
            )
        self.btn_play.setText("Play")

    def _finish_mpv_backend_switch(self, playback_path, position):
        """Load mpv after Qt Multimedia has released its native video surface."""
        if (
            self._backend_name != "mpv"
            or not self._mpv_widget
            or playback_path != self._playback_path
        ):
            return
        self._mpv_widget.load(playback_path, start=position)
        self._mpv_widget.set_volume(self.vol_slider.value())
        self._mpv_widget.set_speed(
            float(self.cmb_speed.currentText().replace("x", ""))
        )

    def _cancel_background_reads(self):
        for worker in tuple(self._background_workers):
            if worker.isRunning() and hasattr(worker, "cancel"):
                worker.cancel()
        self._thumb_worker = None
        self._proxy_worker = None

    def _set_playback_source(self, path):
        if not path:
            return
        self._playback_path = os.fspath(path)
        using_proxy = (
            self._proxy_path is not None
            and Path(self._playback_path).resolve() == Path(self._proxy_path).resolve()
        )
        self._show_player_status("Loading preview…", C["subtext0"])
        if self._backend_name == "mpv" and self._mpv_widget:
            self._mpv_widget.load(self._playback_path)
            self.video_stack.setCurrentWidget(self._mpv_widget)
        else:
            self.player.setSource(QUrl.fromLocalFile(self._playback_path))
            self.video_stack.setCurrentWidget(self.video_widget)
        self.btn_play.setText("Play")
        self.lbl_proxy_status.setText(
            f"Preview: proxy ({format_size(Path(self._playback_path).stat().st_size)})"
            if using_proxy
            else "Preview: original source (exports always use original)"
        )
        self.btn_toggle_proxy.setText("Use Original" if using_proxy else "Use Proxy")
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.cancel()
        self.thumb_strip.set_thumbnails([])
        worker = ThumbnailWorker(self._playback_path, 16)
        self._thumb_worker = worker
        self._background_workers.add(worker)
        worker.thumbnails_ready.connect(
            lambda thumbs, active_worker=worker: (
                self.thumb_strip.set_thumbnails(thumbs)
                if self._thumb_worker is active_worker
                else None
            )
        )
        worker.finished.connect(
            lambda current=worker: self._background_workers.discard(current)
        )
        worker.start()

    def _create_or_cancel_proxy(self):
        if self._proxy_worker and self._proxy_worker.isRunning():
            self._proxy_worker.cancel()
            self.btn_create_proxy.setEnabled(False)
            self.lbl_proxy_status.setText("Proxy: cancelling…")
            return
        if not FFMPEG or not self._filepath:
            return
        proxy_path, _manifest_path = self._proxy_cache.paths_for(self._filepath)
        command = self._proxy_cache.command(FFMPEG, self._filepath, proxy_path)
        source_path = self._filepath
        worker = FFmpegWorker(
            command,
            duration=float(self._source_info.get("duration") or 0),
            output_path=str(proxy_path),
            overwrite=True,
            timeout=max(300, float(self._source_info.get("duration") or 0) * 5),
            parent=self,
        )
        self._proxy_worker = worker
        self._background_workers.add(worker)
        worker.progress.connect(lambda value: self.proxy_progress.setValue(int(value)))
        worker.log_output.connect(self.proxyLog.emit)
        worker.finished_signal.connect(
            lambda ok, message, active_worker=worker, source=source_path: (
                self._on_proxy_finished(active_worker, source, ok, message)
            )
        )
        worker.finished.connect(
            lambda current=worker: self._background_workers.discard(current)
        )
        self.proxy_progress.setValue(0)
        self.proxy_progress.show()
        self.btn_create_proxy.setText("Cancel Proxy")
        self.lbl_proxy_status.setText("Proxy: generating…")
        self._refresh_proxy_cache_status()
        worker.start()

    def _on_proxy_finished(self, worker, source_path, ok, message):
        if worker is not self._proxy_worker or source_path != self._filepath:
            return
        self.proxy_progress.hide()
        estimate = self._proxy_cache.estimate_size(self._source_info)
        estimate_text = format_size(estimate) if estimate > 0 else "size unknown"
        self.btn_create_proxy.setText(f"Create Proxy (~{estimate_text})")
        self.btn_create_proxy.setEnabled(bool(FFMPEG))
        if ok:
            try:
                proxy_path, _manifest_path = self._proxy_cache.paths_for(source_path)
                self._proxy_path = self._proxy_cache.record(source_path, proxy_path)
                self._proxy_cache.prune()
                self.btn_toggle_proxy.setEnabled(True)
                self._set_playback_source(self._proxy_path)
                message = "Proxy ready; preview switched to proxy. Exports still use the original."
            except (OSError, ValueError) as error:
                ok = False
                message = f"Proxy cache validation failed: {error}"
        else:
            self.lbl_proxy_status.setText(f"Proxy unavailable: {message}")
        self.proxyStatus.emit(ok, message)
        self._proxy_worker = None
        self._refresh_proxy_cache_status()

    def _toggle_proxy(self):
        if not self._filepath:
            return
        active_proxy = (
            self._proxy_path is not None
            and self._playback_path
            and Path(self._playback_path).resolve() == Path(self._proxy_path).resolve()
        )
        if active_proxy:
            self._set_playback_source(self._filepath)
            return
        self._proxy_path = self._proxy_cache.lookup(self._filepath)
        self._refresh_proxy_cache_status()
        if self._proxy_path:
            self._set_playback_source(self._proxy_path)
        else:
            self.btn_toggle_proxy.setEnabled(False)
            self.lbl_proxy_status.setText("Proxy cache is stale; create a new proxy")

    def _show_player_status(self, message, color):
        self.lbl_player_status.setText(message)
        self.lbl_player_status.setStyleSheet(
            f"color: {color}; padding: 4px; font-weight: 600;"
        )
        self.lbl_player_status.show()

    def _on_player_error(self, error, message=""):
        if self._backend_name != "qt":
            return
        if error == QMediaPlayer.Error.NoError:
            return
        error_key = (error, message or self.player.errorString())
        if self._last_player_error == error_key:
            return
        self._last_player_error = error_key
        labels = {
            QMediaPlayer.Error.ResourceError: "The media file could not be opened",
            QMediaPlayer.Error.FormatError: "This media format is not supported for preview",
            QMediaPlayer.Error.NetworkError: "The preview backend reported a network error",
            QMediaPlayer.Error.AccessDeniedError: "Access to the media file was denied",
        }
        summary = labels.get(error, "The preview backend could not decode this file")
        details = message or self.player.errorString()
        actionable = f"{summary}. Editing with FFmpeg may still work."
        if details:
            actionable += f" Backend: {details}"
        self._show_player_status(actionable, C["red"])
        self.btn_play.setText("Play")
        self.playbackError.emit(actionable)

    def _on_mpv_error(self, message):
        actionable = f"mpv preview error: {message}. FFmpeg editing remains available."
        self._show_player_status(actionable, C["red"])
        self.btn_play.setText("Play")
        self.playbackError.emit(actionable)

    def _on_media_status(self, status):
        if self._backend_name != "qt":
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self.lbl_player_status.hide()
        elif status == QMediaPlayer.MediaStatus.LoadingMedia:
            self._show_player_status("Loading preview…", C["subtext0"])
        elif status == QMediaPlayer.MediaStatus.StalledMedia:
            self._show_player_status(
                "Preview is stalled; wait or reopen the file.", C["yellow"]
            )
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._on_player_error(
                self.player.error() or QMediaPlayer.Error.FormatError,
                self.player.errorString(),
            )

    def _toggle_play(self):
        if self._backend_name == "mpv" and self._mpv_widget:
            if self._mpv_widget.is_paused():
                self._mpv_widget.play()
                self.btn_play.setText("Pause")
            else:
                self._mpv_widget.pause()
                self.btn_play.setText("Play")
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("Play")
        else:
            self.player.play()
            self.btn_play.setText("Pause")

    def _frame_back(self):
        if self._backend_name == "mpv" and self._mpv_widget:
            self._mpv_widget.frame_step(-1)
            return
        if self._duration > 0:
            step = int(1000 / self._fps)
            pos = max(0, self.player.position() - step)
            self.player.setPosition(pos)

    def _frame_forward(self):
        if self._backend_name == "mpv" and self._mpv_widget:
            self._mpv_widget.frame_step(1)
            return
        if self._duration > 0:
            step = int(1000 / self._fps)
            pos = min(self._duration, self.player.position() + step)
            self.player.setPosition(pos)

    def _on_speed_change(self, text):
        speed = float(text.replace("x", ""))
        if self._backend_name == "mpv" and self._mpv_widget:
            self._mpv_widget.set_speed(speed)
        else:
            self.player.setPlaybackRate(speed)

    def _on_volume_change(self, value):
        if self._backend_name == "mpv" and self._mpv_widget:
            self._mpv_widget.set_volume(value)
        else:
            self.audio.setVolume(value / 100)

    def _toggle_ab_loop(self, checked):
        if checked:
            if self._loop_a < 0:
                self._loop_a = int(self.get_position_sec() * 1000)
                self.btn_ab_loop.setText("B?")
                self.btn_ab_loop.setToolTip("Click to set B point")
            elif self._loop_b < 0:
                self._loop_b = int(self.get_position_sec() * 1000)
                self._loop_active = True
                self.btn_ab_loop.setText("A-B")
                self.btn_ab_loop.setToolTip("A-B loop active (click to clear)")
        else:
            self._loop_a = -1
            self._loop_b = -1
            self._loop_active = False
            self.btn_ab_loop.setText("A-B")
            self.btn_ab_loop.setToolTip("Set A-B loop points")

    def _on_position(self, pos_ms):
        if self._duration > 0:
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(int(pos_ms / self._duration * 10000))
            self.seek_slider.blockSignals(False)
            self.thumb_strip.set_position(pos_ms / self._duration)
        self.lbl_time.setText(
            f"{format_duration_short(pos_ms / 1000)} / {format_duration_short(self._duration / 1000)}"
        )
        sec = pos_ms / 1000
        frame = int(sec * self._fps)
        self.lbl_timecode.setText(f"{format_duration(sec)} | Frame: {frame}")
        self.positionChanged.emit(pos_ms / 1000)
        # A-B loop
        if self._loop_active and self._loop_b > 0 and pos_ms >= self._loop_b:
            if self._backend_name == "mpv" and self._mpv_widget:
                self._mpv_widget.seek(self._loop_a / 1000)
            else:
                self.player.setPosition(self._loop_a)

    def _on_duration(self, dur_ms):
        self._duration = dur_ms

    def _seek(self, value):
        if self._duration > 0:
            position_ms = int(value / 10000 * self._duration)
            if self._backend_name == "mpv" and self._mpv_widget:
                self._mpv_widget.seek(position_ms / 1000)
            else:
                self.player.setPosition(position_ms)

    def _on_thumb_click(self, ratio):
        if self._duration > 0:
            position_ms = int(ratio * self._duration)
            if self._backend_name == "mpv" and self._mpv_widget:
                self._mpv_widget.seek(position_ms / 1000)
            else:
                self.player.setPosition(position_ms)

    def stop(self):
        if self._backend_name == "mpv" and self._mpv_widget:
            self._mpv_widget.stop()
        else:
            self.player.stop()
        self.btn_play.setText("Play")

    def release(self):
        """Release media handles and finish the current thumbnail read."""
        self.stop()
        self.player.setSource(QUrl())
        if self._mpv_widget:
            self._mpv_widget.shutdown()
        self._cancel_background_reads()

    def get_position_sec(self):
        if self._backend_name == "mpv" and self._mpv_widget:
            return self._mpv_widget.position()
        return self.player.position() / 1000

    def get_fps(self):
        return self._fps


# ---------------------------------------------------------------------------
# FileInfoBar
# ---------------------------------------------------------------------------


class FileInfoBar(QWidget):
    fileLoaded = pyqtSignal(str, dict)
    fileLoadFailed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fileInfoBar")
        self._info = None
        self._filepath = None
        self._probe_worker = None
        self._probe_workers = set()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.btn_open = QPushButton("Open Video")
        self.btn_open.setObjectName("primaryBtn")
        self.btn_open.setFixedWidth(120)
        self.btn_open.setAccessibleName("Open video file")
        self.btn_open.clicked.connect(self._open_file)

        self.lbl_name = QLabel("Open a video to get started")
        self.lbl_name.setProperty("class", "dimLabel")
        self.lbl_info = QLabel("")
        self.lbl_info.setProperty("class", "dimLabel")
        self.btn_cancel_probe = QPushButton("Cancel inspection")
        self.btn_cancel_probe.setAccessibleName("Cancel media inspection")
        self.btn_cancel_probe.setVisible(False)
        self.btn_cancel_probe.clicked.connect(self.cancel_probe)

        layout.addWidget(self.btn_open)
        layout.addWidget(self.lbl_name, 1)
        layout.addWidget(self.lbl_info)
        layout.addWidget(self.btn_cancel_probe)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", str(Path.home() / "Videos"),
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts *.mpg *.mpeg);;All Files (*)"
        )
        if path:
            self.load_file(path)

    def load_file(self, path):
        if not validate_media_path(path):
            self.lbl_name.setText("Invalid file path")
            self.lbl_info.setText("Choose an existing local media file")
            self.fileLoadFailed.emit(path or "", "The selected file does not exist.")
            return
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.cancel()
        self.lbl_name.setText(f"Inspecting {Path(path).name}…")
        self.lbl_name.setToolTip(path)
        self.lbl_info.setText("Reading bounded media metadata")
        self.btn_open.setEnabled(False)
        self.btn_cancel_probe.setEnabled(True)
        self.btn_cancel_probe.setVisible(True)
        worker = MediaProbeWorker(path, self)
        self._probe_worker = worker
        self._probe_workers.add(worker)
        worker.finished_signal.connect(
            lambda filepath, result, current=worker: self._on_probe_finished(
                current,
                filepath,
                result,
            )
        )
        worker.finished.connect(lambda current=worker: self._probe_workers.discard(current))
        worker.start()

    def cancel_probe(self):
        if self._probe_worker and self._probe_worker.isRunning():
            self._probe_worker.cancel()
            self.btn_cancel_probe.setEnabled(False)
            self.lbl_info.setText("Cancelling media inspection…")

    def _on_probe_finished(self, worker, path, result):
        if worker is not self._probe_worker:
            return
        self._probe_worker = None
        self.btn_open.setEnabled(True)
        self.btn_cancel_probe.setVisible(False)
        if result.error:
            self._filepath = None
            self._info = None
            cancelled = result.error.code == "probe_cancelled"
            self.lbl_name.setText(
                "Media inspection cancelled" if cancelled else "Could not open media"
            )
            details = result.error.message
            if result.error.details:
                details += f" {result.error.details}"
            self.lbl_info.setText(result.error.message)
            self.lbl_info.setToolTip(details)
            if not cancelled:
                self.fileLoadFailed.emit(path, details)
            return
        self._filepath = path
        self._info = result.info
        add_recent(path)
        name = Path(path).name
        if len(name) > 50:
            name = name[:47] + "..."
        self.lbl_name.setText(name)
        self.lbl_name.setToolTip(path)
        if self._info:
            w = self._info.get("width", "?")
            h = self._info.get("height", "?")
            dur = format_duration(self._info.get("duration", 0))
            fps = self._info.get("fps", "?")
            size = format_size(self._info.get("size", 0))
            br = format_bitrate(self._info.get("bit_rate", 0))
            self.lbl_info.setText(f"{w}x{h}  |  {fps} fps  |  {dur}  |  {size}  |  {br}")
        self.fileLoaded.emit(path, self._info)

    def filepath(self):
        return self._filepath

    def info(self):
        return self._info
