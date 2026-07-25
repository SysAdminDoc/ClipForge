"""Reusable UI widgets: Toast, RangeSlider, ThumbnailStrip, CropView, VideoPlayer, FileInfoBar."""

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider,
    QComboBox, QFileDialog, QGraphicsView, QGraphicsScene, QProgressBar,
)
from PyQt6.QtCore import (
    Qt, QUrl, QTimer, QPointF, QPropertyAnimation, QEasingCurve, QRect,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QPixmap, QPolygonF,
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

from clipforge_utils import (
    format_duration, format_duration_short, format_size, format_bitrate,
    validate_media_path,
)
from .constants import C
from .tools import FFMPEG, probe_media, probe_video, extract_frame
from .settings import add_recent
from .workers import FFmpegWorker, ThumbnailWorker
from .proxy import ProxyCache


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
        self.setText(text)
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
        self._thumb_worker = None
        self._fps = 30.0
        self._last_player_error = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Video display
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(200)
        self.video_widget.setStyleSheet(f"background: {C['crust']}; border-radius: 8px;")
        layout.addWidget(self.video_widget)

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
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        # Controls bar
        controls = QWidget()
        controls.setObjectName("playerControls")
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(8, 4, 8, 4)
        cl.setSpacing(4)

        # Frame step back
        self.btn_frame_back = QPushButton("<<")
        self.btn_frame_back.setProperty("class", "playerBtn")
        self.btn_frame_back.setToolTip("Previous frame")
        self.btn_frame_back.setAccessibleName("Previous frame")
        self.btn_frame_back.setFixedWidth(36)
        self.btn_frame_back.clicked.connect(self._frame_back)
        cl.addWidget(self.btn_frame_back)

        self.btn_play = QPushButton("Play")
        self.btn_play.setFixedWidth(52)
        self.btn_play.clicked.connect(self._toggle_play)
        cl.addWidget(self.btn_play)

        # Frame step forward
        self.btn_frame_fwd = QPushButton(">>")
        self.btn_frame_fwd.setProperty("class", "playerBtn")
        self.btn_frame_fwd.setToolTip("Next frame")
        self.btn_frame_fwd.setAccessibleName("Next frame")
        self.btn_frame_fwd.setFixedWidth(36)
        self.btn_frame_fwd.clicked.connect(self._frame_forward)
        cl.addWidget(self.btn_frame_fwd)

        self.lbl_time = QLabel("0:00 / 0:00")
        self.lbl_time.setProperty("class", "dimLabel")
        self.lbl_time.setFixedWidth(120)
        cl.addWidget(self.lbl_time)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 10000)
        self.seek_slider.sliderMoved.connect(self._seek)
        cl.addWidget(self.seek_slider, 1)

        # Playback speed
        self.cmb_speed = QComboBox()
        self.cmb_speed.addItems(["0.25x", "0.5x", "1x", "1.5x", "2x", "4x"])
        self.cmb_speed.setCurrentText("1x")
        self.cmb_speed.setFixedWidth(65)
        self.cmb_speed.currentTextChanged.connect(self._on_speed_change)
        cl.addWidget(self.cmb_speed)

        # A-B loop
        self.btn_ab_loop = QPushButton("A-B")
        self.btn_ab_loop.setProperty("class", "playerBtn")
        self.btn_ab_loop.setToolTip("Set A-B loop points")
        self.btn_ab_loop.setFixedWidth(36)
        self.btn_ab_loop.setCheckable(True)
        self.btn_ab_loop.clicked.connect(self._toggle_ab_loop)
        cl.addWidget(self.btn_ab_loop)
        self._loop_a = -1
        self._loop_b = -1
        self._loop_active = False

        self.lbl_vol = QLabel("Vol:")
        self.lbl_vol.setProperty("class", "dimLabel")
        cl.addWidget(self.lbl_vol)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        cl.addWidget(self.vol_slider)

        layout.addWidget(controls)

        proxy_controls = QWidget()
        proxy_layout = QHBoxLayout(proxy_controls)
        proxy_layout.setContentsMargins(8, 0, 8, 0)
        proxy_layout.setSpacing(6)
        self.lbl_proxy_status = QLabel("Preview: original source")
        self.lbl_proxy_status.setProperty("class", "dimLabel")
        self.lbl_proxy_status.setAccessibleName("Proxy preview status")
        proxy_layout.addWidget(self.lbl_proxy_status, 1)
        self.proxy_progress = QProgressBar()
        self.proxy_progress.setRange(0, 100)
        self.proxy_progress.setFixedWidth(110)
        self.proxy_progress.setAccessibleName("Proxy generation progress")
        self.proxy_progress.hide()
        proxy_layout.addWidget(self.proxy_progress)
        self.btn_create_proxy = QPushButton("Create Proxy")
        self.btn_create_proxy.setAccessibleName("Create or cancel preview proxy")
        self.btn_create_proxy.clicked.connect(self._create_or_cancel_proxy)
        self.btn_create_proxy.setEnabled(False)
        proxy_layout.addWidget(self.btn_create_proxy)
        self.btn_toggle_proxy = QPushButton("Use Proxy")
        self.btn_toggle_proxy.setAccessibleName("Switch between proxy and original preview")
        self.btn_toggle_proxy.clicked.connect(self._toggle_proxy)
        self.btn_toggle_proxy.setEnabled(False)
        proxy_layout.addWidget(self.btn_toggle_proxy)
        layout.addWidget(proxy_controls)

        # Timecode display
        self.lbl_timecode = QLabel("00:00:00.000 | Frame: 0")
        self.lbl_timecode.setProperty("class", "dimLabel")
        self.lbl_timecode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_timecode)

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
        estimate = self._proxy_cache.estimate_size(self._source_info)
        estimate_text = format_size(estimate) if estimate > 0 else "size unknown"
        self.btn_create_proxy.setText(f"Create Proxy (~{estimate_text})")
        self.btn_create_proxy.setEnabled(bool(FFMPEG))
        self.btn_toggle_proxy.setEnabled(bool(self._proxy_path))
        self._set_playback_source(self._proxy_path or filepath)

    def _cancel_background_reads(self):
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.cancel()
            self._thumb_worker.wait(5000)
        self._thumb_worker = None
        if self._proxy_worker and self._proxy_worker.isRunning():
            self._proxy_worker.cancel()
            self._proxy_worker.wait(5000)
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
        self.player.setSource(QUrl.fromLocalFile(self._playback_path))
        self.btn_play.setText("Play")
        self.lbl_proxy_status.setText(
            f"Preview: proxy ({format_size(Path(self._playback_path).stat().st_size)})"
            if using_proxy
            else "Preview: original source (exports always use original)"
        )
        self.btn_toggle_proxy.setText("Use Original" if using_proxy else "Use Proxy")
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.cancel()
            self._thumb_worker.wait(5000)
        self.thumb_strip.set_thumbnails([])
        worker = ThumbnailWorker(self._playback_path, 16)
        self._thumb_worker = worker
        worker.thumbnails_ready.connect(
            lambda thumbs, active_worker=worker: (
                self.thumb_strip.set_thumbnails(thumbs)
                if self._thumb_worker is active_worker
                else None
            )
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
        worker.progress.connect(lambda value: self.proxy_progress.setValue(int(value)))
        worker.log_output.connect(self.proxyLog.emit)
        worker.finished_signal.connect(
            lambda ok, message, active_worker=worker, source=source_path: (
                self._on_proxy_finished(active_worker, source, ok, message)
            )
        )
        self.proxy_progress.setValue(0)
        self.proxy_progress.show()
        self.btn_create_proxy.setText("Cancel Proxy")
        self.lbl_proxy_status.setText("Proxy: generating…")
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

    def _on_media_status(self, status):
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
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("Play")
        else:
            self.player.play()
            self.btn_play.setText("Pause")

    def _frame_back(self):
        if self._duration > 0:
            step = int(1000 / self._fps)
            pos = max(0, self.player.position() - step)
            self.player.setPosition(pos)

    def _frame_forward(self):
        if self._duration > 0:
            step = int(1000 / self._fps)
            pos = min(self._duration, self.player.position() + step)
            self.player.setPosition(pos)

    def _on_speed_change(self, text):
        speed = float(text.replace("x", ""))
        self.player.setPlaybackRate(speed)

    def _toggle_ab_loop(self, checked):
        if checked:
            if self._loop_a < 0:
                self._loop_a = self.player.position()
                self.btn_ab_loop.setText("B?")
                self.btn_ab_loop.setToolTip("Click to set B point")
            elif self._loop_b < 0:
                self._loop_b = self.player.position()
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
            self.player.setPosition(self._loop_a)

    def _on_duration(self, dur_ms):
        self._duration = dur_ms

    def _seek(self, value):
        if self._duration > 0:
            self.player.setPosition(int(value / 10000 * self._duration))

    def _on_thumb_click(self, ratio):
        if self._duration > 0:
            self.player.setPosition(int(ratio * self._duration))

    def stop(self):
        self.player.stop()
        self.btn_play.setText("Play")

    def release(self):
        """Release media handles and finish the current thumbnail read."""
        self.stop()
        self.player.setSource(QUrl())
        self._cancel_background_reads()

    def get_position_sec(self):
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

        layout.addWidget(self.btn_open)
        layout.addWidget(self.lbl_name, 1)
        layout.addWidget(self.lbl_info)

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
        result = probe_media(path)
        if result.error:
            self._filepath = None
            self._info = None
            self.lbl_name.setText("Could not open media")
            details = result.error.message
            if result.error.details:
                details += f" {result.error.details}"
            self.lbl_info.setText(result.error.message)
            self.lbl_info.setToolTip(details)
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
