#!/usr/bin/env python3
"""
Jarvis Desktop Agent - KDE Plasma Integration
Voice-enabled desktop assistant that communicates with Jarvis AI.
"""

import os
import sys
import json
import re
import time
import socket
import threading
import subprocess
import tempfile
import logging
import uuid
import shutil
from pathlib import Path
from datetime import datetime

# Import TOOLS_SCHEMA for agentic capabilities
try:
    from agent_tools import TOOLS_SCHEMA
except ImportError:
    TOOLS_SCHEMA = []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jarvis-agent")

CONFIG_DIR = Path.home() / ".config" / "jarvis-agent"
CONFIG_FILE = CONFIG_DIR / "config.json"
SOCKET_PATH = "/tmp/jarvis-agent.sock"
TEMP_DIR = Path(tempfile.gettempdir()) / "jarvis-agent"

DEFAULT_CONFIG = {
    "jarvis_url": "http://localhost:8000",
    "jarvis_model": "gemma-4-E2B-worker",
    "user_id": "alfio_dev",
    "stt_enabled": True,
    "tts_enabled": True,
    "whisper_model": "tiny",
    "whisper_device": "auto",
    "whisper_compute": "int8",
    "language": "it",
    "response_language": "it",
    "show_notifications": True,
    "show_tray": True,
    "record_mode": "always_on",
    "vad_aggressiveness": 3,
    "silence_timeout": 0.8,
    "vad_pre_ms": 300,
    "vad_post_ms": 500,
    "vad_min_speech_frames": 10,
    "vad_min_utterance_ms": 400,
    "tts_slow": False,
    "tts_tld": "it",
    "tts_pitch": 50,
    "tts_speed": 175,
    "_theme_mode": "system",
}

try:
    from PIL import Image as PILImage
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import (QIcon, QAction, QPixmap, QFont, QTextCursor,
        QTextCharFormat, QTextBlockFormat, QColor, QPalette)
    from PySide6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QDialog,
        QFormLayout, QLineEdit, QCheckBox, QDialogButtonBox, QInputDialog, QSpinBox,
        QDoubleSpinBox, QTextEdit, QPlainTextEdit, QPushButton, QHBoxLayout, QVBoxLayout,
        QWidget, QScrollBar, QLabel, QFrame, QSizePolicy, QGroupBox, QComboBox, QSplitter,
        QListWidget, QListWidgetItem)
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

try:
    import httpx
    HAVE_HTTPX = True
except ImportError:
    HAVE_HTTPX = False

try:
    from faster_whisper import WhisperModel
    HAVE_WHISPER = True
except ImportError:
    HAVE_WHISPER = False

try:
    from gtts import gTTS
    HAVE_TTS = True
except ImportError:
    HAVE_TTS = False


