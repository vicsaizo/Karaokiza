"""Karaokiza: convierte enlaces de YouTube (u otras webs) y audios en canciones UltraStar.

Ventanita sobre UltraSinger (motor\\UltraSinger, con su propio .venv). Sin Docker.
"""

import ctypes
import json
import os
import queue
import time
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Karaokiza"
APP_VERSION = "1.1.0"
APP_TITLE = f"{APP_NAME} · conversor para UltraStar"
GITHUB_URL = "https://github.com/vicsaizo/Karaokiza"
ITCH_URL = "https://vicsaizo.itch.io/karaokiza"
KOFI_URL = "https://ko-fi.com/vicsaizo"
CONTACT_EMAIL = "vicsaizo@vicsaizo.com"
LATEST_RELEASE_API = "https://api.github.com/repos/vicsaizo/Karaokiza/releases/latest"

FROZEN = getattr(sys, "frozen", False)
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))  # cosas metidas dentro del exe

# Si el motor ya está junto al exe (C:\Karaoke) se usa ahí; si no, el exe es también el
# instalador y lo monta todo en el perfil del usuario
if (EXE_DIR / "motor").exists():
    BASE_DIR = EXE_DIR
    DEFAULT_OUTPUT = EXE_DIR / "Canciones"
else:
    # No en AppData\Local: ahí uv no consigue instalar Python (falla el enlace de versión).
    # Quien instaló la versión antigua (carpeta «Karaoke») conserva su motor: mover la carpeta
    # rompería el entorno de Python, que guarda rutas absolutas.
    LEGACY_DIR = Path.home() / "Karaoke"
    BASE_DIR = LEGACY_DIR if (LEGACY_DIR / "motor").exists() else Path.home() / "Karaokiza"
    DEFAULT_OUTPUT = BASE_DIR / "Canciones"

MOTOR_ROOT = BASE_DIR / "motor"
MOTOR_DIR = MOTOR_ROOT / "UltraSinger"
MOTOR_PYTHON = MOTOR_DIR / ".venv" / "Scripts" / "python.exe"
MOTOR_SCRIPT = MOTOR_DIR / "src" / "UltraSinger.py"
MODELS_DIR = MOTOR_ROOT / "modelos"     # solo en instalaciones hechas por el exe
FFMPEG_DIR = MOTOR_ROOT / "ffmpeg" / "bin"
CONFIG_PATH = BASE_DIR / "karaokiza.json"
LEGACY_CONFIG_PATH = BASE_DIR / "karaoke.json"
LOG_DIR = BASE_DIR / "registros"
YTDLP_UPDATE_EVERY = 3 * 24 * 3600  # YouTube rompe yt-dlp cada pocas semanas
# Compilación LGPL (incluye libmp3lame) servida por GitHub: rápida y sin obligaciones GPL
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"

# Idiomas con modelo de alineación por defecto en WhisperX
LANGUAGES = {
    "Automático": None,
    "Español": "es",
    "Inglés": "en",
    "Francés": "fr",
    "Alemán": "de",
    "Italiano": "it",
    "Portugués": "pt",
    "Neerlandés": "nl",
    "Japonés": "ja",
    "Chino": "zh",
    "Ucraniano": "uk",
}
LANGUAGE_NAMES = {code: name for name, code in LANGUAGES.items() if code}

