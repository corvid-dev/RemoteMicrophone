"""
Mic Sender UI - runs on your LOCAL PC.
Pick a microphone, enter the gaming PC's IP address, then start streaming.

pip install sounddevice numpy
"""

import json
import os
import queue
import random
import socket
import struct
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np
import sounddevice as sd

APP_NAME = "RemoteMicrophone Sender"
VERSION = "1.0.0"

DEFAULT_PORT = 5005
SAMPLE_RATE = 48000
CHANNELS = 1
BLOCKSIZE = 480
AUDIO_BYTES = BLOCKSIZE * CHANNELS * 2
PACKET_HEADER = struct.Struct("!4sII")
PACKET_MAGIC = b"MIC1"
SEND_QUEUE_SIZE = 4

SETTINGS_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RemoteMicrophone"
)
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "sender_settings.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_settings(data):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


class SenderApp:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} v{VERSION}")
        root.geometry("410x285")
        root.resizable(False, False)

        self.stream = None
        self.sock = None
        self.running = False
        self.send_queue = None
        self.stop_event = None
        self.send_thread = None
        self.gain_factor = 1.0

        pad = {"padx": 10, "pady": 6}

        ttk.Label(root, text="Microphone:").grid(row=0, column=0, sticky="w", **pad)
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(
            root, textvariable=self.mic_var, width=35, state="readonly"
        )
        self.mic_combo.grid(row=1, column=0, columnspan=2, sticky="we", **pad)

        ttk.Label(root, text="Gaming PC IP address:").grid(
            row=2, column=0, sticky="w", **pad
        )
        address_frame = ttk.Frame(root)
        address_frame.grid(row=3, column=0, columnspan=2, sticky="we", padx=10, pady=6)
        address_frame.columnconfigure(0, weight=1)

        self.ip_var = tk.StringVar(value="192.168.1.")
        self.ip_entry = ttk.Entry(address_frame, textvariable=self.ip_var)
        self.ip_entry.grid(row=0, column=0, sticky="we")

        ttk.Label(address_frame, text="Port:").grid(row=0, column=1, padx=(12, 4))
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.port_entry = ttk.Entry(address_frame, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=2)

        gain_frame = ttk.Frame(root)
        gain_frame.grid(row=4, column=0, columnspan=2, sticky="we", padx=10, pady=(2, 4))
        ttk.Label(gain_frame, text="Microphone gain:").pack(side="left")
        self.gain_var = tk.IntVar(value=100)
        self.gain_scale = ttk.Scale(
            gain_frame,
            from_=0,
            to=300,
            orient="horizontal",
            command=self._on_gain_changed,
        )
        self.gain_scale.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.gain_value_var = tk.StringVar(value="100%")
        ttk.Label(gain_frame, textvariable=self.gain_value_var, width=5).pack(side="right")

        self.toggle_btn = ttk.Button(root, text="Start Streaming", command=self.toggle)
        self.toggle_btn.grid(row=5, column=0, columnspan=2, sticky="we", **pad)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(root, textvariable=self.status_var, foreground="gray").grid(
            row=6, column=0, columnspan=2, sticky="w", padx=10
        )

        self.refresh_devices()
        self.apply_saved_settings()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def apply_saved_settings(self):
        settings = load_settings()
        if settings.get("ip"):
            self.ip_var.set(settings["ip"])
        if settings.get("port"):
            self.port_var.set(str(settings["port"]))
        gain_percent = settings.get("gain_percent", 100)
        try:
            gain_percent = max(0, min(300, int(gain_percent)))
        except (TypeError, ValueError):
            gain_percent = 100
        self.gain_scale.set(gain_percent)
        self._set_gain(gain_percent)

        saved_mic = settings.get("mic_name")
        names = list(self.mic_combo["values"])
        if saved_mic in names:
            self.mic_combo.current(names.index(saved_mic))

    def current_settings(self):
        sel = self.mic_combo.current()
        mic_name = self.mic_devices[sel][1] if sel >= 0 else None
        return {
            "ip": self.ip_var.get().strip(),
            "port": self.port_var.get().strip(),
            "mic_name": mic_name,
            "gain_percent": int(round(self.gain_factor * 100)),
        }

    def _set_gain(self, percent):
        percent = max(0, min(300, int(round(float(percent) / 5.0) * 5)))
        self.gain_factor = percent / 100.0
        self.gain_var.set(percent)
        self.gain_value_var.set(f"{percent}%")
        if abs(float(self.gain_scale.get()) - percent) > 0.01:
            self.gain_scale.set(percent)

    def _on_gain_changed(self, value):
        self._set_gain(value)

    def on_close(self):
        save_settings(self.current_settings())
        self.stop()
        self.root.destroy()

    def refresh_devices(self):
        try:
            devices = sd.query_devices()
        except Exception as exc:
            self.mic_devices = []
            messagebox.showerror("Audio Error", f"Could not list microphones:\n{exc}")
            return

        self.mic_devices = [
            (i, d["name"])
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
        names = [name for _, name in self.mic_devices]
        self.mic_combo["values"] = names

        if names:
            default_idx = sd.default.device[0]
            for pos, (dev_idx, _) in enumerate(self.mic_devices):
                if dev_idx == default_idx:
                    self.mic_combo.current(pos)
                    break
            else:
                self.mic_combo.current(0)

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        ip = self.ip_var.get().strip()
        if not ip:
            messagebox.showerror("Error", "Enter the gaming PC's IP address.")
            return

        try:
            socket.inet_aton(ip)
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except OSError:
            messagebox.showerror("Error", "Enter a valid IPv4 address.")
            return
        except ValueError:
            messagebox.showerror("Error", "Port must be between 1 and 65535.")
            return

        sel = self.mic_combo.current()
        if sel < 0:
            messagebox.showerror("Error", "Select a microphone.")
            return
        device_index = self.mic_devices[sel][0]
        max_input_channels = int(sd.query_devices(device_index)["max_input_channels"])
        # Wire format is always mono: capture in stereo and downmix if the
        # device supports it, otherwise capture mono directly.
        input_channels = 2 if max_input_channels >= 2 else 1

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.send_queue = queue.Queue(maxsize=SEND_QUEUE_SIZE)
        self.stop_event = threading.Event()
        session_id = random.getrandbits(32)

        def audio_callback(indata, frames, time_info, status):
            if frames != BLOCKSIZE or not self.running:
                return

            if input_channels == 2:
                mono = np.mean(indata.astype(np.float32), axis=1)
            else:
                mono = indata[:, 0].astype(np.float32)

            gain = self.gain_factor
            if gain != 1.0:
                mono *= gain
            audio = np.clip(mono, -32768, 32767).astype(np.int16).tobytes()

            try:
                self.send_queue.put_nowait(audio)
            except queue.Full:
                try:
                    self.send_queue.get_nowait()
                    self.send_queue.put_nowait(audio)
                except queue.Empty:
                    pass

        def send_loop():
            sequence = 0
            destination = (ip, port)
            while not self.stop_event.is_set():
                try:
                    audio = self.send_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if len(audio) != AUDIO_BYTES:
                    continue

                packet = PACKET_HEADER.pack(PACKET_MAGIC, session_id, sequence) + audio
                try:
                    self.sock.sendto(packet, destination)
                except OSError:
                    if not self.stop_event.is_set():
                        self.root.after(0, self.status_var.set, "Network send error")
                sequence = (sequence + 1) & 0xFFFFFFFF

        try:
            self.running = True
            self.send_thread = threading.Thread(target=send_loop, daemon=True)
            self.send_thread.start()
            self.stream = sd.InputStream(
                device=device_index,
                samplerate=SAMPLE_RATE,
                channels=input_channels,
                dtype="int16",
                blocksize=BLOCKSIZE,
                callback=audio_callback,
            )
            self.stream.start()
        except Exception as exc:
            self.running = False
            if self.stop_event:
                self.stop_event.set()
            if self.sock:
                self.sock.close()
            self.sock = None
            messagebox.showerror("Error", f"Could not start mic stream:\n{exc}")
            return

        self.toggle_btn.config(text="Stop Streaming")
        mode = "stereo downmix" if input_channels == 2 else "mono input"
        self.status_var.set(f"Streaming to {ip}:{port} ({mode})")
        save_settings(self.current_settings())

    def stop(self):
        if not self.running and not self.stream and not self.sock:
            return

        self.running = False
        if self.stop_event:
            self.stop_event.set()

        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

        if self.send_thread and self.send_thread.is_alive():
            self.send_thread.join(timeout=0.5)

        self.send_thread = None
        self.send_queue = None
        self.stop_event = None
        self.toggle_btn.config(text="Start Streaming")
        self.status_var.set("Idle")


if __name__ == "__main__":
    root = tk.Tk()
    SenderApp(root)
    root.mainloop()