class JarvisAgent:
    def __init__(self):
        self.config = self.load_config()
        self.running = True
        self.is_recording = False
        self.is_processing = False
        self.record_process = None
        self.whisper_model = None
        self.whisper_loaded = False
        self.record_file = None
        self.status_label = "Pronto"
        self.api_client = None
        self.last_response = ""
        self._main_loop = None
        self._timer = None
        self._icon_path = None
        self._menu_handle = None
        self._always_on_thread = None
        self._stop_always_on = False
        self._whisper_loading = False
        self._last_whisper_model = None
        self.chat_window = None
        self.tray = None
        self.tray_menu = None
        self._conversation_id = str(uuid.uuid4())

        TEMP_DIR.mkdir(parents=True, exist_ok=True)

        self._create_icon_file()

        self._start_socket_server()

        if HAVE_HTTPX:
            self.api_client = httpx.Client(
                base_url=self.config["jarvis_url"],
                timeout=httpx.Timeout(120.0, connect=5.0),
                headers={"Content-Type": "application/json"},
            )

        if HAVE_WHISPER and self.config["stt_enabled"]:
            self._last_whisper_model = self.config.get("whisper_model", "tiny")
            self._whisper_loading = True
            threading.Thread(target=self._init_whisper, daemon=True).start()

    def _create_icon_file(self):
        if not HAVE_PIL:
            return
        try:
            from PIL import ImageDraw
            img = PILImage.new("RGBA", (48, 48), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            cx, cy, r = 24, 24, 18
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(74, 144, 226, 255))
            draw.ellipse([cx - 6, cy - 8, cx + 6, cy + 2], fill=(255, 255, 255, 220))
            draw.arc([cx - 10, cy - 14, cx + 10, cy + 6], 0, 180,
                     fill=(255, 255, 255, 220), width=2)
            draw.rectangle([cx - 3, cy + 10, cx + 3, cy + 16], fill=(255, 255, 255, 200))
            self._icon_path = TEMP_DIR / "icon.png"
            img.save(str(self._icon_path), "PNG")
        except Exception as e:
            log.error(f"Errore creazione icona: {e}")
            try:
                img = PILImage.new("RGBA", (48, 48), (74, 144, 226, 255))
                self._icon_path = TEMP_DIR / "icon.png"
                img.save(str(self._icon_path), "PNG")
            except Exception:
                self._icon_path = None

    def load_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    cfg = json.load(f)
                    return {**DEFAULT_CONFIG, **cfg}
            except Exception as e:
                log.error(f"Errore lettura config: {e}")
        self.save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)

    def save_config(self, cfg=None):
        if cfg is None:
            cfg = self.config
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            log.error(f"Errore salvataggio config: {e}")

    def _start_socket_server(self):
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        server.listen(5)
        os.chmod(SOCKET_PATH, 0o666)
        t = threading.Thread(target=self._socket_loop, args=(server,), daemon=True)
        t.start()

    def _socket_loop(self, server):
        while self.running:
            try:
                conn, _ = server.accept()
                data = conn.recv(4096)
                if data:
                    command = data.decode("utf-8").strip()
                    result = self._handle_socket_command(command)
                    if result != "ok":
                        conn.send(result.encode("utf-8"))
                conn.close()
            except Exception as e:
                if self.running:
                    log.error(f"Errore socket: {e}")
        server.close()

    def _handle_socket_command(self, command):
        log.info(f"Comando socket: {command}")
        if command in ("toggle", "record"):
            self._on_activate()
        elif command == "stop":
            self._stop_recording()
        elif command == "text":
            self._show_text_dialog()
        elif command == "quit":
            self.quit()
        elif command == "status":
            return self.status_label
        return "ok"

    def _on_activate(self):
        if self.is_recording:
            self._stop_recording()
        elif self.is_processing:
            pass
        else:
            self._start_recording()

    # ── Whisper Model ──

    def _init_whisper(self):
        if not HAVE_WHISPER:
            self._whisper_loading = False
            return
        try:
            model = self.config["whisper_model"]
            log.info(f"Caricamento Whisper '{model}'...")
            device = self.config["whisper_device"]
            compute = self.config["whisper_compute"]
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            new_model = WhisperModel(
                model, device=device, compute_type=compute,
                cpu_threads=4, num_workers=1)
            old = self.whisper_model
            self.whisper_model = new_model
            self.whisper_loaded = True
            if old is not None:
                del old
            log.info(f"Whisper pronto ({device}, {compute})")
        except Exception as e:
            log.error(f"Errore caricamento Whisper: {e}")
            self.whisper_loaded = False
        finally:
            self._whisper_loading = False

    # ── Audio Recording (with VAD silence detection) ──

    def _record_vad(self):
        import webrtcvad
        import struct
        import wave

        aggressiveness = self.config.get("vad_aggressiveness", 2)
        silence_timeout = self.config.get("silence_timeout", 1.2)
        pre_ms = self.config.get("vad_pre_ms", 300)
        post_ms = self.config.get("vad_post_ms", 500)

        vad = webrtcvad.Vad(aggressiveness)
        sample_rate = 16000
        frame_ms = 30
        frame_size = int(sample_rate * 2 * frame_ms / 1000)
        max_silence_frames = int(silence_timeout * 1000 / frame_ms)
        pre_frames = int(pre_ms / frame_ms)
        post_frames = int(post_ms / frame_ms)

        self._update_status("In ascolto...")
        self._notify("Jarvis", "Ascolto... parla ora")

        proc = subprocess.Popen(
            ["arecord", "-D", "default", "-f", "S16_LE", "-r", str(sample_rate),
             "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        frames = []
        speech_frames = []
        ring = []
        silence_count = 0
        speech_detected = False
        post_count = 0
        started = time.time()
        max_duration = 30

        try:
            while True:
                chunk = proc.stdout.read(frame_size)
                if not chunk or len(chunk) < frame_size:
                    break
                if time.time() - started > max_duration:
                    break

                is_speech = vad.is_speech(chunk, sample_rate)
                ring.append(chunk)
                if len(ring) > pre_frames:
                    ring.pop(0)

                if is_speech:
                    if not speech_detected:
                        speech_detected = True
                        frames.extend(ring)
                    frames.append(chunk)
                    silence_count = 0
                    post_count = 0
                elif speech_detected:
                    frames.append(chunk)
                    post_count += 1
                    if post_count >= post_frames:
                        break
                else:
                    silence_count += 1
                    if silence_count > max_silence_frames and not speech_detected:
                        break
        finally:
            proc.terminate()
            proc.wait(timeout=3)

        if not frames or len(frames) < 5:
            self._update_status("Pronto")
            return None

        audio = b''.join(frames)
        duration = len(frames) * frame_ms / 1000
        log.info(f"Audio acquisito: {duration:.1f}s, {len(audio)} bytes")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = TEMP_DIR / f"recording_{ts}.wav"
        with wave.open(str(wav_path), 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio)

        return wav_path

    def _record_manual(self):
        self._update_status("In ascolto...")
        self._notify("Jarvis", "Ascolto... parla ora")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = TEMP_DIR / f"recording_{ts}.wav"
        proc = subprocess.Popen(
            ["arecord", "-D", "default", "-f", "S16_LE", "-r", "16000",
             "-c", "1", "-t", "wav", str(wav_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.record_process = proc
        return wav_path

    def _start_recording(self):
        if self.is_recording or self.is_processing:
            return
        mode = self.config.get("record_mode", "always_on")
        if mode == "always_on":
            self._notify("Jarvis", "Già in ascolto continuo")
            return
        self.is_recording = True

        if mode == "vad":
            threading.Thread(target=self._record_vad_thread, daemon=True).start()
        else:
            wav_path = self._record_manual()
            if wav_path:
                self.record_file = wav_path
                self._timer = threading.Timer(15, self._stop_recording)
                self._timer.daemon = True
                self._timer.start()

    def _record_vad_thread(self):
        try:
            wav_path = self._record_vad()
            self.is_recording = False
            if wav_path and wav_path.exists():
                sz = wav_path.stat().st_size
                log.info(f"File VAD: {sz} bytes")
                if sz < 2048:
                    self._notify("Jarvis", "Nessun audio rilevato")
                    self._update_status("Pronto")
                    return
                self.record_file = wav_path
                self._update_status("Trascrizione...")
                self.is_processing = True
                threading.Thread(target=self._process_audio_from, args=(wav_path,), daemon=True).start()
            else:
                self._update_status("Pronto")
        except Exception as e:
            log.error(f"Errore registrazione VAD: {e}")
            self._notify("Jarvis", f"Errore registrazione: {str(e)[:50]}")
            self.is_recording = False
            self._update_status("Pronto")

    def _stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._update_status("Trascrizione...")
        if self.record_process:
            try:
                self.record_process.terminate()
                self.record_process.wait(timeout=3)
            except Exception:
                if self.record_process:
                    self.record_process.kill()
        if self.record_file and self.record_file.exists():
            sz = self.record_file.stat().st_size
            if sz < 1024:
                self._notify("Jarvis", "Nessun audio rilevato")
                self._update_status("Pronto")
                return
            threading.Thread(target=self._process_audio_from, args=(self.record_file,), daemon=True).start()
        else:
            self._notify("Jarvis", "Errore registrazione")
            self._update_status("Pronto")

    def _process_audio_from(self, audio_path):
        self.is_processing = True
        try:
            d = self._get_audio_duration(str(audio_path))
            if d and d < 0.4:
                log.info(f"Audio troppo corto ({d:.2f}s), scartato")
                self._update_status("In ascolto continuo...")
                self.is_processing = False
                return
            text = self._transcribe(audio_path)
            if not text or not text.strip():
                self._notify("Jarvis", "Non ho capito. Riprova.")
                self._update_status("In ascolto continuo..." if self.config.get("record_mode") == "always_on" else "Pronto")
                self.is_processing = False
                return
            self._chat_add_message("user", text)
            self._notify("Jarvis", f"Ho sentito: {text[:80]}...")
            response = self._query_jarvis(text)
            if response:
                self.last_response = response
                self._chat_add_message("assistant", response)
                if self.config["tts_enabled"] and HAVE_TTS:
                    self._speak(response)
                else:
                    self._notify("Jarvis", response)
            else:
                self._notify("Jarvis", "Errore nella risposta")
        except Exception as e:
            log.error(f"Errore processamento: {e}")
            self._notify("Jarvis", f"Errore: {str(e)[:60]}")
        self.is_processing = False
        self._update_status("In ascolto continuo..." if self.config.get("record_mode") == "always_on" else "Pronto")

    def _transcribe(self, audio_file):
        if not self.whisper_loaded or not self.whisper_model:
            return ""
        try:
            d = self._get_audio_duration(str(audio_file))
            if d and d < 0.3:
                return ""
            segs, _ = self.whisper_model.transcribe(
                str(audio_file),
                language=self.config.get("language", "it"),
                beam_size=1, vad_filter=True)
            return " ".join(s.text for s in segs).strip()
        except Exception as e:
            log.error(f"Errore trascrizione: {e}")
            return ""

    def _classify_quick(self, text: str) -> bool:
        """Returns True for quick/greeting queries (no project context needed), False for code/project queries."""
        quick_patterns = [
            r'^(ciao|salve|buongiorno|buonasera|buon pomeriggio|ehilà|hey|grazie|grazie mille|ti ringrazio)$',
            r'^(come stai|come va|tutto bene|che fai)$',
            r'^(che ore sono|che giorno è|che data è)$',
            r'^(arrivederci|ciao|a dopo|a presto)$',
        ]
        clean = text.strip().lower()
        for pattern in quick_patterns:
            if re.match(pattern, clean):
                return True
        return False

    def _query_jarvis(self, text):
        if not HAVE_HTTPX or not self.api_client:
            return ""
        try:
            payload = {
                "model": self.config["jarvis_model"],
                "messages": [
                    {"role": "system", "content": (
                        "Sei Jarvis, un assistente AI personale. "
                        "Rispondi in modo conciso e naturale in italiano.\n"
                        "Puoi controllare il DESKTOP usando [azione: comando]:\n"
                        "- [azione: finestre] — elenca le finestre aperte\n"
                        "- [azione: apri <url>] — apre il browser su una pagina\n"
                        "- [azione: avvia <applicazione>] — avvia un'applicazione\n"
                        "- [azione: cerca <testo>] — cerca su Google\n\n"
                        "Puoi interagire con file e codice usando i TOOL messi a disposizione nella chat "
                        "(read_file, search_code, git_status, ecc).\n\n"
                        "Puoi usare tag XML per azioni backend:\n"
                        "- <EXEC>comando</EXEC> — comandi shell readonly (whitelist)\n"
                        "- <MEMORY>testo</MEMORY> — salva in memoria\n"
                        "- <SCHEDULE>cron|task</SCHEDULE> — promemoria ricorrenti\n"
                        "- <SSH>server|comando</SSH> — esecuzione remota\n"
                        "- <TODO_ADD>desc|prio|scadenza</TODO_ADD> — aggiungi task\n\n"
                        "Esempio: 'Certo, apro il sito. [azione: apri https://example.com]'\n"
                        "Se l'utente chiede informazioni su impegni, eventi o task, "
                        "usa il task manager integrato per verificare. "
                        "Se chiede di creare promemoria o task, creali. "
                        "Sii sempre utile e diretto.")},
                    {"role": "user", "content": text}],
                "stream": False,
                "user_id": self.config.get("user_id", "alfio_dev"),
                "tools": TOOLS_SCHEMA,
                "conversation_id": self._conversation_id if hasattr(self, '_conversation_id') else "default",
                "options": {"concise": self._classify_quick(text)}}
            resp = self.api_client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw = ""
            if "message" in data and "content" in data["message"]:
                raw = data["message"]["content"]
            elif "response" in data:
                raw = data["response"]
            # Execute desktop actions from raw text, return clean display text
            display = self._execute_desktop_actions(raw)
            if display:
                return display
            return self._clean_response(raw)
        except httpx.ConnectError:
            self._notify("Jarvis", "Jarvis non raggiungibile.")
            return ""
        except httpx.TimeoutException:
            self._notify("Jarvis", "Jarvis non risponde (timeout)")
            return ""
        except Exception as e:
            log.error(f"Errore API: {e}")
            return ""

    def _clean_response(self, text):
        text = text or ""
        # Delegato a tag_processor per pulizia thinking blocks e tag action
        from tag_processor import strip_thinking_blocks, strip_all_tags
        # Usa model_family dal profilo se disponibile (filtra thinking pattern)
        _family = "all"
        try:
            from config import MODEL_PROFILE
            _family = MODEL_PROFILE.family
        except Exception:
            pass
        text = strip_thinking_blocks(text, model_family=_family)
        text = strip_all_tags(text)
        text = self._strip_action_tags(text)
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            log.warning("Risposta pulita vuota, uso testo grezzo")
        return text

    # ── Desktop Actions ──

    def _strip_action_tags(self, text):
        return re.sub(r'\s*\[azione:\s*.*?\]\s*', ' ', text).strip()

    def _execute_desktop_actions(self, text):
        actions = re.findall(r'\[azione:\s*(.*?)\]', text)
        for action in actions:
            log.info(f"Azione desktop: {action}")
            self._run_desktop_action(action.strip())
        return self._strip_action_tags(text)

    def _run_desktop_action(self, cmd):
        try:
            if cmd == "finestre":
                self._list_windows()
            elif cmd.startswith("apri "):
                url = cmd[5:].strip()
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                subprocess.Popen(["xdg-open", url])
                self._notify("Jarvis", f"Apro {url}")
            elif cmd.startswith("avvia "):
                app = cmd[6:].strip()
                subprocess.Popen([app])
                self._notify("Jarvis", f"Avvio {app}")
            elif cmd.startswith("cerca "):
                query = cmd[6:].strip()
                url = f"https://www.google.com/search?q={__import__('urllib.parse').quote(query)}"
                subprocess.Popen(["xdg-open", url])
                self._notify("Jarvis", f"Cerco '{query}' su Google")
            else:
                log.warning(f"Azione sconosciuta: {cmd}")
        except Exception as e:
            log.error(f"Errore azione desktop '{cmd}': {e}")

    def _list_windows(self):
        try:
            r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
            wins = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
            if wins:
                lines = []
                for w in wins[:15]:
                    parts = w.split(None, 3)
                    if len(parts) >= 4:
                        lines.append(f"  {parts[3]}")
                    else:
                        lines.append(f"  {w}")
                msg = f"Finestre aperte ({len(wins)}):\n" + "\n".join(lines)
                self._notify("Jarvis", msg)
            else:
                self._notify("Jarvis", "Nessuna finestra attiva")
        except FileNotFoundError:
            self._notify("Jarvis", "Installa wmctrl per elencare le finestre")
        except Exception as e:
            log.error(f"Errore elenco finestre: {e}")

    def _speak(self, text):
        if not HAVE_TTS:
            return
        try:
            lang = self.config.get("response_language", "it")
            self._notify("Jarvis", text[:200])

            # Try espeak-ng first (offline, supports pitch/speed/voice)
            espeak = shutil.which("espeak-ng") or shutil.which("espeak")
            if espeak:
                pitch = self.config.get("tts_pitch", 50)
                speed = self.config.get("tts_speed", 175)
                voice = f"mb-{lang}1" if lang.startswith("it") else lang
                wav_file = TEMP_DIR / f"resp_{uuid.uuid4().hex[:8]}.wav"
                subprocess.run(
                    [espeak, "-v", voice, "-p", str(pitch),
                     "-s", str(speed), "-w", str(wav_file), text],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                if wav_file.exists() and wav_file.stat().st_size > 1024:
                    for cmd in ["ffplay", "paplay", "aplay"]:
                        exe = shutil.which(cmd)
                        if not exe:
                            continue
                        r = subprocess.run(
                            [exe, str(wav_file)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        if r.returncode == 0:
                            break
                    try:
                        wav_file.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return

            # Fallback to gTTS (online, supports slow/tld)
            slow = self.config.get("tts_slow", False)
            tld = self.config.get("tts_tld", "it")
            tts = gTTS(text=text, lang=lang, slow=slow, tld=tld)
            mp3_file = TEMP_DIR / f"resp_{uuid.uuid4().hex[:8]}.mp3"
            tts.save(str(mp3_file))

            wav_file = mp3_file.with_suffix(".wav")
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                subprocess.run(
                    [ffmpeg, "-y", "-loglevel", "quiet", "-i", str(mp3_file), str(wav_file)],
                    check=True)

            played = False
            for cmd in ["ffplay", "paplay", "aplay"]:
                exe = shutil.which(cmd)
                if not exe:
                    continue
                if cmd == "ffplay":
                    r = subprocess.run(
                        [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", str(mp3_file)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    played = r.returncode == 0
                elif wav_file.exists():
                    r = subprocess.run([exe, str(wav_file)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    played = r.returncode == 0
                if played:
                    break

            try:
                mp3_file.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                wav_file.unlink(missing_ok=True)
            except Exception:
                pass

        except Exception as e:
            log.error(f"Errore TTS: {e}")

    def _notify(self, title, message):
        if not self.config.get("show_notifications", True):
            return
        msg = str(message)[:200]
        # Use QTimer.singleShot(0, ...) for thread-safe GUI calls
        QTimer.singleShot(0, lambda: self._notify_ui(title, msg))

    def _notify_ui(self, title, message):
        if self.tray and self.tray.supportsMessages():
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 5000)
        elif shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "-i", "face-smile", "-t", "5000", title, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _get_audio_duration(self, path):
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.strip())
        except Exception:
            pass
        return None

    # ── System Tray (Qt6 QSystemTrayIcon) ──

    def _build_menu(self):
        menu = QMenu()
        menu.setToolTipsVisible(True)

        s = menu.addAction(f"Stato: {self.status_label}")
        s.setEnabled(False)

        menu.addSeparator()

        mode_action = menu.addAction("Modo: " + {
            "vad": "VAD (click)", "manual": "Manuale", "always_on": "Ascolto continuo"
        }.get(self.config.get("record_mode", "always_on"), ""))
        mode_action.triggered.connect(lambda: self._toggle_record_mode())

        sens_menu = menu.addMenu("Sensibilità VAD")
        current_vad = self.config.get("vad_aggressiveness", 3)
        sens_labels = {0: "Bassa (0)", 1: "Media (1)", 2: "Alta (2)", 3: "Molto alta (3)"}
        for val, label in sorted(sens_labels.items()):
            a = sens_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(val == current_vad)
            a.triggered.connect(lambda checked, v=val: self._set_sensitivity(v))

        menu.addSeparator()

        t = menu.addAction("Ferma registrazione" if self.is_recording else "Parla con Jarvis")
        t.triggered.connect(lambda: self._on_activate())

        tx = menu.addAction(QIcon.fromTheme("mail-message-new"), "Chat con Jarvis...")
        tx.triggered.connect(lambda: self._open_chat())

        menu.addSeparator()

        theme_menu = menu.addMenu(QIcon.fromTheme("preferences-color"), "Tema")
        current_theme = self.config.get("_theme_mode", "system")
        theme_labels = {"system": "Sistema", "chiaro": "Chiaro", "scuro": "Scuro"}
        theme_icons = {"system": "computer", "chiaro": "weather-clear", "scuro": "weather-clear-night"}
        for key, label in sorted(theme_labels.items(), key=lambda x: (x[0] != "system", x[0])):
            a = theme_menu.addAction(QIcon.fromTheme(theme_icons[key]), label)
            a.setCheckable(True)
            a.setChecked(key == current_theme)
            a.triggered.connect(lambda checked, k=key: self._apply_theme(k))

        menu.addSeparator()

        c = menu.addAction(QIcon.fromTheme("configure"), "Configurazione...")
        c.triggered.connect(lambda: self._show_config_dialog())

        self._menu_handle = menu

        menu.addSeparator()

        q = menu.addAction(QIcon.fromTheme("application-exit"), "Esci")
        q.triggered.connect(lambda: self.quit())

        return menu

    def _start_tray(self):
        if not HAVE_QT:
            log.warning("PySide6 non disponibile, tray icon disabilitata")
            return False
        icon_path = str(self._icon_path) if self._icon_path and self._icon_path.exists() else ""
        if icon_path:
            self.tray = QSystemTrayIcon(QIcon(icon_path))
        else:
            self.tray = QSystemTrayIcon(QIcon.fromTheme("face-smile"))
        self.tray.setToolTip("Jarvis Agent")
        self._rebuild_menu()
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        log.info("QSystemTrayIcon avviato")
        return True

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_activate()

    def _update_menu(self):
        if not self.tray:
            return
        QTimer.singleShot(0, self._rebuild_menu)

    def _rebuild_menu(self):
        if not self.tray:
            return
        old = self.tray_menu
        self.tray_menu = self._build_menu()
        self.tray.setContextMenu(self.tray_menu)
        if old:
            old.deleteLater()

    def _update_status(self, status):
        self.status_label = status
        log.info(f"Stato: {status}")
        QTimer.singleShot(0, lambda: self._update_status_ui(status))

    def _update_status_ui(self, status):
        if self.tray:
            self.tray.setToolTip(f"Jarvis Agent - {status}")
        self._rebuild_menu()

    # ── Theme ──

    def _apply_theme(self, mode=None):
        if mode is None:
            mode = self.config.get("_theme_mode", "system")
        self.config["_theme_mode"] = mode
        self.save_config()

        app = QApplication.instance()
        if not app:
            return

        if mode == "system":
            app.setStyle("")
            app.setStyleSheet("")
            app.setPalette(QApplication.style().standardPalette())
        else:
            app.setStyle("Fusion")
            palette = QPalette()
            if mode == "chiaro":
                palette.setColor(QPalette.ColorRole.Window, QColor(248, 249, 250))
                palette.setColor(QPalette.ColorRole.WindowText, QColor(33, 37, 41))
                palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
                palette.setColor(QPalette.ColorRole.AlternateBase, QColor(233, 236, 239))
                palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
                palette.setColor(QPalette.ColorRole.ToolTipText, QColor(33, 37, 41))
                palette.setColor(QPalette.ColorRole.Text, QColor(33, 37, 41))
                palette.setColor(QPalette.ColorRole.Button, QColor(233, 236, 239))
                palette.setColor(QPalette.ColorRole.ButtonText, QColor(33, 37, 41))
                palette.setColor(QPalette.ColorRole.BrightText, QColor(220, 53, 69))
                palette.setColor(QPalette.ColorRole.Link, QColor(13, 110, 253))
                palette.setColor(QPalette.ColorRole.Highlight, QColor(13, 110, 253))
                palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
            else:  # scuro
                palette.setColor(QPalette.ColorRole.Window, QColor(35, 38, 41))
                palette.setColor(QPalette.ColorRole.WindowText, QColor(239, 240, 241))
                palette.setColor(QPalette.ColorRole.Base, QColor(26, 28, 31))
                palette.setColor(QPalette.ColorRole.AlternateBase, QColor(35, 38, 41))
                palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(53, 56, 59))
                palette.setColor(QPalette.ColorRole.ToolTipText, QColor(239, 240, 241))
                palette.setColor(QPalette.ColorRole.Text, QColor(239, 240, 241))
                palette.setColor(QPalette.ColorRole.Button, QColor(53, 56, 59))
                palette.setColor(QPalette.ColorRole.ButtonText, QColor(239, 240, 241))
                palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
                palette.setColor(QPalette.ColorRole.Link, QColor(61, 174, 233))
                palette.setColor(QPalette.ColorRole.Highlight, QColor(61, 174, 233))
                palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
            app.setPalette(palette)

    # ── Dialoghi Qt ──

    def _show_text_dialog(self):
        if not HAVE_QT:
            self._notify("Jarvis", "PySide6 non disponibile per il dialogo testuale")
            return
        txt, ok = QInputDialog.getText(None, "Jarvis", "Scrivi il tuo messaggio:")
        if ok and txt.strip():
            self._notify("Jarvis", "Elaborazione richiesta...")
            threading.Thread(target=self._process_text_query, args=(txt.strip(),), daemon=True).start()

    def _process_text_query(self, text):
        self.is_processing = True
        self._update_status("Elaborazione...")
        try:
            self._chat_add_message("user", text)
            response = self._query_jarvis(text)
            if response:
                self.last_response = response
                self._chat_add_message("assistant", response)
                self._notify("Jarvis", response)
                if self.config["tts_enabled"] and HAVE_TTS:
                    self._speak(response)
        finally:
            self.is_processing = False
            self._update_status("Pronto")

    def _show_config_dialog(self):
        if not HAVE_QT:
            self._notify("Jarvis", "PySide6 non disponibile per la configurazione")
            return

        from PySide6.QtWidgets import (QGroupBox, QVBoxLayout, QHBoxLayout,
            QComboBox, QSpinBox, QDoubleSpinBox, QLabel)

        d = QDialog()
        d.setWindowTitle("Configurazione Jarvis")
        d.setMinimumWidth(500)
        d.resize(520, 620)
        main_layout = QVBoxLayout(d)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # ── Aspetto ──
        ga = QGroupBox("Aspetto")
        fa = QFormLayout(ga)
        fa.setSpacing(8)
        theme_combo = QComboBox()
        theme_combo.addItems(["Sistema", "Chiaro", "Scuro"])
        theme_keys = {"Sistema": "system", "Chiaro": "chiaro", "Scuro": "scuro"}
        theme_rev = {v: k for k, v in theme_keys.items()}
        theme_combo.setCurrentText(theme_rev.get(self.config.get("_theme_mode", "system"), "Sistema"))
        fa.addRow("Tema:", theme_combo)
        main_layout.addWidget(ga)

        # ── Server ──
        g1 = QGroupBox("Server")
        f1 = QFormLayout(g1)
        f1.setSpacing(8)
        url_edit = QLineEdit(self.config.get("jarvis_url", ""))
        f1.addRow("URL Jarvis:", url_edit)
        user_edit = QLineEdit(self.config.get("user_id", "alfio_dev"))
        f1.addRow("User ID:", user_edit)
        model_combo = QComboBox()
        model_combo.setEditable(True)
        model_combo.addItems(["gemma-4-E2B-worker", "gemma-4-E2B", "llama-3.1-8b", "mixtral-8x7b"])
        model_combo.setCurrentText(self.config.get("jarvis_model", "gemma-4-E2B-worker"))
        f1.addRow("Modello AI:", model_combo)
        main_layout.addWidget(g1)

        # ── Riconoscimento vocale ──
        g2 = QGroupBox("Riconoscimento vocale")
        f2 = QFormLayout(g2)
        f2.setSpacing(8)
        lang_edit = QLineEdit(self.config.get("language", "it"))
        f2.addRow("Lingua STT:", lang_edit)
        resp_lang_edit = QLineEdit(self.config.get("response_language", "it"))
        f2.addRow("Lingua TTS:", resp_lang_edit)
        whisper_combo = QComboBox()
        whisper_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        whisper_combo.setCurrentText(self.config.get("whisper_model", "tiny"))
        f2.addRow("Modello Whisper:", whisper_combo)
        main_layout.addWidget(g2)

        # ── Voce ──
        gv = QGroupBox("Voce")
        gv.setToolTip("Installa espeak-ng per controllo completo (tono, velocità)")
        fv = QFormLayout(gv)
        fv.setSpacing(8)

        slow_check = QCheckBox("Lettura lenta (gTTS)")
        slow_check.setChecked(self.config.get("tts_slow", False))
        fv.addRow(slow_check)

        tld_combo = QComboBox()
        tld_combo.addItems(["it", "com", "co.uk"])
        tld_combo.setCurrentText(self.config.get("tts_tld", "it"))
        tld_combo.setToolTip("Dominio Google TTS per accenti diversi")
        fv.addRow("Accento TLD:", tld_combo)

        pitch_spin = QSpinBox()
        pitch_spin.setRange(0, 99)
        pitch_spin.setValue(self.config.get("tts_pitch", 50))
        pitch_spin.setToolTip("Tono della voce (0=grave, 99=acuto) — richiede espeak-ng")
        fv.addRow("Tono:", pitch_spin)

        speed_spin = QSpinBox()
        speed_spin.setRange(80, 450)
        speed_spin.setValue(self.config.get("tts_speed", 175))
        speed_spin.setSuffix(" wpm")
        speed_spin.setToolTip("Velocità in parole al minuto — richiede espeak-ng")
        fv.addRow("Velocità:", speed_spin)

        main_layout.addWidget(gv)

        # ── Registrazione ──
        g3 = QGroupBox("Registrazione")
        f3 = QFormLayout(g3)
        f3.setSpacing(8)

        mode_combo = QComboBox()
        mode_combo.addItems(["Sempre in ascolto", "VAD (click-stop)", "Manuale"])
        mode_map = {"Sempre in ascolto": "always_on", "VAD (click-stop)": "vad", "Manuale": "manual"}
        mode_combo.setCurrentText({v: k for k, v in mode_map.items()}.get(self.config.get("record_mode", "always_on"), "Sempre in ascolto"))
        f3.addRow("Modalità:", mode_combo)

        sens_spin = QSpinBox()
        sens_spin.setRange(0, 3)
        sens_spin.setValue(self.config.get("vad_aggressiveness", 3))
        sens_labels = {0: "Bassa", 1: "Media", 2: "Alta", 3: "Molto alta"}
        sens_spin.setToolTip(sens_labels.get(sens_spin.value(), ""))
        sens_spin.valueChanged.connect(lambda v: sens_spin.setToolTip(sens_labels.get(v, "")))
        f3.addRow("Sensibilità VAD:", sens_spin)

        timeout_spin = QDoubleSpinBox()
        timeout_spin.setRange(0.3, 5.0)
        timeout_spin.setSingleStep(0.1)
        timeout_spin.setDecimals(1)
        timeout_spin.setSuffix(" s")
        timeout_spin.setValue(self.config.get("silence_timeout", 1.2))
        timeout_spin.setToolTip("Secondi di silenzio per fermare l'ascolto")
        f3.addRow("Timeout silenzio:", timeout_spin)

        speech_spin = QSpinBox()
        speech_spin.setRange(1, 50)
        speech_spin.setValue(self.config.get("vad_min_speech_frames", 10))
        speech_spin.setToolTip("Minimi frame classificati come parlato (30ms l'uno)")
        f3.addRow("Frame parlato min:", speech_spin)

        pre_spin = QSpinBox()
        pre_spin.setRange(0, 1000)
        pre_spin.setSingleStep(50)
        pre_spin.setSuffix(" ms")
        pre_spin.setValue(self.config.get("vad_pre_ms", 300))
        pre_spin.setToolTip("Millisecondi di audio prima del rilevamento")
        f3.addRow("Pre-roll:", pre_spin)

        post_spin = QSpinBox()
        post_spin.setRange(100, 2000)
        post_spin.setSingleStep(50)
        post_spin.setSuffix(" ms")
        post_spin.setValue(self.config.get("vad_post_ms", 500))
        post_spin.setToolTip("Millisecondi di silenzio dopo il parlato")
        f3.addRow("Post-roll:", post_spin)

        main_layout.addWidget(g3)

        # ── Opzioni ──
        g4 = QGroupBox("Opzioni")
        f4 = QFormLayout(g4)
        f4.setSpacing(8)

        stt_check = QCheckBox("Abilita riconoscimento vocale (STT)")
        stt_check.setChecked(self.config.get("stt_enabled", True))
        f4.addRow(stt_check)

        tts_check = QCheckBox("Abilita sintesi vocale (TTS)")
        tts_check.setChecked(self.config.get("tts_enabled", True))
        f4.addRow(tts_check)

        notif_check = QCheckBox("Mostra notifiche desktop")
        notif_check.setChecked(self.config.get("show_notifications", True))
        f4.addRow(notif_check)

        main_layout.addWidget(g4)

        # ── Bottoni ──
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        main_layout.addWidget(buttons)

        if d.exec() == QDialog.Accepted:
            self.config["jarvis_url"] = url_edit.text().strip()
            self.config["user_id"] = user_edit.text().strip()
            self.config["jarvis_model"] = model_combo.currentText().strip()
            self.config["language"] = lang_edit.text().strip()
            self.config["response_language"] = resp_lang_edit.text().strip()
            self.config["whisper_model"] = whisper_combo.currentText().strip()
            self.config["record_mode"] = mode_map.get(mode_combo.currentText(), "always_on")
            self.config["vad_aggressiveness"] = sens_spin.value()
            self.config["silence_timeout"] = timeout_spin.value()
            self.config["vad_min_speech_frames"] = speech_spin.value()
            self.config["vad_pre_ms"] = pre_spin.value()
            self.config["vad_post_ms"] = post_spin.value()
            self.config["stt_enabled"] = stt_check.isChecked()
            self.config["tts_enabled"] = tts_check.isChecked()
            self.config["show_notifications"] = notif_check.isChecked()
            self.config["tts_slow"] = slow_check.isChecked()
            self.config["tts_tld"] = tld_combo.currentText()
            self.config["tts_pitch"] = pitch_spin.value()
            self.config["tts_speed"] = speed_spin.value()
            new_theme = theme_keys.get(theme_combo.currentText(), "system")
            self.config["_theme_mode"] = new_theme
            self.save_config()
            self._apply_config()
            self._apply_theme(new_theme)
            self._notify("Jarvis", "Configurazione salvata")

    def _open_chat(self):
        if not HAVE_QT:
            self._notify("Jarvis", "PySide6 non disponibile per la chat")
            return
        if self.chat_window is None or not self.chat_window.isVisible():
            self.chat_window = ChatWindow(self)
            self.chat_window.show()
        else:
            self.chat_window.raise_()
            self.chat_window.activateWindow()

    def _chat_add_message(self, role, text):
        if self.chat_window and self.chat_window.isVisible():
            self.chat_window.add_message(role, text)

    def _apply_config(self):
        if HAVE_HTTPX:
            self.api_client = httpx.Client(
                base_url=self.config["jarvis_url"],
                timeout=httpx.Timeout(120.0, connect=5.0),
                headers={"Content-Type": "application/json"})
        self._apply_record_mode()
        # Reload Whisper if model changed or STT toggled on
        if self.config["stt_enabled"] and not getattr(self, "_whisper_loading", False):
            old_model = getattr(self, "_last_whisper_model", None)
            new_model = self.config.get("whisper_model", "tiny")
            if new_model != old_model or not self.whisper_loaded:
                self._last_whisper_model = new_model
                self._whisper_loading = True
                threading.Thread(target=self._init_whisper, daemon=True).start()

    def _toggle_record_mode(self):
        modes = ["always_on", "vad", "manual"]
        current = self.config.get("record_mode", "always_on")
        idx = modes.index(current) if current in modes else 0
        new = modes[(idx + 1) % len(modes)]
        self.config["record_mode"] = new
        self.save_config()
        self._apply_record_mode()
        labels = {"vad": "VAD (click-stop)", "manual": "Manuale", "always_on": "Ascolto continuo"}
        self._notify("Jarvis", f"Modalità: {labels.get(new, new)}")
        self._update_menu()

    def _set_sensitivity(self, aggressiveness):
        self.config["vad_aggressiveness"] = min(3, max(0, aggressiveness))
        self.save_config()
        self._stop_always_on = True
        # Restart the always-on loop to pick up new config
        if self.config.get("record_mode") == "always_on":
            time.sleep(0.1)
            self._always_on_thread = threading.Thread(target=self._always_on_loop, daemon=True)
            self._always_on_thread.start()
        labels = {0: "Bassa", 1: "Media", 2: "Alta", 3: "Molto alta"}
        self._notify("Jarvis", f"Sensibilità VAD: {labels.get(aggressiveness, aggressiveness)}")
        self._update_menu()

    def _apply_record_mode(self):
        mode = self.config.get("record_mode", "always_on")
        loop = getattr(self, "_always_on_thread", None)
        if mode == "always_on":
            if not loop or not loop.is_alive():
                self._always_on_thread = threading.Thread(target=self._always_on_loop, daemon=True)
                self._always_on_thread.start()
        elif loop and loop.is_alive():
            self._stop_always_on = True

    def _always_on_loop(self):
        import webrtcvad
        import wave
        from collections import deque

        vad = webrtcvad.Vad(self.config.get("vad_aggressiveness", 2))
        sample_rate = 16000
        frame_ms = 30
        frame_size = int(sample_rate * 2 * frame_ms / 1000)
        max_silence_frames = int(self.config.get("silence_timeout", 1.2) * 1000 / frame_ms)
        pre_frames = int(self.config.get("vad_pre_ms", 300) / frame_ms)
        post_frames = int(self.config.get("vad_post_ms", 500) / frame_ms)

        self._stop_always_on = False
        log.info("Always-on loop avviato")

        while self.running and not self._stop_always_on:
            if self.config.get("record_mode") != "always_on":
                break
            try:
                proc = subprocess.Popen(
                    ["arecord", "-D", "default", "-f", "S16_LE", "-r", str(sample_rate),
                     "-c", "1", "-t", "raw"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except Exception as e:
                log.error(f"Errore mic always-on: {e}")
                time.sleep(1)
                continue

            ring = deque(maxlen=pre_frames)
            speech_frames = None
            sil_cnt = 0
            speaking = False
            post_cnt = 0
            speech_frames_cnt = 0
            total_frames_cnt = 0
            last_activity = time.time()

            try:
                while self.running and not self._stop_always_on:
                    if self.config.get("record_mode") != "always_on":
                        break
                    if self.is_processing or self.is_recording:
                        sil_cnt = 0
                        post_cnt = 0
                        speaking = False
                        speech_frames = None
                        speech_frames_cnt = 0
                        total_frames_cnt = 0
                        chunk = proc.stdout.read(frame_size)
                        continue

                    chunk = proc.stdout.read(frame_size)
                    if not chunk or len(chunk) < frame_size:
                        break

                    is_speech = vad.is_speech(chunk, sample_rate)
                    ring.append(chunk)
                    now = time.time()

                    if is_speech:
                        if not speaking:
                            speaking = True
                            speech_frames = list(ring)
                            speech_frames_cnt = 1
                            total_frames_cnt = len(ring)
                            log.info("Always-on: parlato rilevato")
                            self._update_status("Parlato rilevato...")
                        else:
                            speech_frames.append(chunk)
                            speech_frames_cnt += 1
                            total_frames_cnt += 1
                        sil_cnt = 0
                        post_cnt = 0
                        last_activity = now
                    elif speaking:
                        speech_frames.append(chunk)
                        total_frames_cnt += 1
                        post_cnt += 1
                        if post_cnt >= post_frames:
                            utterance = b''.join(speech_frames)
                            speaking = False
                            speech_frames = None
                            total_utt_frames = total_frames_cnt
                            utt_speech_frames = speech_frames_cnt
                            post_cnt = 0
                            speech_frames_cnt = 0
                            total_frames_cnt = 0
                            dur = total_utt_frames * frame_ms / 1000
                            min_dur = self.config.get("vad_min_utterance_ms", 300) / 1000
                            min_speech = self.config.get("vad_min_speech_frames", 3)
                            log.info(f"Always-on: utterance {dur:.1f}s, speech_frames={utt_speech_frames}/{total_utt_frames}, frames={total_utt_frames}")
                            if dur < min_dur or utt_speech_frames < min_speech:
                                log.info(f"Utterance scartata (dur={dur:.2f}s < {min_dur}s o speech_frames={utt_speech_frames} < {min_speech})")
                                continue
                            if len(utterance) > 4096:
                                ts = datetime.now().strftime("%H%M%S")
                                wav = TEMP_DIR / f"utt_{ts}.wav"
                                with wave.open(str(wav), 'wb') as wf:
                                    wf.setnchannels(1)
                                    wf.setsampwidth(2)
                                    wf.setframerate(sample_rate)
                                    wf.writeframes(utterance)
                                self._update_status("Trascrizione...")
                                self.is_processing = True
                                threading.Thread(target=self._process_audio_from, args=(wav,), daemon=True).start()
                    else:
                        if now - last_activity > 120:
                            self._update_status("In ascolto continuo...")
                            last_activity = now
                        sil_cnt += 1
            except Exception as e:
                log.error(f"Errore always-on loop: {e}")
            finally:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    pass

        log.info("Always-on loop terminato")
        self._update_status("Pronto")

    # ── Lifecycle ──

    def quit(self):
        log.info("Arresto Jarvis Agent...")
        self.running = False
        self._stop_always_on = True
        if self.is_recording:
            self._stop_recording()
        if self.tray:
            try:
                self.tray.setVisible(False)
            except Exception:
                pass
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass
        QApplication.quit()

    def run(self):
        self._apply_theme()
        self._apply_config()
        has_tray = self._start_tray()

        if not has_tray:
            self._notify("Jarvis Agent", "Avviato in modalità solo socket")
            self._notify_ui("Jarvis Agent", "Jarvis è pronto.")
            return QApplication.instance().exec() if QApplication.instance() else 0

        self._notify_ui("Jarvis Agent",
            "Jarvis è pronto. Premi il tasto assegnato o usa l'icona per parlare.")

        return QApplication.instance().exec()


class ChatWindow(QDialog):
    STYLE = """
        QListWidget#convList {
            border: none;
            border-radius: 6px;
            background: transparent;
            padding: 4px;
        }
        QListWidget#convList::item {
            border-radius: 6px;
            padding: 8px 12px;
            margin: 2px 0px;
        }
        QTextEdit#chatArea {
            border: none;
            border-radius: 8px;
            padding: 12px;
            font-size: 13px;
        }
        QTextEdit#chatArea:focus { border: none; }
        QPlainTextEdit#chatInput {
            border-radius: 12px;
            padding: 10px 14px;
            font-size: 13px;
            min-height: 22px;
            max-height: 120px;
        }
        QPushButton#sendBtn {
            border: none;
            border-radius: 10px;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: bold;
            min-height: 24px;
        }
        QPushButton#sendBtn:hover { }
        QPushButton#sendBtn:disabled { }
        QPushButton#newConvBtn {
            border: none;
            border-radius: 8px;
            padding: 8px;
            font-size: 13px;
            font-weight: bold;
        }
        QPushButton#delConvBtn {
            border: none;
            border-radius: 8px;
            padding: 4px 8px;
            font-size: 11px;
        }
        QScrollBar:vertical {
            background: transparent;
            width: 6px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            border-radius: 3px;
            min-height: 30px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
    """

    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.setWindowTitle("Jarvis - Chat")
        self.setMinimumSize(560, 480)
        self.resize(720, 620)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowCloseButtonHint)

        self.conversations = {}
        self.conv_ids = []
        self.current_conv = None
        self._new_conversation()

        self._build_ui()
        self._apply_styles()

    # ── Styles ──

    def _colors(self):
        theme = self.agent.config.get("_theme_mode", "system")
        app_q = QApplication.instance()
        pal = app_q.palette() if app_q else None
        if theme == "scuro":
            return {
                "bg": "#1a1c1f",
                "surface": "#222428",
                "border": "#3a3d41",
                "text": "#e0e0e0",
                "text_sec": "#9a9ea3",
                "accent": "#3daee9",
                "accent_hover": "#3498db",
                "user_bubble": "#3daee9",
                "user_text": "#ffffff",
                "assistant_bg": "#2a2d31",
                "assistant_text": "#e0e0e0",
                "conv_active": "#2d3035",
                "conv_hover": "#282b2f",
                "system_text": "#7a7e83",
                "scrollbar": "#4a4d51",
                "scrollbar_hover": "#5a5d61",
            }
        else:
            return {
                "bg": "#f5f5f5",
                "surface": "#ffffff",
                "border": "#e0e0e0",
                "text": "#1a1a1a",
                "text_sec": "#666666",
                "accent": "#0d6efd",
                "accent_hover": "#0b5ed7",
                "user_bubble": "#0d6efd",
                "user_text": "#ffffff",
                "assistant_bg": "#f0f0f0",
                "assistant_text": "#1a1a1a",
                "conv_active": "#e8f0fe",
                "conv_hover": "#f0f0f0",
                "system_text": "#999999",
                "scrollbar": "#c0c0c0",
                "scrollbar_hover": "#a0a0a0",
            }

    def _apply_styles(self):
        c = self._colors()
        style = self.STYLE + f"""
            QWidget#chatPanel {{ background: {c['bg']}; }}
            QWidget#sidebar {{ background: {c['surface']}; border-right: 1px solid {c['border']}; }}
            QLabel#headerLabel {{ color: {c['text']}; font-size: 14px; font-weight: bold; }}
            QListWidget#convList {{ color: {c['text']}; }}
            QListWidget#convList::item {{ color: {c['text']}; }}
            QListWidget#convList::item:selected {{ background: {c['conv_active']}; color: {c['text']}; }}
            QListWidget#convList::item:hover {{ background: {c['conv_hover']}; }}
            QTextEdit#chatArea {{ background: {c['bg']}; color: {c['text']}; }}
            QPlainTextEdit#chatInput {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; selection-background-color: {c['accent']}; }}
            QPlainTextEdit#chatInput:focus {{ border-color: {c['accent']}; }}
            QPushButton#sendBtn {{ background: {c['accent']}; color: white; }}
            QPushButton#sendBtn:hover {{ background: {c['accent_hover']}; }}
            QPushButton#sendBtn:disabled {{ background: {c['border']}; color: {c['text_sec']}; }}
            QPushButton#newConvBtn {{ background: transparent; color: {c['accent']}; }}
            QPushButton#newConvBtn:hover {{ background: {c['conv_hover']}; }}
            QPushButton#delConvBtn {{ background: transparent; color: {c['text_sec']}; }}
            QPushButton#delConvBtn:hover {{ background: #e81123; color: white; }}
            QScrollBar::handle:vertical {{ background: {c['scrollbar']}; }}
            QScrollBar::handle:vertical:hover {{ background: {c['scrollbar_hover']}; }}
        """
        self.setStyleSheet(style)

    # ── Conversation Management ──

    def _new_conversation(self, title=None):
        cid = str(uuid.uuid4())[:8]
        self.conversations[cid] = {"title": title or "Nuova conversazione", "messages": []}
        self.conv_ids.append(cid)
        self.current_conv = cid
        return cid

    def _delete_conversation(self, cid):
        if cid not in self.conversations:
            return
        idx = self.conv_ids.index(cid)
        del self.conversations[cid]
        self.conv_ids.remove(cid)
        if self.conv_ids:
            self.current_conv = self.conv_ids[min(idx, len(self.conv_ids) - 1)]
        else:
            self._new_conversation()
            self.current_conv = self.conv_ids[0]
        self._refresh()
        self._rebuild_messages()

    def _conv_title(self, cid):
        msgs = self.conversations[cid]["messages"]
        for m in msgs:
            if m["role"] == "user":
                t = m["text"][:40]
                return t + ("..." if len(m["text"]) > 40 else "")
        return "Nuova conversazione"

    # ── UI Build ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        # ── Left sidebar ──
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(300)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(8, 8, 8, 8)
        sl.setSpacing(6)

        header_row = QHBoxLayout()
        hl = QLabel("Conversazioni")
        hl.setObjectName("headerLabel")
        header_row.addWidget(hl)
        header_row.addStretch()
        new_btn = QPushButton("+")
        new_btn.setObjectName("newConvBtn")
        new_btn.setFixedSize(28, 28)
        new_btn.setToolTip("Nuova conversazione")
        new_btn.clicked.connect(self._on_new_conv)
        header_row.addWidget(new_btn)
        sl.addLayout(header_row)

        self.conv_list = QListWidget()
        self.conv_list.setObjectName("convList")
        self.conv_list.setSpacing(1)
        self.conv_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.conv_list.currentRowChanged.connect(self._on_conv_switch)
        self.conv_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.conv_list.customContextMenuRequested.connect(self._on_conv_context)
        sl.addWidget(self.conv_list)

        splitter.addWidget(sidebar)

        # ── Right panel (chat) ──
        chat_panel = QWidget()
        chat_panel.setObjectName("chatPanel")
        cl = QVBoxLayout(chat_panel)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # Header
        chat_header = QWidget()
        chat_header.setObjectName("chatHeader")
        chl = QHBoxLayout(chat_header)
        chl.setContentsMargins(16, 8, 16, 8)
        self.conv_title_label = QLabel("Nuova conversazione")
        self.conv_title_label.setObjectName("headerLabel")
        chl.addWidget(self.conv_title_label)
        chl.addStretch()
        del_btn = QPushButton("🗑")
        del_btn.setObjectName("delConvBtn")
        del_btn.setToolTip("Elimina conversazione")
        del_btn.clicked.connect(self._on_delete_conv)
        chl.addWidget(del_btn)
        cl.addWidget(chat_header)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {self._colors()['border']};")
        cl.addWidget(sep)

        # Message area
        self.chat = QTextEdit()
        self.chat.setObjectName("chatArea")
        self.chat.setReadOnly(True)
        self.chat.setFrameShape(QFrame.Shape.NoFrame)
        self.chat.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chat.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat.viewport().setAutoFillBackground(False)
        cl.addWidget(self.chat, stretch=1)

        # Input area
        input_container = QWidget()
        icl = QVBoxLayout(input_container)
        icl.setContentsMargins(12, 8, 12, 12)
        icl.setSpacing(6)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.input = QPlainTextEdit()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText("Scrivi un messaggio per Jarvis...")
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setTabChangesFocus(False)
        # Ctrl+Enter or Enter to send (Enter in plain text = newline normally, we intercept via shortcut)
        self.input.installEventFilter(self)
        input_row.addWidget(self.input)

        self.send_btn = QPushButton("Invia")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setFixedWidth(72)
        self.send_btn.clicked.connect(self._send)
        input_row.addWidget(self.send_btn)

        icl.addLayout(input_row)
        cl.addWidget(input_container)

        splitter.addWidget(chat_panel)
        splitter.setSizes([200, 520])
        root.addWidget(splitter)

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self._send()
                return True
            if event.key() == Qt.Key.Key_Return and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                event.accept()
                tc = self.input.textCursor()
                tc.insertText("\n")
                return True
        return super().eventFilter(obj, event)

    # ── Conv list events ──

    def _on_new_conv(self):
        self._new_conversation()
        self._refresh()
        self._rebuild_messages()

    def _on_delete_conv(self):
        if len(self.conv_ids) <= 1:
            return
        self._delete_conversation(self.current_conv)

    def _on_conv_switch(self, row):
        if 0 <= row < len(self.conv_ids):
            self.current_conv = self.conv_ids[row]
            self._rebuild_messages()

    def _on_conv_context(self, pos):
        item = self.conv_list.itemAt(pos)
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu()
        del_a = menu.addAction("Elimina conversazione")
        ren_a = menu.addAction("Rinomina")
        a = menu.exec(self.conv_list.mapToGlobal(pos))
        if a == del_a:
            self._delete_conversation(cid)
        elif a == ren_a:
            txt, ok = QInputDialog.getText(self, "Rinomina", "Nuovo nome:", text=self.conversations[cid]["title"])
            if ok and txt.strip():
                self.conversations[cid]["title"] = txt.strip()
                self._refresh()

    def _refresh(self):
        self.conv_list.blockSignals(True)
        self.conv_list.clear()
        for cid in self.conv_ids:
            item = QListWidgetItem(self._conv_title(cid))
            item.setData(Qt.ItemDataRole.UserRole, cid)
            self.conv_list.addItem(item)
        if self.current_conv in self.conv_ids:
            idx = self.conv_ids.index(self.current_conv)
            self.conv_list.setCurrentRow(idx)
        self.conv_list.blockSignals(False)
        if self.current_conv in self.conversations:
            self.conv_title_label.setText(self.conversations[self.current_conv]["title"])

    def _rebuild_messages(self):
        self.chat.clear()
        if self.current_conv not in self.conversations:
            return
        c = self._colors()
        theme = self.agent.config.get("_theme_mode", "system")
        is_dark = theme == "scuro"

        user_bg = c["user_bubble"]
        user_fg = c["user_text"]
        asst_bg = c["assistant_bg"]
        asst_fg = c["assistant_text"]
        sys_fg = c["system_text"]

        html_parts = []
        for m in self.conversations[self.current_conv]["messages"]:
            if m["role"] == "user":
                html_parts.append(
                    f'<div style="text-align:right; margin:6px 0 2px 0;">'
                    f'<span style="display:inline-block; background:{user_bg}; color:{user_fg}; '
                    f'border-radius:12px 12px 4px 12px; padding:8px 14px; '
                    f'max-width:75%; font-size:13px; line-height:1.4;">'
                    f'{self._escape_html(m["text"])}</span></div>'
                )
            elif m["role"] == "assistant":
                html_parts.append(
                    f'<div style="text-align:left; margin:6px 0 2px 0;">'
                    f'<div style="display:inline-block; background:{asst_bg}; color:{asst_fg}; '
                    f'border-radius:12px 12px 12px 4px; padding:8px 14px; '
                    f'max-width:85%; font-size:13px; line-height:1.5;">'
                    f'<b style="color:{c["accent"]};">Jarvis</b><br>{self._format_text(m["text"])}'
                    f'</div></div>'
                )
            else:
                html_parts.append(
                    f'<div style="text-align:center; margin:6px 0; '
                    f'color:{sys_fg}; font-style:italic; font-size:12px;">'
                    f'{self._escape_html(m["text"])}</div>'
                )

        self.chat.setHtml(
            f'<html><body style="margin:0; padding:4px 8px;">{"".join(html_parts)}</body></html>'
        )
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def _escape_html(self, text):
        return (text.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

    def _format_text(self, text):
        escaped = self._escape_html(text)
        # Basic markdown-style: **bold**, *italic*, `code`, ```code block```
        import re as _re
        # Code blocks first
        escaped = _re.sub(
            r'```(\w*)\n(.*?)```',
            lambda m: f'<pre style="background:#1e1e1e; color:#d4d4d4; border-radius:6px; '
                      f'padding:10px; margin:6px 0; font-size:12px; line-height:1.3; '
                      f'overflow-x:auto;"><code>{self._escape_html(m.group(2))}</code></pre>',
            escaped, flags=_re.DOTALL
        )
        # Inline code
        escaped = _re.sub(
            r'`([^`]+)`',
            r'<code style="background:#e8e8e8; color:#d63384; border-radius:3px; '
            r'padding:1px 4px; font-size:12px;">\1</code>',
            escaped
        )
        # **bold**
        escaped = _re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
        # *italic*
        escaped = _re.sub(r'\*(.+?)\*', r'<i>\1</i>', escaped)
        # Line breaks
        escaped = escaped.replace("\n", "<br>")
        return escaped

    # ── Send / Receive ──

    def _send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._add_msg("user", text)
        threading.Thread(target=self._process, args=(text,), daemon=True).start()

    def _process(self, text):
        response = self.agent._query_jarvis(text)
        QTimer.singleShot(0, lambda: self._process_done(response))

    def _process_done(self, response):
        self.input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.input.setFocus()
        if response:
            self.agent.last_response = response
            self._add_msg("assistant", response)
            if self.agent.config["tts_enabled"] and HAVE_TTS:
                self.agent._speak(response)
        else:
            self._add_msg("system", "Nessuna risposta da Jarvis.")

    def _add_msg(self, role, text):
        if self.current_conv not in self.conversations:
            self._new_conversation()
        msgs = self.conversations[self.current_conv]["messages"]
        msgs.append({"role": role, "text": text, "time": time.time()})
        auto_title = False
        if role == "user" and sum(1 for m in msgs if m["role"] == "user") == 1:
            auto_title = True
        self._rebuild_messages()
        self._refresh()
        if auto_title:
            self.conversations[self.current_conv]["title"] = self._conv_title(self.current_conv)
            self._refresh()

    def add_message(self, role, text):
        QTimer.singleShot(0, lambda: self._add_msg(role, text))


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    agent = JarvisAgent()
    try:
        sys.exit(agent.run())
    except KeyboardInterrupt:
        agent.quit()
        sys.exit(0)


if __name__ == "__main__":
    main()
