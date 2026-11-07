"""Interfață grafică: drag & drop / click pentru alegerea video-ului sursă + setări + progres."""
import json
import os
import sys
from datetime import datetime

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from shorts import config, media, pipeline

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts")

STYLE = """
QWidget { background: #14161c; color: #e8eaf0; font-size: 14px; }
QLabel, QCheckBox { background: transparent; }
QLineEdit, QSpinBox, QPlainTextEdit { background: #1d2029; border: 1px solid #2c3040; border-radius: 6px; padding: 6px; }
QPushButton { background: #2a3045; border: none; border-radius: 8px; padding: 9px 16px; }
QPushButton:hover { background: #353d58; }
QPushButton#start { background: #f5c400; color: #14161c; font-weight: bold; font-size: 15px; }
QPushButton#start:hover { background: #ffd633; }
QPushButton:disabled { background: #22252f; color: #666; }
QProgressBar { border: 1px solid #2c3040; border-radius: 6px; text-align: center; background: #1d2029; }
QProgressBar::chunk { background: #f5c400; border-radius: 5px; }
QFrame#drop { border: 2px dashed #4a5272; border-radius: 14px; background: #181b24; }
QFrame#drop[hover="true"] { border-color: #f5c400; background: #1d2130; }
QLabel#hint { color: #8a90a6; }
"""


class DropZone(QFrame):
    fileChosen = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(150)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        self.title = QLabel("Trage aici video-ul sursă\nsau fă click pentru a-l alege")
        self.title.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        self.title.setFont(f)
        self.info = QLabel("")
        self.info.setObjectName("hint")
        self.info.setAlignment(Qt.AlignCenter)
        self.info.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.info)

    def _hover(self, on: bool):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            path, _ = QFileDialog.getOpenFileName(
                self, "Alege video-ul sursă", os.path.expanduser("~"),
                "Video (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts);;Toate fișierele (*)")
            if path:
                self.fileChosen.emit(path)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(VIDEO_EXTS) for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(VIDEO_EXTS):
                self.fileChosen.emit(p)
                break

    def show_video(self, path: str, details: str):
        self.title.setText(os.path.basename(path))
        self.info.setText(details + "\n(click sau trage alt fișier pentru a schimba)")