# Mensajes de UltraSinger -> etapa que ve el usuario (en orden)
STAGES = [
    ("Downloading", "Descargando"),
    ("Extracting audio", "Preparando audio"),
    ("Separating", "Separando la voz"),
    ("demucs", "Separando la voz"),
    ("Converting audio for AI", "Preparando la voz"),
    ("BPM is", "Midiendo el ritmo"),
    ("Detecting musical key", "Buscando la tonalidad"),
    ("Transcribing", "Escribiendo la letra"),
    ("Detected language", "Escribiendo la letra"),
    ("Calculating silence", "Ajustando la letra"),
    ("Pitch", "Sacando las notas"),
    ("SwiftF0", "Sacando las notas"),
    ("Creating midi_segments", "Sacando las notas"),
    ("Creating UltraStar file", "Escribiendo el karaoke"),
    ("Creating midi file", "Terminando"),
]
STAGE_ORDER = list(dict.fromkeys(label for _, label in STAGES))

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LANG_RE = re.compile(r"Detected language:?\s*([a-z]{2,3})\s*\(([\d.]+)\)")
FOLDER_RE = re.compile(r"Creating output folder\. -> (.+)$")
TXT_RE = re.compile(r"Creating UltraStar file (.+\.txt)")

CREATE_NO_WINDOW = 0x08000000


def normalize_input(text: str) -> str | None:
    """Deja un enlace o ruta listo para UltraSinger; None si no vale."""
    text = text.strip().strip('"')
    if not text:
        return None
    if os.path.isfile(text):
        return text
    if text.startswith("http://"):
        text = "https://" + text[len("http://"):]
    if not text.startswith("https://"):
        if re.match(r"^(www\.|m\.)?(youtube\.com|youtu\.be)/", text):
            text = "https://" + text
        else:
            return None
    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc.lower()
    # YouTube: quedarnos solo con el vídeo (sin listas ni minutos)
    if host.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    if host.endswith("youtube.com"):
        if parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/")[2]
        else:
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else text
    return text


def load_config() -> dict:
    for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
    return {}


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:4])


