"""
Mic Receiver UI - runs on the GAMING PC.
Send audio to CABLE Input, then select CABLE Output in Discord or a game.

Requires VB-CABLE installed.
pip install sounddevice numpy
"""

import json
import os
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np
import sounddevice as sd

APP_NAME = "RemoteMicrophone Receiver"
VERSION = "0.2.0"

DEFAULT_PORT = 5005
SAMPLE_RATE = 48000
CHANNELS = 1
BLOCKSIZE = 480
AUDIO_BYTES = BLOCKSIZE * CHANNELS * 2
PACKET_HEADER = struct.Struct("!4sII")
PACKET_MAGIC = b"MIC1"
TARGET_BUFFER_PACKETS = 5
TRIM_BUFFER_PACKETS = 12
RING_CAPACITY = 64
SILENCE_RMS_THRESHOLD = 350.0
TRIM_COOLDOWN_SECONDS = 2.0
UDP_RECEIVE_BUFFER_BYTES = 1024 * 1024
SENDER_TIMEOUT_SECONDS = 1.0

SETTINGS_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RemoteMicrophone"
)
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "receiver_settings.json")


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



class PacketRingBuffer:
    """Fixed-size sequence-aware packet ring for 32-bit UDP sequence numbers."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.sequences = [None] * capacity
        self.packets = [None] * capacity
        self.count = 0

    def clear(self):
        self.sequences[:] = [None] * self.capacity
        self.packets[:] = [None] * self.capacity
        self.count = 0

    @staticmethod
    def forward_distance(sequence, reference):
        return (sequence - reference) & 0xFFFFFFFF

    def insert(self, sequence, packet, expected_sequence):
        distance = self.forward_distance(sequence, expected_sequence)

        # Values in the upper half of the sequence space are behind the reader.
        if distance >= 0x80000000:
            return "stale"
        if distance >= self.capacity:
            return "too_far"

        index = sequence % self.capacity
        if self.sequences[index] != sequence:
            if self.sequences[index] is None:
                self.count += 1
            self.sequences[index] = sequence
            self.packets[index] = packet
        return "stored"

    def pop(self, sequence):
        index = sequence % self.capacity
        if self.sequences[index] != sequence:
            return None

        packet = self.packets[index]
        self.sequences[index] = None
        self.packets[index] = None
        self.count -= 1
        return packet

    def discard(self, sequence):
        self.pop(sequence)

    def peek(self, sequence):
        index = sequence % self.capacity
        if self.sequences[index] != sequence:
            return None
        return self.packets[index]

    def contiguous_count(self, start_sequence):
        total = 0
        for offset in range(self.capacity):
            sequence = (start_sequence + offset) & 0xFFFFFFFF
            if self.sequences[sequence % self.capacity] != sequence:
                break
            total += 1
        return total

    def earliest_sequence(self, reference):
        earliest = None
        earliest_distance = self.capacity + 1
        for sequence in self.sequences:
            if sequence is None:
                continue
            distance = self.forward_distance(sequence, reference)
            if distance < 0x80000000 and distance < earliest_distance:
                earliest = sequence
                earliest_distance = distance
        return earliest


def get_local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class ReceiverApp:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} v{VERSION}")
        root.geometry("410x270")
        root.resizable(False, False)

        self.stream = None
        self.sock = None
        self.running = False
        self.stop_event = None
        self.receive_thread = None
        self.last_sender_ip = None
        self.last_packet_time = 0.0
        self._poll_job = None

        self.buffer_lock = threading.Lock()
        self.packet_buffer = PacketRingBuffer(RING_CAPACITY)
        self.session_id = None
        self.expected_sequence = None
        self.playback_started = False
        self.last_trim_time = 0.0
        self.underrun_count = 0
        self.trim_count = 0

        pad = {"padx": 10, "pady": 6}

        ttk.Label(root, text="This PC's IP (give to sender):").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad
        )
        self.my_ip = get_local_ip()
        ip_frame = ttk.Frame(root)
        ip_frame.grid(row=1, column=0, columnspan=2, sticky="we", padx=10)
        self.ip_entry = ttk.Entry(ip_frame, width=28)
        self.ip_entry.insert(0, self.my_ip)
        self.ip_entry.config(state="readonly")
        self.ip_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(ip_frame, text="Copy", width=6, command=self.copy_ip).pack(
            side="left", padx=(6, 0)
        )

        ttk.Label(root, text="Send audio into:").grid(
            row=2, column=0, sticky="w", **pad
        )
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            root, textvariable=self.device_var, width=35, state="readonly"
        )
        self.device_combo.grid(row=3, column=0, columnspan=2, sticky="we", **pad)

        ttk.Label(root, text="Listen port:").grid(row=4, column=0, sticky="w", **pad)
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(root, textvariable=self.port_var, width=8).grid(
            row=4, column=1, sticky="w"
        )

        self.toggle_btn = ttk.Button(root, text="Start Listening", command=self.toggle)
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
        if settings.get("port"):
            self.port_var.set(str(settings["port"]))
        saved_device = settings.get("device_name")
        names = list(self.device_combo["values"])
        if saved_device in names:
            self.device_combo.current(names.index(saved_device))

    def current_settings(self):
        sel = self.device_combo.current()
        device_name = self.out_devices[sel][1] if sel >= 0 else None
        return {
            "port": self.port_var.get().strip(),
            "device_name": device_name,
        }

    def on_close(self):
        save_settings(self.current_settings())
        self.stop()
        self.root.destroy()

    def copy_ip(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.my_ip)

    def refresh_devices(self):
        try:
            devices = sd.query_devices()
        except Exception as exc:
            self.out_devices = []
            messagebox.showerror("Audio Error", f"Could not list output devices:\n{exc}")
            return

        self.out_devices = [
            (i, d["name"])
            for i, d in enumerate(devices)
            if d["max_output_channels"] > 0
        ]
        names = [name for _, name in self.out_devices]
        self.device_combo["values"] = names

        for pos, (_, name) in enumerate(self.out_devices):
            if "CABLE Input" in name:
                self.device_combo.current(pos)
                break
        else:
            if names:
                self.device_combo.current(0)

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Port must be between 1 and 65535.")
            return

        sel = self.device_combo.current()
        if sel < 0:
            messagebox.showerror("Error", "Select an output device.")
            return
        device_index = self.out_devices[sel][0]
        max_output_channels = int(sd.query_devices(device_index)["max_output_channels"])
        # Wire format is always mono: upmix to stereo whenever the output
        # device supports it, otherwise play back mono directly.
        output_channels = 2 if max_output_channels >= 2 else 1

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RECEIVE_BUFFER_BYTES
            )
            self.sock.bind(("0.0.0.0", port))
            self.sock.settimeout(0.2)
        except OSError as exc:
            self.sock.close()
            self.sock = None
            messagebox.showerror("Error", f"Could not bind port {port}:\n{exc}")
            return

        self.stop_event = threading.Event()
        with self.buffer_lock:
            self.packet_buffer.clear()
            self.session_id = None
            self.expected_sequence = None
            self.playback_started = False
            self.last_trim_time = 0.0
            self.underrun_count = 0
            self.trim_count = 0
            self.last_packet_time = 0.0

        def receive_loop():
            while not self.stop_event.is_set():
                try:
                    packet, addr = self.sock.recvfrom(PACKET_HEADER.size + AUDIO_BYTES)
                except socket.timeout:
                    continue
                except OSError:
                    break

                if len(packet) != PACKET_HEADER.size + AUDIO_BYTES:
                    continue

                magic, session_id, sequence = PACKET_HEADER.unpack_from(packet)
                if magic != PACKET_MAGIC:
                    continue

                audio = packet[PACKET_HEADER.size:]
                self.last_sender_ip = addr[0]
                self.last_packet_time = time.monotonic()

                with self.buffer_lock:
                    if session_id != self.session_id:
                        self.packet_buffer.clear()
                        self.session_id = session_id
                        self.expected_sequence = sequence
                        self.playback_started = False

                    result = self.packet_buffer.insert(
                        sequence, audio, self.expected_sequence
                    )

                    # A large forward jump means the old stream position is no
                    # longer useful. Start a fresh ring at the newest packet.
                    if result == "too_far":
                        self.packet_buffer.clear()
                        self.expected_sequence = sequence
                        self.playback_started = False
                        self.packet_buffer.insert(
                            sequence, audio, self.expected_sequence
                        )

        def audio_callback(outdata, frames, time_info, status):
            outdata.fill(0)
            if frames != BLOCKSIZE or not self.running:
                return

            audio = None
            with self.buffer_lock:
                if not self.playback_started:
                    earliest = self.packet_buffer.earliest_sequence(
                        self.expected_sequence
                    )
                    if earliest is None:
                        return

                    self.expected_sequence = earliest
                    if (
                        self.packet_buffer.contiguous_count(self.expected_sequence)
                        < TARGET_BUFFER_PACKETS
                    ):
                        return
                    self.playback_started = True

                buffered = self.packet_buffer.contiguous_count(
                    self.expected_sequence
                )

                # Correct accumulated clock drift only during a quiet packet.
                # v0.1.7 discarded a full 10 ms packet whenever the queue exceeded
                # six packets, which caused audible time jumps during speech.
                now = time.monotonic()
                if (
                    buffered > TRIM_BUFFER_PACKETS
                    and now - self.last_trim_time >= TRIM_COOLDOWN_SECONDS
                ):
                    candidate = self.packet_buffer.peek(self.expected_sequence)
                    if candidate is not None:
                        candidate_samples = np.frombuffer(candidate, dtype=np.int16)
                        candidate_float = candidate_samples.astype(np.float32)
                        rms = float(np.sqrt(np.mean(candidate_float * candidate_float)))
                        if rms <= SILENCE_RMS_THRESHOLD:
                            self.packet_buffer.discard(self.expected_sequence)
                            self.expected_sequence = (
                                self.expected_sequence + 1
                            ) & 0xFFFFFFFF
                            self.last_trim_time = now
                            self.trim_count += 1

                audio = self.packet_buffer.pop(self.expected_sequence)
                if audio is None:
                    self.underrun_count += 1
                    # Preserve future packets, pause, and rebuild the target
                    # cushion. The next start skips any permanently missing gap.
                    self.playback_started = False
                    return

                self.expected_sequence = (
                    self.expected_sequence + 1
                ) & 0xFFFFFFFF

            if audio is not None:
                mono = np.frombuffer(audio, dtype=np.int16)
                if output_channels == 2:
                    outdata[:, 0] = mono
                    outdata[:, 1] = mono
                else:
                    outdata[:, 0] = mono

        try:
            self.running = True
            self.receive_thread = threading.Thread(target=receive_loop, daemon=True)
            self.receive_thread.start()
            self.stream = sd.OutputStream(
                device=device_index,
                samplerate=SAMPLE_RATE,
                channels=output_channels,
                dtype="int16",
                blocksize=BLOCKSIZE,
                callback=audio_callback,
            )
            self.stream.start()
        except Exception as exc:
            self.running = False
            self.stop_event.set()
            self.sock.close()
            self.sock = None
            messagebox.showerror("Error", f"Could not start output stream:\n{exc}")
            return

        self.toggle_btn.config(text="Stop Listening")
        self.device_combo.config(state="disabled")
        mode = "stereo output" if output_channels == 2 else "mono output"
        self.status_var.set(
            f"Listening on port {port} ({mode}) — waiting for sender..."
        )
        save_settings(self.current_settings())
        self._poll_status()

    def _poll_status(self):
        if not self.running:
            return

        port = self.port_var.get()
        sender_active = (
            self.last_sender_ip is not None
            and self.last_packet_time > 0.0
            and time.monotonic() - self.last_packet_time <= SENDER_TIMEOUT_SECONDS
        )

        if sender_active:
            with self.buffer_lock:
                buffered = self.packet_buffer.contiguous_count(
                    self.expected_sequence
                )
            self.status_var.set(
                f"Receiving from {self.last_sender_ip} "
                f"(buffer {buffered}, underruns {self.underrun_count}, "
                f"trims {self.trim_count})"
            )
        else:
            self.last_sender_ip = None
            self.status_var.set(f"Listening on port {port} — waiting for sender...")

        self._poll_job = self.root.after(500, self._poll_status)

    def stop(self):
        if not self.running and not self.stream and not self.sock:
            return

        self.running = False
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
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

        if self.receive_thread and self.receive_thread.is_alive():
            self.receive_thread.join(timeout=0.5)

        self.receive_thread = None
        self.stop_event = None
        self.last_sender_ip = None
        self.last_packet_time = 0.0
        with self.buffer_lock:
            self.packet_buffer.clear()
            self.session_id = None
            self.expected_sequence = None
            self.playback_started = False
            self.last_trim_time = 0.0
            self.underrun_count = 0
            self.trim_count = 0

        self.toggle_btn.config(text="Start Listening")
        self.device_combo.config(state="readonly")
        self.status_var.set("Idle")


if __name__ == "__main__":
    root = tk.Tk()
    ReceiverApp(root)
    root.mainloop()
