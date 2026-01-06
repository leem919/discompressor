import sys
import os
import shutil
import subprocess
import threading
import zipfile
import requests
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QMessageBox,
    QHBoxLayout,
    QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QPoint
from PyQt6.QtGui import QIcon

BLURPLE = "#5865F2"
DARK_GRAY = "#2C2F33"
DARKER_GRAY = "#23272A"
GREEN = "#57F287"
RED = "#ED4245"
WHITE = "#FFFFFF"

FFMPEG_DIR = Path("ffmpeg_bin")
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"
FFPROBE_EXE = FFMPEG_DIR / "ffprobe.exe"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def ffmpeg_in_path():
    return shutil.which("ffmpeg") is not None


def ffprobe_in_path():
    return shutil.which("ffprobe") is not None


def bundled_ffmpeg_exists():
    return FFMPEG_EXE.exists() and FFPROBE_EXE.exists()


def get_ffmpeg_paths():
    if bundled_ffmpeg_exists():
        return str(FFMPEG_EXE), str(FFPROBE_EXE)
    if ffmpeg_in_path() and ffprobe_in_path():
        return "ffmpeg", "ffprobe"
    return None, None


def get_ffmpeg_download_size():
    try:
        with requests.get(FFMPEG_URL, stream=True) as r:
            r.raise_for_status()
            return int(r.headers.get("Content-Length", 0))
    except:
        return 0


def download_ffmpeg(progress_callback=None):
    zip_path = Path("ffmpeg.zip")

    with requests.get(FFMPEG_URL, stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0

        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded / total)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall("ffmpeg_temp")

    extracted_root = next(Path("ffmpeg_temp").iterdir())
    bin_dir = extracted_root / "bin"

    FFMPEG_DIR.mkdir(exist_ok=True)
    shutil.move(str(bin_dir / "ffmpeg.exe"), FFMPEG_EXE)
    shutil.move(str(bin_dir / "ffprobe.exe"), FFPROBE_EXE)

    shutil.rmtree("ffmpeg_temp")
    zip_path.unlink()

    return str(FFMPEG_EXE), str(FFPROBE_EXE)


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


class WorkerSignals(QObject):
    progress = pyqtSignal(float)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)


class InstallerSignals(QObject):
    progress = pyqtSignal(float)
    finished = pyqtSignal()
    error = pyqtSignal(str)


class InstallerWorker(threading.Thread):
    def __init__(self, signals: InstallerSignals):
        super().__init__(daemon=True)
        self.signals = signals

    def run(self):
        try:
            download_ffmpeg(lambda p: self.signals.progress.emit(p))
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(str(e))


class CompressionWorker(threading.Thread):
    def __init__(self, ffmpeg, ffprobe, input_path, target_mb, signals):
        super().__init__(daemon=True)
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.input_path = input_path
        self.target_mb = target_mb
        self.signals = signals
        self.process = None

    def run(self):
        try:
            duration = self.get_duration()
            if not duration:
                self.signals.error.emit("Could not read video duration.")
                return

            target_bytes = self.target_mb * 1024 * 1024
            bitrate = int((target_bytes * 8) / duration)

            output_path = os.path.splitext(self.input_path)[0] + f"_{self.target_mb}mb.mp4"

            cmd = [
                self.ffmpeg,
                "-i",
                self.input_path,
                "-b:v",
                str(bitrate),
                "-bufsize",
                str(bitrate),
                "-y",
                output_path,
            ]

            self.process = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                creationflags=0x08000000
            )


            for line in self.process.stderr:
                if "time=" in line:
                    try:
                        timestamp = line.split("time=")[1].split(" ")[0]
                        h, m, s = timestamp.split(":")
                        current = float(h) * 3600 + float(m) * 60 + float(s)
                        progress = min(current / duration, 1.0)
                        self.signals.progress.emit(progress)
                    except:
                        pass

            self.process.wait()

            if self.process.returncode != 0:
                return

            self.signals.finished.emit(output_path)

        except Exception as e:
            self.signals.error.emit(str(e))

    def get_duration(self):
        cmd = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            self.input_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            return float(result.stdout.strip())
        except:
            return None


class DropLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.parent().load_file(urls[0].toLocalFile())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.parent().browse_file()


class FFmpegInstallerWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Setting up FFmpeg…")
        self.setStyleSheet(f"background-color: {DARK_GRAY}; color: {WHITE};")
        self.resize(400, 120)

        layout = QVBoxLayout()
        self.setLayout(layout)

        label = QLabel("Downloading FFmpeg…")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)


class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.offset = QPoint(0, 0)
        self.parent = parent
        self.setFixedHeight(40)
        self.setAutoFillBackground(False)

        layout = QHBoxLayout()
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(10)
        self.setLayout(layout)

        self.title = QLabel("Discompressor")
        self.title.setStyleSheet(
            f"color: {WHITE}; font-size: 16px; background-color: transparent;"
        )
        layout.addWidget(self.title)

        layout.addStretch()

        self.min_btn = QPushButton("\u2212")
        self.min_btn.setObjectName("titlebar_button")
        self.min_btn.setFixedSize(30, 30)
        self.min_btn.clicked.connect(self.parent.showMinimized)
        layout.addWidget(self.min_btn)

        self.close_btn = QPushButton("\u2715")
        self.close_btn.setObjectName("titlebar_close")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.clicked.connect(self.parent.close)
        layout.addWidget(self.close_btn)

    def mousePressEvent(self, event):
        self.offset = event.pos()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.parent.move(event.globalPosition().toPoint() - self.offset)