def latest_release() -> tuple[str, str] | None:
    """(versión, enlace) de la última versión publicada en GitHub; None si no hay o falla."""
    request = urllib.request.Request(LATEST_RELEASE_API, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        return data["tag_name"], data.get("html_url", GITHUB_URL + "/releases/latest")
    except (OSError, ValueError, KeyError):
        return None


def save_config(config: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def uv_exe() -> str:
    for candidate in (MOTOR_ROOT / "uv.exe", RESOURCES / "uv.exe"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("uv") or "uv"


def ram_gb() -> float:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullTotalPhys / 1024 ** 3


@dataclass
class Hardware:
    gpu_name: str | None
    vram_gb: float
    ram_gb: float

    @property
    def use_gpu(self) -> bool:
        return self.gpu_name is not None and self.vram_gb >= 4

    @property
    def whisper_model(self) -> str:
        # large-v2 necesita >8 GB de VRAM; en CPU, medium con 16 GB de RAM y small con menos
        if self.use_gpu:
            return "large-v2" if self.vram_gb >= 10 else "medium"
        return "medium" if self.ram_gb >= 15 else "small"

    def describe(self) -> str:
        if self.use_gpu:
            return (f"Gráfica {self.gpu_name} ({self.vram_gb:.0f} GB) · modelo {self.whisper_model} · "
                    "1-3 min por canción")
        text = (f"Sin gráfica NVIDIA: modo lento (modelo {self.whisper_model}), unos 15-25 min por canción. "
                "Puedes dejarlo trabajando con varias en cola.")
        if self.ram_gb < 15:
            text += f" Solo hay {self.ram_gb:.0f} GB de RAM: cierra el navegador y otros programas."
        return text


def detect_hardware() -> Hardware:
    ram = ram_gb()
    if os.environ.get("KARAOKE_FORCE_CPU"):  # para probar el modo portátil en un PC con gráfica
        return Hardware(None, 0, ram)
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        name, vram = out.stdout.strip().splitlines()[0].rsplit(",", 1)
        return Hardware(name.replace("NVIDIA GeForce ", "").strip(), float(vram) / 1024, ram)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return Hardware(None, 0, ram)


def motor_env() -> dict:
    """Entorno para ejecutar el motor: ffmpeg y modelos propios si los instaló el exe."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1",
               NO_COLOR="1", TERM="dumb")
    env["PATH"] = os.pathsep.join([str(MOTOR_DIR / ".venv" / "Scripts"), str(FFMPEG_DIR), fresh_path()])
    if MODELS_DIR.exists():
        env["HF_HOME"] = str(MODELS_DIR / "huggingface")
        env["TORCH_HOME"] = str(MODELS_DIR / "torch")
    return env


def fresh_path() -> str:
    """PATH actual de Windows (puede haber cambiado tras abrir el programa)."""
    try:
        import winreg
        parts = []
        for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
            except OSError:
                pass
        return os.pathsep.join(parts + [os.environ.get("PATH", "")])
    except ImportError:
        return os.environ.get("PATH", "")


def kill_tree(proc: subprocess.Popen) -> None:
    if proc and proc.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)


@dataclass
class Job:
    source: str
    display: str
    language: str | None
    output_dir: Path
    status: str = "En cola"
    detected: str | None = None
    confidence: float | None = None
    song_dir: str | None = None
    txt_path: str | None = None
    log: list[str] = field(default_factory=list)
    item_id: str = ""


class KaraokeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.events: queue.Queue = queue.Queue()
        self.jobs: list[Job] = []
        self.worker: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.cancel_requested = False
        self.hardware = detect_hardware()

        root.title(APP_TITLE)
        root.geometry("860x620")
        root.minsize(680, 480)
        self._build_ui()
        self._check_motor()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._drain_events)
        threading.Thread(target=self._check_for_update, daemon=True).start()
        # Archivos o enlaces arrastrados sobre el icono: se convierten directamente
        if len(sys.argv) > 1:
            self.input_text.insert("end", "\n".join(sys.argv[1:]))
            root.after(500, self._start)

    # ---------- interfaz ----------
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)
        style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"))

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x")
        ttk.Button(header, text="Acerca de · Apoyar ♥", command=self._show_about).pack(side="right")
        ttk.Label(header, text="Pega uno o varios enlaces (uno por línea) o añade audios/vídeos",
                  style="Title.TLabel").pack(side="left")
        ttk.Label(main, text=self.hardware.describe(), wraplength=820,
                  foreground="#1a7f37" if self.hardware.use_gpu else "#b26a00").pack(anchor="w", pady=(2, 0))
        self.update_label = tk.Label(main, text="", fg="#0b5cad", cursor="hand2", font=("Segoe UI", 10, "underline"))
        self.update_url = GITHUB_URL + "/releases/latest"
        self.update_label.bind("<Button-1>", lambda _e: webbrowser.open(self.update_url))

        self.input_text = tk.Text(main, height=5, font=("Segoe UI", 10), wrap="none", undo=True)
        self.input_text.pack(fill="x", pady=(6, 6))

        row = ttk.Frame(main)
        row.pack(fill="x")
        ttk.Button(row, text="Pegar", command=self._paste).pack(side="left")
        ttk.Button(row, text="Añadir archivos…", command=self._add_files).pack(side="left", padx=6)

        ttk.Label(row, text="Idioma:").pack(side="left", padx=(18, 4))
        self.language_var = tk.StringVar(value=self.config.get("language", "Automático"))
        ttk.Combobox(row, textvariable=self.language_var, values=list(LANGUAGES), state="readonly",
                     width=13).pack(side="left")

        self.convert_btn = ttk.Button(row, text="Convertir", style="Big.TButton", command=self._start)
        self.convert_btn.pack(side="right")

        out_row = ttk.Frame(main)
        out_row.pack(fill="x", pady=(10, 4))
        ttk.Label(out_row, text="Guardar en:").pack(side="left")
        self.output_var = tk.StringVar(value=self.config.get("output_dir", str(DEFAULT_OUTPUT)))
        ttk.Entry(out_row, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(out_row, text="Cambiar…", command=self._choose_output).pack(side="left")
        ttk.Button(out_row, text="Abrir carpeta", command=self._open_output).pack(side="left", padx=(6, 0))

        columns = ("cancion", "estado", "idioma")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=8)
        self.tree.heading("cancion", text="Canción")
        self.tree.heading("estado", text="Estado")
        self.tree.heading("idioma", text="Idioma")
        self.tree.column("cancion", width=430)
        self.tree.column("estado", width=230)
        self.tree.column("idioma", width=120)
        self.tree.pack(fill="both", expand=True, pady=(8, 4))
        self.tree.bind("<Double-1>", self._open_selected)
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("error", foreground="#c62828")
        self.tree.tag_configure("warn", foreground="#b26a00")

        prog_row = ttk.Frame(main)
        prog_row.pack(fill="x")
        self.progress = ttk.Progressbar(prog_row, mode="determinate", maximum=len(STAGE_ORDER))
        self.progress.pack(side="left", fill="x", expand=True)
        self.cancel_btn = ttk.Button(prog_row, text="Cancelar", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(6, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(main, textvariable=self.status_var).pack(anchor="w", pady=(4, 0))

        self.details_shown = tk.BooleanVar(value=False)
        ttk.Checkbutton(main, text="Ver detalles", variable=self.details_shown,
                        command=self._toggle_details).pack(anchor="w", pady=(6, 0))
        self.log_text = tk.Text(main, height=10, font=("Consolas", 9), state="disabled", wrap="none")

    # ---------- acerca de y versión nueva ----------
    def _show_about(self):
        win = tk.Toplevel(self.root)
        win.title(f"Acerca de {APP_NAME}")
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=18)
        frame.pack()
        ttk.Label(frame, text=f"{APP_NAME} {APP_VERSION}", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        text = (
            "Convierte enlaces de YouTube y tus audios en karaokes para UltraStar:\n"
            "detecta el idioma, separa la voz, escribe la letra y saca las notas.\n\n"
            "Es gratis. Si te ha sido útil, puedes invitarme a un café ♥\n\n"
            "Hecho por vicsaizo sobre UltraSinger (Vadim Rangnau, licencia MIT),\n"
            "con FFmpeg, yt-dlp, Whisper, Demucs y PyTorch.\n\n"
            "Privacidad: no recoge ni envía datos tuyos. Solo consulta GitHub para saber\n"
            "si hay una versión nueva, y descarga canciones y modelos de sus webs oficiales.\n\n"
            "Usa solo contenido que tengas derecho a usar: descargar de YouTube puede ir\n"
            "contra sus condiciones de uso."
        )
        ttk.Label(frame, text=text, justify="left").pack(anchor="w", pady=(8, 10))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Invítame a un café ♥", style="Big.TButton",
                   command=lambda: webbrowser.open(KOFI_URL)).pack(side="left")
        ttk.Button(buttons, text="itch.io", command=lambda: webbrowser.open(ITCH_URL)).pack(side="left", padx=6)
        ttk.Button(buttons, text="GitHub", command=lambda: webbrowser.open(GITHUB_URL)).pack(side="left")
        ttk.Button(buttons, text="Licencias", command=self._open_licenses).pack(side="left", padx=6)

        ttk.Label(frame, text=f"Dudas y fallos: {CONTACT_EMAIL}", foreground="#555").pack(anchor="w", pady=(10, 0))
        check = tk.BooleanVar(value=self.config.get("check_updates", True))

        def toggle():
            self.config["check_updates"] = check.get()
            save_config(self.config)

        ttk.Checkbutton(frame, text="Avisarme cuando haya una versión nueva", variable=check,
                        command=toggle).pack(anchor="w", pady=(6, 0))
        ttk.Button(frame, text="Cerrar", command=win.destroy).pack(anchor="e")

    def _open_licenses(self):
        for candidate in (EXE_DIR / "LICENCIAS.txt", EXE_DIR / "_internal" / "LICENCIAS.txt",
                          RESOURCES / "LICENCIAS.txt"):
            if candidate.exists():
                os.startfile(candidate)
                return
        webbrowser.open(GITHUB_URL)

    def _check_for_update(self):
        """En segundo plano; si no hay internet o no hay versión publicada, no pasa nada."""
        if not self.config.get("check_updates", True):
            return
        found = latest_release()
        if found and version_tuple(found[0]) > version_tuple(APP_VERSION):
            self._emit("update", found)

    def _toggle_details(self):
        if self.details_shown.get():
            self.log_text.pack(fill="both", expand=True, pady=(4, 0))
        else:
            self.log_text.pack_forget()

    def _append_log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
            self.log_text.delete("1.0", "500.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _paste(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        current = self.input_text.get("1.0", "end").strip()
        self.input_text.insert("end", ("\n" if current else "") + text.strip())

    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="Elige audios o vídeos",
            filetypes=[("Audio y vídeo", "*.mp3 *.wav *.flac *.m4a *.ogg *.mp4 *.mkv *.webm *.mov"),
                       ("Todos", "*.*")])
        for f in files:
            current = self.input_text.get("1.0", "end").strip()
            self.input_text.insert("end", ("\n" if current else "") + f)

    def _choose_output(self):
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or str(BASE_DIR))
        if folder:
            self.output_var.set(folder)

    def _open_output(self):
        folder = Path(self.output_var.get())
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def _open_selected(self, _event=None):
        for item in self.tree.selection():
            job = next((j for j in self.jobs if j.item_id == item), None)
            if job and job.song_dir and os.path.isdir(job.song_dir):
                os.startfile(job.song_dir)

    # ---------- motor ----------
    def _check_motor(self):
        if MOTOR_PYTHON.exists() and MOTOR_SCRIPT.exists():
            self.status_var.set("Listo. Pega un enlace y pulsa Convertir.")
            return
        self.status_var.set("Falta instalar el motor de conversión.")
        size = "unos 8 GB" if self.hardware.use_gpu else "unos 3 GB"
        if messagebox.askyesno(
                APP_TITLE,
                "Bienvenido. Antes de la primera canción hay que instalar el motor de conversión.\n\n"
                f"Se instalará en:\n{BASE_DIR}\n\n"
                f"Descarga {size} (y los modelos de IA con la primera canción). "
                "Tarda entre 10 y 30 minutos según la conexión. Solo hace falta una vez.\n\n"
                f"{self.hardware.describe()}\n\n¿Empezamos?"):
            self._run_in_thread(self._install_motor)

    def _install_motor(self):
        self._set_busy(True)
        try:
            ok = self._install_steps()
        except Exception as e:  # cualquier fallo inesperado se ve en la ventana, no en silencio
            self._emit("log", f"Error instalando: {e!r}")
            ok = False
        if ok:
            self.config["ytdlp_updated"] = time.time()
            save_config(self.config)
            self._emit("status", "Motor instalado. Pega un enlace y pulsa Convertir.")
        else:
            self._emit("status", "La instalación falló. Marca «Ver detalles» para ver por qué y vuelve a abrir el programa.")
        self._emit("progress", 0)
        self._set_busy(False)

    def _install_steps(self) -> bool:
        motor_zip = RESOURCES / "motor.zip"
        if not motor_zip.exists():
            self._emit("log", f"Este {APP_NAME}.exe no lleva el motor dentro ({motor_zip}).")
            return False
        MOTOR_ROOT.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, UV_LINK_MODE="copy", UV_PYTHON_PREFERENCE="only-managed",
                   UV_PYTHON_INSTALL_DIR=str(MOTOR_ROOT / "python"),
                   UV_CACHE_DIR=str(MOTOR_ROOT / "uv-cache"))
        env["PATH"] = fresh_path()
        total = 6 if self.hardware.use_gpu else 5

        def step(n, text):
            self._emit("status", f"Instalando ({n}/{total}): {text}…")
            self._emit("progress", (n - 1) / total * len(STAGE_ORDER))

        step(1, "copiando el motor")
        if not MOTOR_SCRIPT.exists():
            with zipfile.ZipFile(motor_zip) as z:
                z.extractall(MOTOR_DIR)
        shutil.copy2(RESOURCES / "uv.exe", MOTOR_ROOT / "uv.exe")
        uv = str(MOTOR_ROOT / "uv.exe")

        step(2, "descargando Python y librerías (lo más largo)")
        if self._run_streaming([uv, "sync", "--frozen", "--extra", "windows", "--python", "3.12"],
                               MOTOR_DIR, env) != 0:
            return False

        if self.hardware.use_gpu:
            step(3, f"preparando la gráfica {self.hardware.gpu_name}")
            if self._run_streaming([uv, "pip", "install", "torch==2.8.0", "torchvision==0.23.0",
                                    "torchaudio==2.8.0", "--index-url", "https://download.pytorch.org/whl/cu128",
                                    "--no-deps", "--reinstall-package", "torch", "--reinstall-package",
                                    "torchvision", "--reinstall-package", "torchaudio"], MOTOR_DIR, env) != 0:
                return False

        step(total - 2, "actualizando el descargador de YouTube")
        if self._run_streaming([uv, "pip", "install", "-U", "yt-dlp[default]", "yt-dlp-ejs"], MOTOR_DIR, env) != 0:
            return False

        step(total - 1, "descargando ffmpeg")
        if not (FFMPEG_DIR / "ffmpeg.exe").exists() and not shutil.which("ffmpeg", path=fresh_path()):
            self._download_ffmpeg()

        # Modelos de IA ahora y no con la primera canción: desde HuggingFace pueden ser 5-10 min
        step(total, f"descargando los modelos de IA (unos 2 GB, el más grande es Whisper {self.hardware.whisper_model})")
        MODELS_DIR.mkdir(exist_ok=True)
        prefetch = ("from faster_whisper.utils import download_model; "
                    f"download_model('{self.hardware.whisper_model}'); "
                    "import demucs.pretrained; demucs.pretrained.get_model('htdemucs'); "
                    "import torchaudio; torchaudio.pipelines.VOXPOPULI_ASR_BASE_10K_ES.get_model(); "
                    "torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H.get_model()")
        if self._run_streaming([str(MOTOR_PYTHON), "-c", prefetch], MOTOR_DIR, motor_env()) != 0:
            self._emit("log", "No se pudieron descargar los modelos ahora; se descargarán con la primera canción.")
        shutil.rmtree(MOTOR_ROOT / "uv-cache", ignore_errors=True)  # ya copiado al entorno: ahorra GBs
        return True

    def _download_ffmpeg(self):
        FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = MOTOR_ROOT / "ffmpeg.zip"
        self._emit("log", f"Descargando {FFMPEG_URL}")
        last = {"pct": -1}

        def report(blocks, block_size, total):
            pct = min(100, blocks * block_size * 100 // total) if total > 0 else 0
            if pct // 5 != last["pct"] // 5:
                last["pct"] = pct
                self._emit("status", f"Instalando: descargando ffmpeg… {pct} %")

        urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook=report)
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if name.endswith(("/bin/ffmpeg.exe", "/bin/ffprobe.exe")):
                    (FFMPEG_DIR / Path(name).name).write_bytes(z.read(name))
        zip_path.unlink()

    def _update_downloader(self):
        """Actualiza yt-dlp si hace días que no se hace; si falla, se sigue con el que hay."""
        if time.time() - self.config.get("ytdlp_updated", 0) < YTDLP_UPDATE_EVERY:
            return
        self._emit("status", "Actualizando el descargador de YouTube…")
        env = dict(os.environ, UV_LINK_MODE="copy", PATH=fresh_path())
        if MODELS_DIR.exists():  # instalación hecha por el exe: caché temporal propia
            env["UV_CACHE_DIR"] = str(MOTOR_ROOT / "uv-cache")
        code = self._run_streaming([uv_exe(), "pip", "install", "-U", "yt-dlp[default]", "yt-dlp-ejs"],
                                   MOTOR_DIR, env)
        if MODELS_DIR.exists():
            shutil.rmtree(MOTOR_ROOT / "uv-cache", ignore_errors=True)
        if code == 0:
            self.config["ytdlp_updated"] = time.time()
            save_config(self.config)

    def _run_streaming(self, cmd, cwd, env, on_line=None) -> int:
        # Windows busca el ejecutable con el PATH de este proceso, no con el de env
        cmd = [shutil.which(cmd[0], path=env.get("PATH")) or cmd[0]] + list(cmd[1:])
        try:
            self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=CREATE_NO_WINDOW, text=True,
                                         encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            self._emit("log", f"No se pudo ejecutar {cmd[0]}: {e}")
            return -1
        buffer = ""
        while True:
            chunk = self.proc.stdout.read(1)
            if not chunk:
                break
            if chunk in "\r\n":
                line = ANSI_RE.sub("", buffer).rstrip()
                buffer = ""
                if line:
                    self._emit("log", line)
                    if on_line:
                        on_line(line)
            else:
                buffer += chunk
        return self.proc.wait()

    # ---------- conversión ----------
    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        if not (MOTOR_PYTHON.exists() and MOTOR_SCRIPT.exists()):
            self._check_motor()
            return
        lines = [l for l in self.input_text.get("1.0", "end").splitlines() if l.strip()]
        sources, bad = [], []
        for l in lines:
            src = normalize_input(l)
            (sources if src else bad).append(src or l.strip())
        if bad:
            messagebox.showwarning(APP_TITLE, "No entiendo estas líneas (no son enlaces ni archivos):\n\n"
                                   + "\n".join(bad[:10]))
        if not sources:
            return
        output_dir = Path(self.output_var.get() or DEFAULT_OUTPUT)
        output_dir.mkdir(parents=True, exist_ok=True)
        language = LANGUAGES.get(self.language_var.get())
        self.config.update(language=self.language_var.get(), output_dir=str(output_dir))
        save_config(self.config)

        new_jobs = []
        for src in dict.fromkeys(sources):
            display = Path(src).name if os.path.isfile(src) else src
            job = Job(source=src, display=display, language=language, output_dir=output_dir)
            job.item_id = self.tree.insert("", "end", values=(display, job.status, ""))
            new_jobs.append(job)
        self.jobs.extend(new_jobs)
        self.input_text.delete("1.0", "end")
        self.cancel_requested = False
        self._run_in_thread(lambda: self._process_jobs(new_jobs))

    def _process_jobs(self, jobs: list[Job]):
        self._set_busy(True)
        self._update_downloader()
        done = failed = 0
        for n, job in enumerate(jobs, 1):
            if self.cancel_requested:
                self._update_job(job, "Cancelada", "warn")
                continue
            self._emit("status", f"Canción {n} de {len(jobs)}")
            if self._convert(job):
                done += 1
            else:
                failed += 1
        summary = f"Terminado: {done} lista(s)"
        if failed:
            summary += f", {failed} con error"
        self._emit("status", summary + ". Doble clic en una canción para abrir su carpeta.")
        self._emit("progress", 0)
        self._set_busy(False)

    def _convert(self, job: Job) -> bool:
        cmd = [str(MOTOR_PYTHON), "-u", str(MOTOR_SCRIPT), "-i", job.source, "-o", str(job.output_dir),
               "--format_version", "1.1.0", "--whisper", self.hardware.whisper_model]
        if job.language:
            cmd += ["--language", job.language]
        env = motor_env()
        if not self.hardware.use_gpu:
            cmd += ["--force_cpu"]
            env["CUDA_VISIBLE_DEVICES"] = ""

        self._update_job(job, "Empezando…")
        self._emit("progress", 0)
        reached = {"index": -1}

        def on_line(line: str):
            job.log.append(line)
            for key, label in STAGES:
                if key.lower() in line.lower():
                    idx = STAGE_ORDER.index(label)
                    if idx > reached["index"]:
                        reached["index"] = idx
                        self._update_job(job, label + "…")
                        self._emit("progress", idx + 1)
                    break
            if m := LANG_RE.search(line):
                job.detected, job.confidence = m.group(1), float(m.group(2))
                self._update_job(job, None)
            if (m := FOLDER_RE.search(line)) and Path(m.group(1).strip()).name != "cache":
                job.song_dir = m.group(1).strip()
                self._update_job(job, None, display=Path(job.song_dir).name)
            if m := TXT_RE.search(line):
                job.txt_path = m.group(1).strip()

        self._emit("log", f"===== {job.source} =====")
        code = self._run_streaming(cmd, MOTOR_DIR / "src", env, on_line)
        self._write_job_log(job)

        if self.cancel_requested:
            self._update_job(job, "Cancelada", "warn")
            return False
        txt_ok = job.txt_path and os.path.isfile(job.txt_path)
        if code != 0 or not txt_ok:
            self._update_job(job, self._explain_error(job), "error")
            return False
        if not job.language and job.confidence is not None and job.confidence < 0.6:
            self._update_job(job, "Lista, pero revisa el idioma", "warn")
        else:
            self._update_job(job, "Lista ✓", "ok")
        return True

    @staticmethod
    def _explain_error(job: Job) -> str:
        text = "\n".join(job.log[-80:]).lower()
        if "sign in to confirm" in text or "not a bot" in text:
            return "Error: YouTube pide iniciar sesión"
        if "video unavailable" in text or "private video" in text:
            return "Error: vídeo no disponible"
        if "out of memory" in text:
            return "Error: memoria de la gráfica llena"
        if "unsupported url" in text:
            return "Error: web no compatible"
        return "Error (ver detalles)"

    def _write_job_log(self, job: Job):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            name = re.sub(r'[\\/:*?"<>|]+', "_", Path(job.song_dir).name if job.song_dir else job.display)[:80]
            (LOG_DIR / f"{name}.log").write_text("\n".join(job.log), encoding="utf-8")
        except OSError:
            pass

    def _cancel(self):
        self.cancel_requested = True
        kill_tree(self.proc)
        self.status_var.set("Cancelando…")

    # ---------- hilos y eventos ----------
    def _run_in_thread(self, target):
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _emit(self, kind, payload):
        self.events.put((kind, payload))

    def _update_job(self, job: Job, status: str | None, tag: str = "", display: str | None = None):
        if status is not None:
            job.status = status
        if display:
            job.display = display
        self._emit("job", (job, tag))

    def _set_busy(self, busy: bool):
        self._emit("busy", busy)

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "update":
                    version, url = payload
                    self.update_url = url
                    self.update_label.configure(text=f"Hay una versión nueva ({version}). Pulsa aquí para descargarla.")
                    self.update_label.pack(anchor="w", pady=(2, 0))
                elif kind == "progress":
                    self.progress["value"] = payload
                elif kind == "busy":
                    self.convert_btn.configure(state="disabled" if payload else "normal")
                    self.cancel_btn.configure(state="normal" if payload else "disabled")
                elif kind == "job":
                    job, tag = payload
                    lang = ""
                    if job.language:
                        lang = LANGUAGE_NAMES.get(job.language, job.language) + " (elegido)"
                    elif job.detected:
                        lang = LANGUAGE_NAMES.get(job.detected, job.detected)
                        if job.confidence is not None:
                            lang += f" ({job.confidence:.0%})"
                    self.tree.item(job.item_id, values=(job.display, job.status, lang),
                                   tags=(tag,) if tag else ())
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_TITLE, "Hay una conversión en marcha. ¿Cerrar y cancelarla?"):
                return
            self.cancel_requested = True
            kill_tree(self.proc)
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "karaoke.ico"
    if icon.exists():
        root.iconbitmap(default=str(icon))
    KaraokeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