class Worker(QThread):
    log = Signal(str)
    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, video, n, req, game, music, voice):
        super().__init__()
        self.args = (video, n, req, game, music, voice)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        video, n, req, game, music, voice = self.args
        try:
            res = pipeline.run(video, n, req, game, music, voice, log=self.log.emit,
                               progress=lambda d, t: self.progress.emit(d, t), stop=lambda: self._stop)
            self.done.emit(True, f"Gata! {len(res)} clipuri generate în {config.OUTPUT_DIR}")
        except InterruptedError:
            self.done.emit(False, "Oprit de utilizator.")
        except Exception as e:
            self.done.emit(False, f"Eroare: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Shorts Generator")
        self.resize(760, 760)
        self.video = ""
        self.worker = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)

        self.drop = DropZone()
        self.drop.fileChosen.connect(self.set_video)
        root.addWidget(self.drop)

        row = QHBoxLayout()
        row.addWidget(QLabel("Număr clipuri:"))
        self.n = QSpinBox()
        self.n.setRange(1, 100)
        self.n.setValue(3)
        row.addWidget(self.n)
        row.addSpacing(16)
        row.addWidget(QLabel("Joc:"))
        self.game = QLineEdit(config.DEFAULT_GAME)
        self.game.setMaximumWidth(160)
        row.addWidget(self.game)
        row.addSpacing(16)
        row.addWidget(QLabel("Voce:"))
        self.voice = QComboBox()
        self.voice.addItems(config.VOICES)
        self.voice.setCurrentText(config.KOKORO_VOICE)
        row.addWidget(self.voice)
        row.addStretch()
        self.music = QCheckBox("Muzică de fundal")
        self.music.setChecked(True)
        row.addWidget(self.music)
        root.addLayout(row)

        root.addWidget(QLabel("Cerințe speciale (opțional, ex: mașini, plajă, urmăriri cu poliția):"))
        self.req = QLineEdit()
        self.req.setPlaceholderText("lasă gol pentru cele mai bune momente în general")
        root.addWidget(self.req)

        outrow = QHBoxLayout()
        self.out_label = QLabel("")
        self.out_label.setObjectName("hint")
        outrow.addWidget(self.out_label, 1)
        newf = QPushButton("Folder output nou")
        newf.clicked.connect(self.new_output_folder)
        outrow.addWidget(newf)
        out = QPushButton("Deschide folder")
        out.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(config.OUTPUT_DIR)))
        outrow.addWidget(out)
        mus = QPushButton("Folder muzică")
        mus.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(config.MUSIC_DIR)))
        outrow.addWidget(mus)
        root.addLayout(outrow)

        btns = QHBoxLayout()
        self.start = QPushButton("Generează shorts")
        self.start.setObjectName("start")
        self.start.clicked.connect(self.start_run)
        self.stop = QPushButton("Oprește")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_run)
        btns.addWidget(self.start, 3)
        btns.addWidget(self.stop, 1)
        root.addLayout(btns)


        self.bar = QProgressBar()
        self.bar.setValue(0)
        root.addWidget(self.bar)

        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        root.addWidget(self.logbox, 1)

        self._load_settings()

    # --- setări ---
    def _load_settings(self):
        try:
            with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            s = {}
        self.n.setValue(int(s.get("n", 3)))
        self.game.setText(s.get("game", config.DEFAULT_GAME))
        self.music.setChecked(bool(s.get("music", True)))
        v = s.get("voice", config.KOKORO_VOICE)
        if v in config.VOICES:
            self.voice.setCurrentText(v)
        out = s.get("output_dir", "")
        if out and os.path.isdir(out):
            config.set_output_dir(out)
        self._refresh_out_label()
        vid = s.get("video", "")
        if vid and os.path.isfile(vid):
            self.set_video(vid)
        elif os.path.isfile(config.DEFAULT_VIDEO):
            self.set_video(config.DEFAULT_VIDEO)

    def _save_settings(self):
        with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"n": self.n.value(), "game": self.game.text(), "music": self.music.isChecked(),
                       "voice": self.voice.currentText(), "output_dir": config.OUTPUT_DIR,
                       "video": self.video}, f)

    def _refresh_out_label(self):
        rel = os.path.relpath(config.OUTPUT_DIR, config.BASE_DIR)
        self.out_label.setText(f"Folder output: {rel}")

    def new_output_folder(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Rulare în curs", "Așteaptă sau oprește generarea curentă.")
            return
        default = datetime.now().strftime("batch_%Y-%m-%d_%H%M")
        name, ok = QInputDialog.getText(self, "Folder output nou",
                                        "Numele noului folder (se creează în output_shorts/):", text=default)
        name = "".join(c for c in name.strip() if c not in '\\/:*?"<>|')
        if not ok or not name:
            return
        path = os.path.join(config.OUTPUT_ROOT, name)
        os.makedirs(path, exist_ok=True)
        config.set_output_dir(path)
        self._refresh_out_label()
        self._save_settings()
        self.log(f"Folder nou activ: {path}")

    # --- acțiuni ---
    def set_video(self, path: str):
        try:
            i = media.probe(path)
            details = f"{i['width']}x{i['height']} • {i['fps']:.0f} fps • {i['duration'] / 60:.1f} min"
        except Exception:
            QMessageBox.warning(self, "Video invalid", "Fișierul nu a putut fi citit de ffprobe.")
            return
        self.video = path
        self.drop.show_video(path, details)

    def log(self, msg: str):
        self.logbox.appendPlainText(msg)

    def start_run(self):
        if not self.video:
            QMessageBox.information(self, "Alege video", "Alege mai întâi video-ul sursă.")
            return
        self._save_settings()
        self.logbox.clear()
        self.bar.setValue(0)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.worker = Worker(self.video, self.n.value(), self.req.text().strip(),
                             self.game.text().strip() or config.DEFAULT_GAME, self.music.isChecked(),
                             self.voice.currentText())
        self.worker.log.connect(self.log)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def stop_run(self):
        if self.worker:
            self.log("Se oprește după pasul curent...")
            self.worker.stop()

    def on_progress(self, d, t):
        self.bar.setMaximum(max(1, t))
        self.bar.setValue(d)
        self.bar.setFormat(f"{d}/{t} clipuri")

    def on_done(self, ok, msg):
        self.log("\n" + msg)
        self.start.setEnabled(True)
        self.stop.setEnabled(False)
        if ok:
            QDesktopServices.openUrl(QUrl.fromLocalFile(config.OUTPUT_DIR))


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