class VideoCompressor(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        self.worker = None
        self.worker_process = None
        self.file_path = None
        self.ffmpeg, self.ffprobe = get_ffmpeg_paths()

        self.setWindowTitle("Discompressor")
        self.resize(600, 400)

        layout = QVBoxLayout()
        self.setLayout(layout)

        self.titlebar = TitleBar(self)
        layout.addWidget(self.titlebar)

        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {DARK_GRAY};
                color: {WHITE};
                font-size: 14px;
            }}

            QPushButton {{
                background-color: {BLURPLE};
                color: {WHITE};
                border: none;
                border-radius: 8px;
                padding: 10px 16px;
                font-weight: 500;
            }}

            QPushButton:hover {{
                background-color: #4752C4;
            }}

            QPushButton:pressed {{
                background-color: #3C45A5;
            }}

            QPushButton:disabled {{
                background-color: #3C45A5;
                color: #888888;
            }}


            QPushButton#clear {{
                background-color: {RED};
            }}

            QPushButton#clear:hover {{
                background-color: #C03535;
            }}

            QPushButton#clear:pressed {{
                background-color: #A52A2A;
            }}

            QPushButton#titlebar_button {{
                background-color: transparent;
                color: white;
                border-radius: 6px;
                padding: 0px;
                font-size: 18px;
            }}

            QPushButton#titlebar_button:hover {{
                background-color: #3A3D41;
            }}

            QPushButton#titlebar_button:pressed {{
                background-color: #2E3135;
            }}

            QPushButton#titlebar_close {{
                background-color: transparent;
                color: white;
                border-radius: 6px;
                padding: 0px;
                font-size: 18px;
            }}

            QPushButton#titlebar_close:hover {{
                background-color: {RED};
                color: white;
            }}

            QPushButton#titlebar_close:pressed {{
                background-color: #A52A2A;
                color: white;
            }}

            QPushButton#titlebar_close:disabled {{
                background-color: #2E3135;
                color: #777777;
            }}


            QProgressBar {{
                background-color: {DARKER_GRAY};
                border: 2px solid #1E1F22;
                border-radius: 8px;
                text-align: center;
                height: 20px;
                color: {WHITE};
            }}

            QProgressBar::chunk {{
                background-color: {GREEN};
                border-radius: 8px;
            }}
        """
        )

        self.drop_label = DropLabel(self)
        self.drop_label.setText("Drag & Drop Video Here (Click to Browse)")
        self.drop_label.setStyleSheet(
            f"background-color: {DARKER_GRAY}; border: 2px dashed {WHITE}; padding: 40px;"
        )
        self.drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.drop_label)

        btn_row = QHBoxLayout()
        layout.addLayout(btn_row)

        self.mb_buttons = []
        for size in (10, 50, 500):
            btn = QPushButton(f"{size} MB")
            btn.clicked.connect(lambda _, s=size: self.start_compress(s))
            btn_row.addWidget(btn)
            self.mb_buttons.append(btn)

        self.clear_btn = QPushButton("Clear/Cancel")
        self.clear_btn.setObjectName("clear")
        self.clear_btn.clicked.connect(self.clear)
        layout.addWidget(self.clear_btn)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)


    def set_ui_enabled(self, enabled: bool):
        for btn in self.mb_buttons:
            btn.setEnabled(enabled)

        self.drop_label.setEnabled(enabled)

        self.titlebar.min_btn.setEnabled(True)

        self.titlebar.close_btn.setEnabled(enabled)

        self.clear_btn.setEnabled(True)


    def load_file(self, path):
        self.file_path = path
        self.drop_label.setText(os.path.basename(path))

    def browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mp4 *.mov *.avi *.mkv *.webm);;All Files (*)",
        )
        if path:
            self.load_file(path)

    def clear(self):
        if self.worker and self.worker.process:
            try:
                self.worker.process.terminate()
            except:
                pass

        self.worker = None
        self.worker_process = None

        self.progress.setValue(0)
        self.drop_label.setText("Drag & Drop Video Here (Click to Browse)")
        self.file_path = None

        self.set_ui_enabled(True)


    def start_compress(self, size):
        if not self.file_path:
            QMessageBox.warning(self, "No File", "Please select a video first.")
            return

        if not self.ffmpeg:
            QMessageBox.warning(self, "FFmpeg Missing", "FFmpeg is not installed.")
            return

        self.progress.setValue(0)
        self.set_ui_enabled(False)

        signals = WorkerSignals()
        signals.progress.connect(lambda v: self.progress.setValue(int(v * 100)))
        signals.finished.connect(self.finish)
        signals.error.connect(self.error)

        self.worker = CompressionWorker(
            self.ffmpeg, self.ffprobe, self.file_path, size, signals
        )
        self.worker.start()

    def finish(self, output_path):
        self.set_ui_enabled(True)
        self.progress.setValue(100)
        QMessageBox.information(self, "Done", f"Saved:\n{output_path}")

    def error(self, msg):
        self.set_ui_enabled(True)
        QMessageBox.critical(self, "Error", msg)



def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("blcompress.ico")))

    ffmpeg, ffprobe = get_ffmpeg_paths()

    if not ffmpeg:
        size_bytes = get_ffmpeg_download_size()
        size_mb = size_bytes / (1024 * 1024) if size_bytes else 0

        reply = QMessageBox.question(
            None,
            "FFmpeg Missing",
            (
                "FFmpeg is required but not installed.\n\n"
                "Would you like to download it now?\n\n"
                f"Download size: {size_mb:.2f} MB" if size_mb else
                "FFmpeg is required but not installed.\n\n"
                "Would you like to download it now?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            installer = FFmpegInstallerWindow()

            def launch_main():
                window = VideoCompressor()
                window.show()

            signals = InstallerSignals()
            signals.progress.connect(
                lambda p: installer.progress.setValue(int(p * 100))
            )
            signals.finished.connect(lambda: (installer.close(), launch_main()))
            signals.error.connect(
                lambda msg: QMessageBox.critical(None, "Error", msg)
            )

            worker = InstallerWorker(signals)
            worker.start()

            installer.show()
        else:
            window = VideoCompressor()
            window.show()
    else:
        window = VideoCompressor()
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
