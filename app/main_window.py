import cv2
import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QComboBox, QPushButton, QFormLayout,
    QMessageBox, QStackedWidget, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, QThread
from PyQt6.QtGui import QImage, QPixmap

from app.video.capture import CameraCapture, ScreenCapture, WindowCapture, list_cameras, list_window_titles
from app.video.effects import VideoEffects
from app.video.processor import VideoProcessor
from app.video.output import VirtualCameraOutput
from app.audio.capture import AudioCapture, list_audio_devices
from app.audio.effects import AudioEffects
from app.audio.output import VirtualAudioOutput
import config


PREVIEW_FPS = 15


class VideoPreviewLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(180, 130)
        self.setStyleSheet("background-color: #1a3a8a; border-radius: 8px; color: white; font-size: 12px;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("Превью")

    def update_frame(self, frame):
        h, w, ch = frame.shape
        rgb = frame[:, :, ::-1].copy()
        q_img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)
        scaled = pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.setPixmap(scaled)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CamerUdioStt")
        self.setMinimumSize(520, 340)
        self.resize(560, 370)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )

        self.video_capture = None
        self.video_effects = VideoEffects()
        self.video_processor = None
        self.processor_thread = None
        self.virtual_cam = None
        self.video_running = False

        self.audio_capture = None
        self.audio_effects = AudioEffects()
        self.virtual_audio = None
        self.audio_running = False

        self._latest_frame = None
        self._screen_capturing = False
        self._drag_pos = None
        self._audio_muted = False

        self._build_ui()

        self._preview_timer = QTimer()
        self._preview_timer.setInterval(int(1000 / PREVIEW_FPS))
        self._preview_timer.timeout.connect(self._update_preview)

    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet("background: rgba(25, 25, 35, 240);")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Title bar ──
        title_bar = QWidget()
        title_bar.setFixedHeight(30)
        title_bar.setStyleSheet("background: rgba(35, 35, 50, 240);")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(8, 0, 8, 0)
        title_layout.setSpacing(4)

        # Window buttons
        for text, color, action in [("✕", "#ff5f57", self.close), ("−", "#ffbd2e", self.showMinimized), ("+", "#28c840", self._toggle_maximize)]:
            btn = QPushButton(text)
            btn.setFixedSize(18, 18)
            btn.setStyleSheet(f"background: {color}; border-radius: 9px; color: transparent;")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(action)
            title_layout.addWidget(btn)

        title_layout.addSpacing(8)

        # Video/Audio tabs
        self.tab_video = QPushButton("Видео")
        self.tab_video.setCheckable(True)
        self.tab_video.setChecked(True)
        self.tab_video.setStyleSheet(self._tab_style(True))
        self.tab_video.clicked.connect(lambda: self._switch_tab(0))
        self.tab_video.setCursor(Qt.CursorShape.PointingHandCursor)
        title_layout.addWidget(self.tab_video)

        self.tab_audio = QPushButton("Аудио")
        self.tab_audio.setCheckable(True)
        self.tab_audio.setStyleSheet(self._tab_style(False))
        self.tab_audio.clicked.connect(lambda: self._switch_tab(1))
        self.tab_audio.setCursor(Qt.CursorShape.PointingHandCursor)
        title_layout.addWidget(self.tab_audio)

        # Mute button (in title bar)
        self.mute_btn = QPushButton("🔇")
        self.mute_btn.setFixedSize(24, 24)
        self.mute_btn.setCheckable(True)
        self.mute_btn.setStyleSheet("""
            QPushButton { background: rgba(70,70,90,200); color: white; border: 1px solid rgba(100,100,130,120); border-radius: 12px; font-size: 12px; }
            QPushButton:hover { background: rgba(90,90,110,220); }
            QPushButton:checked { background: rgba(180,50,50,200); }
        """)
        self.mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_btn.clicked.connect(self._toggle_mute)
        title_layout.addWidget(self.mute_btn)

        title_layout.addStretch()

        # Mode button
        self.mode_btn = QPushButton("⚙ Эффекты")
        self.mode_btn.setStyleSheet("""
            QPushButton { background: rgba(70,70,90,200); color: white; border: 1px solid rgba(100,100,130,120); border-radius: 4px; padding: 1px 6px; font-size: 10px; }
            QPushButton:hover { background: rgba(90,90,110,220); }
        """)
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.clicked.connect(self._cycle_mode)
        title_layout.addWidget(self.mode_btn)

        main_layout.addWidget(title_bar)

        # ── Content ──
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(10, 8, 10, 10)
        content_layout.setSpacing(10)

        # Left: preview + status
        left = QVBoxLayout()
        left.setSpacing(4)
        self.video_preview = VideoPreviewLabel()
        left.addWidget(self.video_preview, stretch=1)

        self.audio_level = QLabel("Уровень")
        self.audio_level.setStyleSheet("background: rgba(30, 60, 120, 180); border-radius: 8px; color: white; font-size: 10px;")
        self.audio_level.setMinimumHeight(100)
        self.audio_level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_level.setVisible(False)
        left.addWidget(self.audio_level, stretch=1)

        self.status_label = QLabel("Остановлено")
        self.status_label.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 9px;")
        left.addWidget(self.status_label)
        content_layout.addLayout(left, stretch=1)

        # Right: panels (stacked)
        self.panels = QStackedWidget()
        self.panels.setStyleSheet("background: transparent;")
        self.panels.addWidget(self._build_video_panel())
        self.panels.addWidget(self._build_audio_panel())
        content_layout.addWidget(self.panels, stretch=1)

        main_layout.addWidget(content, stretch=1)

    def _tab_style(self, active):
        bg = "rgba(80,100,160,200)" if active else "transparent"
        return f"""
            QPushButton {{ background: {bg}; color: white; border: none; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: {"bold" if active else "normal"}; }}
            QPushButton:hover {{ background: rgba(80,100,160,180); }}
        """

    def _switch_tab(self, idx):
        is_video = idx == 0
        self.tab_video.setChecked(is_video)
        self.tab_audio.setChecked(not is_video)
        self.tab_video.setStyleSheet(self._tab_style(is_video))
        self.tab_audio.setStyleSheet(self._tab_style(not is_video))
        self.panels.setCurrentIndex(idx)
        self.video_preview.setVisible(is_video)
        self.audio_level.setVisible(not is_video)

    def _build_video_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        # Stacked sections
        self.video_sections = QStackedWidget()
        self.video_sections.setStyleSheet("background: transparent;")

        # Section 0: Effects
        effects_w = QWidget()
        effects_w.setStyleSheet("background: transparent;")
        effects_layout = QVBoxLayout(effects_w)
        effects_layout.setContentsMargins(0, 0, 0, 0)
        effects_layout.setSpacing(3)
        effects_layout.addWidget(self._section_label("Эффекты"))
        self.brightness_slider = self._slider(-100, 100, 0)
        self.brightness_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "brightness", v))
        effects_layout.addWidget(self._labeled("Яркость", self.brightness_slider))
        self.contrast_slider = self._slider(-100, 100, 0)
        self.contrast_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "contrast", v))
        effects_layout.addWidget(self._labeled("Контраст", self.contrast_slider))
        self.saturation_slider = self._slider(-100, 100, 0)
        self.saturation_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "saturation", v))
        effects_layout.addWidget(self._labeled("Насыщенность", self.saturation_slider))
        self.gamma_slider = self._slider(1, 30, 10)
        self.gamma_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "gamma", v / 10.0))
        effects_layout.addWidget(self._labeled("Гамма", self.gamma_slider))
        self.temperature_slider = self._slider(-100, 100, 0)
        self.temperature_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "temperature", v))
        effects_layout.addWidget(self._labeled("Температура", self.temperature_slider))
        self.blur_slider = self._slider(0, 20, 0)
        self.blur_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "blur", v))
        effects_layout.addWidget(self._labeled("Размытие", self.blur_slider))
        self.sharpen_slider = self._slider(0, 100, 0)
        self.sharpen_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "sharpen", v))
        effects_layout.addWidget(self._labeled("Резкость", self.sharpen_slider))
        self.vignette_slider = self._slider(0, 100, 0)
        self.vignette_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "vignette", v))
        effects_layout.addWidget(self._labeled("Виньетка", self.vignette_slider))
        self.pixelate_slider = self._slider(0, 50, 0)
        self.pixelate_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "pixelate", v))
        effects_layout.addWidget(self._labeled("Пиксели", self.pixelate_slider))
        self.sepia_slider = self._slider(0, 100, 0)
        self.sepia_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "sepia", v))
        effects_layout.addWidget(self._labeled("Сепия", self.sepia_slider))
        self.grayscale_slider = self._slider(0, 100, 0)
        self.grayscale_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "grayscale", v))
        effects_layout.addWidget(self._labeled("Ч/Б", self.grayscale_slider))
        self.invert_slider = self._slider(0, 100, 0)
        self.invert_slider.valueChanged.connect(lambda v: setattr(self.video_effects, "invert", v))
        effects_layout.addWidget(self._labeled("Инверсия", self.invert_slider))
        effects_layout.addStretch()

        # Reset button
        self.reset_btn = QPushButton("Сбросить")
        self.reset_btn.setStyleSheet(self._small_btn_style())
        self.reset_btn.clicked.connect(self._reset_effects)
        effects_layout.addWidget(self.reset_btn)

        self.video_sections.addWidget(effects_w)

        # Section 1: Source
        source_w = QWidget()
        source_w.setStyleSheet("background: transparent;")
        source_layout = QVBoxLayout(source_w)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(3)
        source_layout.addWidget(self._section_label("Источник"))
        self.source_combo = self._combo(["Камера", "Весь экран", "Окно"])
        self.source_combo.currentTextChanged.connect(self._on_source_changed)
        source_layout.addWidget(self.source_combo)
        self.camera_combo = self._combo([])
        self._refresh_cameras()
        source_layout.addWidget(self.camera_combo)
        self.window_combo = self._combo([])
        self.window_combo.setVisible(False)
        source_layout.addWidget(self.window_combo)
        self.refresh_win_btn = QPushButton("🔄 Окна")
        self.refresh_win_btn.setVisible(False)
        self.refresh_win_btn.setStyleSheet(self._small_btn_style())
        self.refresh_win_btn.clicked.connect(self._refresh_windows)
        source_layout.addWidget(self.refresh_win_btn)
        source_layout.addStretch()
        self.video_sections.addWidget(source_w)

        # Section 2: Settings
        settings_w = QWidget()
        settings_w.setStyleSheet("background: transparent;")
        settings_layout = QVBoxLayout(settings_w)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(3)
        settings_layout.addWidget(self._section_label("Настройки"))
        res_row = QHBoxLayout()
        self.res_combo = self._combo(["640×480", "1280×720", "1920×1080"])
        res_row.addWidget(self._labeled("Разр.", self.res_combo))
        self.fps_combo = self._combo(["15", "20", "30"])
        self.fps_combo.setCurrentText("20")
        res_row.addWidget(self._labeled("FPS", self.fps_combo))
        settings_layout.addLayout(res_row)
        self.backend_combo = self._combo(["OBS", "NDI"])
        settings_layout.addWidget(self._labeled("Бэкенд", self.backend_combo))
        settings_layout.addStretch()
        self.video_sections.addWidget(settings_w)

        layout.addWidget(self.video_sections, stretch=1)

        self.video_btn = QPushButton("▶ Запустить")
        self.video_btn.setStyleSheet(self._start_btn_style())
        self.video_btn.clicked.connect(self._toggle_video)
        layout.addWidget(self.video_btn)

        return w

    def _build_audio_panel(self):
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        # Stacked sections
        self.audio_sections = QStackedWidget()
        self.audio_sections.setStyleSheet("background: transparent;")

        # Section 0: Effects
        effects_w = QWidget()
        effects_w.setStyleSheet("background: transparent;")
        effects_layout = QVBoxLayout(effects_w)
        effects_layout.setContentsMargins(0, 0, 0, 0)
        effects_layout.setSpacing(3)
        effects_layout.addWidget(self._section_label("Эффекты"))
        self.pitch_slider = self._slider(5, 20, 10)
        self.pitch_slider.valueChanged.connect(self._on_pitch_changed)
        effects_layout.addWidget(self._labeled("Тон", self.pitch_slider))
        self.gain_slider = self._slider(0, 30, 10)
        self.gain_slider.valueChanged.connect(self._on_gain_changed)
        effects_layout.addWidget(self._labeled("Громкость", self.gain_slider))
        self.reverb_slider = self._slider(0, 100, 0)
        self.reverb_slider.valueChanged.connect(self._on_reverb_changed)
        effects_layout.addWidget(self._labeled("Реверб", self.reverb_slider))
        effects_layout.addStretch()

        # Reset button
        self.audio_reset_btn = QPushButton("Сбросить")
        self.audio_reset_btn.setStyleSheet(self._small_btn_style())
        self.audio_reset_btn.clicked.connect(self._reset_audio_effects)
        effects_layout.addWidget(self.audio_reset_btn)

        self.audio_sections.addWidget(effects_w)

        # Section 1: Source
        source_w = QWidget()
        source_w.setStyleSheet("background: transparent;")
        source_layout = QVBoxLayout(source_w)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(3)
        source_layout.addWidget(self._section_label("Устройство"))
        self.audio_device_combo = self._combo([])
        self._refresh_audio_devices()
        source_layout.addWidget(self.audio_device_combo)
        self.refresh_audio_btn = QPushButton("🔄 Обновить")
        self.refresh_audio_btn.setStyleSheet(self._small_btn_style())
        self.refresh_audio_btn.clicked.connect(self._refresh_audio_devices)
        source_layout.addWidget(self.refresh_audio_btn)
        source_layout.addStretch()
        self.audio_sections.addWidget(source_w)

        # Section 2: Settings (Output)
        settings_w = QWidget()
        settings_w.setStyleSheet("background: transparent;")
        settings_layout = QVBoxLayout(settings_w)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(3)
        settings_layout.addWidget(self._section_label("Вывод"))
        self.virt_audio_device = self._combo([])
        self._refresh_virtual_audio_devices()
        settings_layout.addWidget(self.virt_audio_device)
        settings_layout.addStretch()
        self.audio_sections.addWidget(settings_w)

        layout.addWidget(self.audio_sections, stretch=1)

        self.audio_btn = QPushButton("▶ Запустить")
        self.audio_btn.setStyleSheet(self._start_btn_style())
        self.audio_btn.clicked.connect(self._toggle_audio)
        layout.addWidget(self.audio_btn)

        return w

    # ── Helpers ──

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 10px; font-weight: bold;")
        return lbl

    def _combo(self, items):
        c = QComboBox()
        c.addItems(items)
        c.setStyleSheet("""
            QComboBox { background: rgba(50,50,70,200); color: white; border: 1px solid rgba(90,90,120,130); border-radius: 4px; padding: 2px 6px; font-size: 10px; }
            QComboBox QAbstractItemView { background: rgba(35,35,50,240); color: white; border-radius: 4px; }
        """)
        return c

    def _slider(self, min_v, max_v, default):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(min_v, max_v)
        s.setValue(default)
        s.setFixedHeight(16)
        s.setStyleSheet("""
            QSlider::groove:horizontal { background: rgba(50,50,70,200); height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: rgba(100,160,255,220); width: 12px; margin: -4px 0; border-radius: 6px; }
            QSlider::sub-page:horizontal { background: rgba(80,140,255,180); border-radius: 2px; }
        """)
        return s

    def _labeled(self, label, widget):
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 9px; min-width: 50px;")
        layout.addWidget(lbl)
        layout.addWidget(widget, stretch=1)
        return row

    def _small_btn_style(self):
        return """
            QPushButton { background: rgba(50,50,70,200); color: white; border: 1px solid rgba(90,90,120,130); border-radius: 4px; padding: 2px 6px; font-size: 9px; }
            QPushButton:hover { background: rgba(70,70,90,220); }
        """

    def _start_btn_style(self):
        return """
            QPushButton { background: rgba(60,140,60,220); color: white; border: none; border-radius: 6px; padding: 5px; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background: rgba(80,160,80,240); }
        """

    def _stop_btn_style(self):
        return """
            QPushButton { background: rgba(180,50,50,220); color: white; border: none; border-radius: 6px; padding: 5px; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background: rgba(200,70,70,240); }
        """

    def _toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _cycle_mode(self):
        modes = ["⚙ Эффекты", "📹 Источник", " Настройки"]
        current = self.mode_btn.text()
        idx = (modes.index(current) + 1) % len(modes) if current in modes else 0
        self.mode_btn.setText(modes[idx])
        # Switch sections for current tab
        if self.panels.currentIndex() == 0 and hasattr(self, 'video_sections'):
            self.video_sections.setCurrentIndex(idx)
        elif self.panels.currentIndex() == 1 and hasattr(self, 'audio_sections'):
            self.audio_sections.setCurrentIndex(idx)

    def _reset_effects(self):
        self.video_effects.reset()
        self.brightness_slider.setValue(0)
        self.contrast_slider.setValue(0)
        self.saturation_slider.setValue(0)
        self.gamma_slider.setValue(10)
        self.temperature_slider.setValue(0)
        self.blur_slider.setValue(0)
        self.sharpen_slider.setValue(0)
        self.vignette_slider.setValue(0)
        self.pixelate_slider.setValue(0)
        self.sepia_slider.setValue(0)
        self.grayscale_slider.setValue(0)
        self.invert_slider.setValue(0)

    def _reset_audio_effects(self):
        self.audio_effects.pitch = 1.0
        self.audio_effects.gain = 1.0
        self.audio_effects.reverb = 0.0
        self.pitch_slider.setValue(10)
        self.gain_slider.setValue(10)
        self.reverb_slider.setValue(0)

    def _on_source_changed(self, text):
        is_cam = text == "Камера"
        is_win = text == "Окно"
        self.camera_combo.setVisible(is_cam)
        self.window_combo.setVisible(is_win)
        self.refresh_win_btn.setVisible(is_win)

    # ─ Refresh ──

    def _refresh_cameras(self):
        self.camera_combo.clear()
        for c in list_cameras():
            self.camera_combo.addItem(f"Камера {c}", c)
        if self.camera_combo.count() == 0:
            self.camera_combo.addItem("Нет камер")

    def _refresh_windows(self):
        self.window_combo.clear()
        for t in list_window_titles():
            self.window_combo.addItem(t)

    def _refresh_audio_devices(self):
        self.audio_device_combo.clear()
        for i, d in enumerate(list_audio_devices()):
            if d["max_input_channels"] > 0:
                self.audio_device_combo.addItem(f"{d['name'][:30]} ({d['max_input_channels']}in)", i)

    def _refresh_virtual_audio_devices(self):
        self.virt_audio_device.clear()
        for i, d in enumerate(list_audio_devices()):
            if d["max_output_channels"] > 0:
                self.virt_audio_device.addItem(f"{d['name'][:30]} ({d['max_output_channels']}out)", i)

    # ── Video pipeline ──

    def _toggle_video(self):
        self._stop_video() if self.video_running else self._start_video()

    def _start_video(self):
        try:
            res_text = self.res_combo.currentText()
            w, h = map(int, res_text.replace("×", "x").split("x"))
            fps = int(self.fps_combo.currentText())
            backend = "obs" if self.backend_combo.currentText() == "OBS" else "ndi"

            source = self.source_combo.currentText()
            if source == "Камера":
                idx = self.camera_combo.currentData()
                if idx is None: return
                self.video_capture = CameraCapture(camera_index=idx, fps=fps, resolution=(w, h))
            elif source == "Весь экран":
                self.video_capture = ScreenCapture(fps=fps)
            elif source == "Окно":
                title = self.window_combo.currentText()
                if not title: return
                self.video_capture = WindowCapture(window_title=title, fps=fps)

            self.video_processor = VideoProcessor(self.video_effects)
            self.processor_thread = QThread()
            self.video_processor.moveToThread(self.processor_thread)
            self.video_processor.processed_frame.connect(self._on_processed_frame)
            self.processor_thread.started.connect(self.video_processor.start)
            self.processor_thread.start()

            self.video_capture.frame_ready.connect(self.video_processor.feed)
            self.video_capture.error_occurred.connect(self._on_video_error)
            self.video_capture.start()

            self.virtual_cam = VirtualCameraOutput(w, h, fps, backend=backend)
            self.virtual_cam.error_occurred.connect(self._on_vcam_error)
            self.virtual_cam.start()

            self.video_running = True
            self._preview_timer.start()
            self.video_btn.setText("⏹ Остановить")
            self.video_btn.setStyleSheet(self._stop_btn_style())
            self.status_label.setText("Работает")
            self.status_label.setStyleSheet("color: #4f4; font-size: 9px;")

            self._screen_capturing = source in ("Весь экран", "Окно")
            # Не скрываем окно — пользователь может свернуть его сам
        except Exception as e:
            self.status_label.setText(f"Ошибка: {e}")
            self.status_label.setStyleSheet("color: #f44; font-size: 9px;")

    def _stop_video(self):
        # Stop timer first (must be from GUI thread)
        self._preview_timer.stop()

        if self.video_capture:
            self.video_capture.stop()
            self.video_capture = None
        if self.video_processor:
            self.video_processor.stop()
            self.video_processor.deleteLater()
        if self.processor_thread:
            self.processor_thread.quit()
            self.processor_thread.wait(2000)
            self.video_processor = None
            self.processor_thread = None
        if self.virtual_cam:
            self.virtual_cam.stop()
            self.virtual_cam = None

        self.video_running = False
        self._latest_frame = None
        self.video_btn.setText("▶ Запустить")
        self.video_btn.setStyleSheet(self._start_btn_style())
        self.status_label.setText("Остановлено")
        self.status_label.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 9px;")
        self.video_preview.setText("Превью")

        if self._screen_capturing:
            self.show()
            self._screen_capturing = False

    def _on_processed_frame(self, frame):
        self._latest_frame = frame
        if self.virtual_cam:
            self.virtual_cam.send_frame(frame)

    def _on_video_error(self, msg):
        self.status_label.setText(f"Ошибка: {msg}")
        self.status_label.setStyleSheet("color: #f44; font-size: 9px;")

    def _on_vcam_error(self, msg):
        QMessageBox.critical(self, "Ошибка", msg)
        self._stop_video()

    def _update_preview(self):
        if self._latest_frame is not None:
            self.video_preview.update_frame(self._latest_frame)

    # ── Audio pipeline ──

    def _toggle_audio(self):
        self._stop_audio() if self.audio_running else self._start_audio()

    def _start_audio(self):
        device_idx = self.audio_device_combo.currentData()
        if device_idx is None: return

        self.audio_effects.set_sample_rate(config.DEFAULT_SAMPLE_RATE)
        self.audio_capture = AudioCapture(device=device_idx, sample_rate=config.DEFAULT_SAMPLE_RATE, channels=config.DEFAULT_AUDIO_CHANNELS, blocksize=config.DEFAULT_AUDIO_BLOCKSIZE)
        self.audio_capture.audio_ready.connect(self._on_audio_data)
        self.audio_capture.error_occurred.connect(self._on_audio_error)
        self.audio_capture.start()

        out_id = self.virt_audio_device.currentData()
        self.virtual_audio = VirtualAudioOutput(device_id=out_id, sample_rate=config.DEFAULT_SAMPLE_RATE, channels=config.DEFAULT_AUDIO_CHANNELS, blocksize=config.DEFAULT_AUDIO_BLOCKSIZE)
        self.virtual_audio.error_occurred.connect(self._on_vaudio_error)
        self.virtual_audio.start()

        self.audio_running = True
        self.audio_btn.setText("⏹ Остановить")
        self.audio_btn.setStyleSheet(self._stop_btn_style())
        self.status_label.setText("Работает")
        self.status_label.setStyleSheet("color: #4f4; font-size: 9px;")

    def _stop_audio(self):
        if self.audio_capture:
            self.audio_capture.stop()
            self.audio_capture = None
        if self.virtual_audio:
            self.virtual_audio.stop()
            self.virtual_audio = None
        self.audio_running = False
        self.audio_btn.setText("▶ Запустить")
        self.audio_btn.setStyleSheet(self._start_btn_style())
        self.status_label.setText("Остановлено")
        self.status_label.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 9px;")
        self.audio_level.setText("Уровень")

    def _on_audio_data(self, data):
        # Update level meter only when not muted
        if not self._audio_muted:
            level = np.max(np.abs(data))
            bars = int(level * 25)
            db = int(20 * np.log10(level + 1e-9))
            self.audio_level.setText("█" * bars + "░" * (25 - bars) + f"\n{db} dB")

        # Always send audio to virtual microphone
        processed = self.audio_effects.apply(data)
        if self.virtual_audio:
            self.virtual_audio.send_audio(processed)

    def _toggle_mute(self):
        self._audio_muted = self.mute_btn.isChecked()
        if self._audio_muted:
            self.mute_btn.setText("🔈 Вкл")
            self.mute_btn.setStyleSheet("background: rgba(180,50,50,200); color: white; border: none; border-radius: 4px; padding: 2px 6px; font-size: 9px;")
        else:
            self.mute_btn.setText("🔇 Мут")
            self.mute_btn.setStyleSheet(self._small_btn_style())

    def _on_audio_error(self, msg):
        self.status_label.setText(f"Ошибка: {msg}")
        self.status_label.setStyleSheet("color: #f44; font-size: 9px;")

    def _on_vaudio_error(self, msg):
        QMessageBox.critical(self, "Ошибка", msg)
        self._stop_audio()

    def _on_pitch_changed(self, v): self.audio_effects.pitch = v / 10.0
    def _on_gain_changed(self, v): self.audio_effects.gain = v / 10.0
    def _on_reverb_changed(self, v): self.audio_effects.reverb = v / 100.0

    # ── Dragging ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        if self.video_running: self._stop_video()
        if self.audio_running: self._stop_audio()
        event.accept()
