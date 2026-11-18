import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import queue, threading, time, wave, struct
from pathlib import Path
import numpy as np
import sounddevice as sd
import mido

APP_NAME = "Hummify"
SAMPLE_RATE = 44100
FRAME_SIZE = 2048
HOP_SIZE = 1024

def freq_to_note(freq):
    if freq <= 0:
        return None, None, None
    midi = int(round(69 + 12 * np.log2(freq / 440.0)))
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return names[midi % 12] + str(midi // 12 - 1), midi, 440.0 * (2 ** ((midi - 69) / 12))

def yin_pitch(x, sr, threshold=0.15):
    """Compact YIN-style estimator. Returns (Hz, confidence)."""
    x = np.asarray(x, dtype=np.float32)
    if len(x) < 512:
        return 0.0, 0.0
    x = x - np.mean(x)
    rms = float(np.sqrt(np.mean(x * x)))
    if rms < 0.008:
        return 0.0, 0.0
    # Restrict search to roughly 65–1000 Hz (human humming range).
    min_tau = max(2, int(sr / 1000))
    max_tau = min(len(x) // 2, int(sr / 65))
    if max_tau <= min_tau:
        return 0.0, 0.0
    # Difference function; vectorized over lags.
    d = np.empty(max_tau + 1, dtype=np.float32)
    d[0] = 0
    for tau in range(1, max_tau + 1):
        delta = x[:-tau] - x[tau:]
        d[tau] = np.dot(delta, delta)
    cmnd = np.ones_like(d)
    running = 0.0
    for tau in range(1, max_tau + 1):
        running += float(d[tau])
        cmnd[tau] = d[tau] * tau / running if running > 0 else 1.0
    candidates = np.where(cmnd[min_tau:max_tau + 1] < threshold)[0]
    if len(candidates):
        tau = int(candidates[0] + min_tau)
        while tau + 1 <= max_tau and cmnd[tau + 1] < cmnd[tau]:
            tau += 1
    else:
        tau = int(np.argmin(cmnd[min_tau:max_tau + 1]) + min_tau)
        if cmnd[tau] > 0.35:
            return 0.0, max(0.0, 1.0 - float(cmnd[tau]))
    # Parabolic interpolation improves frequency precision.
    if 1 < tau < max_tau:
        a, b, c = float(cmnd[tau-1]), float(cmnd[tau]), float(cmnd[tau+1])
        denom = a - 2*b + c
        if abs(denom) > 1e-9:
            tau += 0.5 * (a - c) / denom
    confidence = max(0.0, min(1.0, 1.0 - float(cmnd[int(round(tau))])))
    return (sr / tau if tau > 0 else 0.0), confidence

class Hummify:
    def __init__(self, root):
        self.root = root
        self.root.title("Hummify — Hum to Notes")
        self.root.geometry("760x570")
        self.root.minsize(650, 500)
        self.root.configure(bg="#11151c")
        self.audio_q = queue.Queue(maxsize=32)
        self.stream = None
        self.running = False
        self.recording = False
        self.audio_chunks = []
        self.events = []
        self.current_note = None
        self.current_start = None
        self.last_seen = 0.0
        self.last_pitch = 0
        self.smooth = []
        self.device_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready — select a microphone and press Start")
        self.note_var = tk.StringVar(value="—")
        self.freq_var = tk.StringVar(value="Frequency: —")
        self.conf_var = tk.StringVar(value="Pitch confidence: —")
        self._build_ui()
        self._load_devices()
        self.root.after(30, self._poll_audio)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground="#202733", background="#202733", foreground="white")
        outer = tk.Frame(self.root, bg="#11151c", padx=22, pady=18)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(outer, bg="#11151c")
        header.pack(fill="x")
        tk.Label(header, text="HUMMIFY", bg="#11151c", fg="#f4f7fb",
                 font=("Segoe UI", 22, "bold")).pack(side="left")
        tk.Label(header, text="LOCAL MELODY TRANSCRIPTION", bg="#11151c", fg="#7f91a8",
                 font=("Segoe UI", 9, "bold")).pack(side="right", pady=10)

        tk.Label(outer, text="Microphone input", bg="#11151c", fg="#aab6c5",
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(24, 6))
        row = tk.Frame(outer, bg="#11151c")
        row.pack(fill="x")
        self.device_box = ttk.Combobox(row, textvariable=self.device_var, state="readonly")
        self.device_box.pack(side="left", fill="x", expand=True, ipady=5)
        self.refresh_btn = tk.Button(row, text="Refresh", command=self._load_devices,
                                     bg="#263243", fg="white", relief="flat", padx=14, pady=8)
        self.refresh_btn.pack(side="left", padx=(8, 0))

        pitch = tk.Frame(outer, bg="#1b2430", padx=20, pady=18)
        pitch.pack(fill="x", pady=22)
        tk.Label(pitch, text="DETECTED NOTE", bg="#1b2430", fg="#91a2b7",
                 font=("Segoe UI", 9, "bold")).pack()
        tk.Label(pitch, textvariable=self.note_var, bg="#1b2430", fg="#63e6be",
                 font=("Segoe UI", 48, "bold")).pack(pady=(2, 0))
        tk.Label(pitch, textvariable=self.freq_var, bg="#1b2430", fg="#e5edf7",
                 font=("Segoe UI", 11)).pack()
        tk.Label(pitch, textvariable=self.conf_var, bg="#1b2430", fg="#91a2b7",
                 font=("Segoe UI", 9)).pack(pady=(5, 0))

        controls = tk.Frame(outer, bg="#11151c")
        controls.pack(fill="x")
        self.start_btn = tk.Button(controls, text="▶  Start listening", command=self.start,
                                   bg="#167d68", fg="white", activebackground="#19977e",
                                   relief="flat", padx=18, pady=11, font=("Segoe UI", 10, "bold"))
        self.start_btn.pack(side="left")
        self.rec_btn = tk.Button(controls, text="●  Record melody", command=self.toggle_record,
                                 bg="#263243", fg="white", relief="flat", padx=18, pady=11,
                                 font=("Segoe UI", 10, "bold"), state="disabled")
        self.rec_btn.pack(side="left", padx=8)
        self.stop_btn = tk.Button(controls, text="Stop", command=self.stop,
                                  bg="#263243", fg="white", relief="flat", padx=18, pady=11,
                                  state="disabled")
        self.stop_btn.pack(side="left")

        tk.Label(outer, text="PIANO ROLL — TRANSCRIBED MELODY", bg="#11151c", fg="#aab6c5",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(24, 7))
        self.roll = tk.Canvas(outer, bg="#151b24", height=230, highlightthickness=0)
        self.roll.pack(fill="both", expand=True)
        self.roll_notes = tk.Label(outer, text="Notes will appear here as MIDI blocks.",
                                   bg="#11151c", fg="#7f91a8", font=("Segoe UI", 9))
        self.roll_notes.pack(anchor="w", pady=(5, 0))
        bottom = tk.Frame(outer, bg="#11151c")
        bottom.pack(fill="x", pady=(12, 0))
        self.export_btn = tk.Button(bottom, text="Export MIDI", command=self.export_midi,
                                    bg="#35445a", fg="white", relief="flat", padx=16, pady=9,
                                    state="disabled")
        self.export_btn.pack(side="left")
        self.wav_btn = tk.Button(bottom, text="Save recording WAV", command=self.save_wav,
                                 bg="#35445a", fg="white", relief="flat", padx=16, pady=9,
                                 state="disabled")
        self.wav_btn.pack(side="left", padx=8)
        tk.Label(bottom, textvariable=self.status_var, bg="#11151c", fg="#7f91a8",
                 font=("Segoe UI", 9)).pack(side="right")

    def _load_devices(self):
        try:
            devices = sd.query_devices()
            self.input_devices = [(i, d["name"]) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
            self.device_box["values"] = [f"{i}: {name}" for i, name in self.input_devices]
            if self.input_devices and not self.device_var.get():
                self.device_box.current(0)
        except Exception as e:
            self.status_var.set(f"Audio device error: {e}")

    def _selected_device(self):
        idx = self.device_box.current()
        return self.input_devices[idx][0] if idx >= 0 else None

    def start(self):
        if self.running:
            return
        try:
            self.events = []
            self.audio_chunks = []
            self.current_note = None
            self.current_start = None
            self.last_seen = 0.0
            self.running = True
            self.stream = sd.InputStream(device=self._selected_device(), channels=1,
                                         samplerate=SAMPLE_RATE, blocksize=HOP_SIZE,
                                         dtype="float32", callback=self._audio_callback)
            self.stream.start()
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.rec_btn.config(state="normal")
            self.status_var.set("Listening — hum a single note or melody")
        except Exception as e:
            self.running = False
            messagebox.showerror("Audio error", str(e))

    def _audio_callback(self, indata, frames, time_info, status):
        try:
            self.audio_q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def _poll_audio(self):
        if self.running:
            while True:
                try:
                    chunk = self.audio_q.get_nowait()
                except queue.Empty:
                    break
                if self.recording:
                    self.audio_chunks.append(chunk.copy())
                # Analyze rolling window from current chunk + recent history.
                self.smooth.append(chunk)
                if len(self.smooth) > 3:
                    self.smooth.pop(0)
                frame = np.concatenate(self.smooth)
                if len(frame) >= FRAME_SIZE:
                    frame = frame[-FRAME_SIZE:]
                    freq, confidence = yin_pitch(frame, SAMPLE_RATE)
                    now = time.monotonic()
                    if freq > 0 and confidence > 0.55:
                        note, midi, ideal = freq_to_note(freq)
                        if midi is not None:
                            cents = 1200 * np.log2(freq / ideal)
                            self.note_var.set(note)
                            self.freq_var.set(f"Frequency: {freq:.2f} Hz   |   {cents:+.0f} cents")
                            self.conf_var.set(f"Pitch confidence: {confidence*100:.0f}%")
                            if self.recording:
                                self._accept_note(midi, now)
                            self.last_seen = now
                    else:
                        self.note_var.set("—")
                        self.freq_var.set("Frequency: —")
                        self.conf_var.set("Pitch confidence: —")
                        if self.recording and self.current_note is not None and now - self.last_seen > 0.16:
                            self._finish_note(self.last_seen)
        self.root.after(30, self._poll_audio)

    def _accept_note(self, midi, now):
        # Hysteresis: ignore tiny pitch wobble; require a stable semitone change.
        if self.current_note is None:
            self.current_note = midi
            self.current_start = now
        elif midi != self.current_note:
            self._finish_note(now)
            self.current_note = midi
            self.current_start = now

    def _finish_note(self, end_time):
        if self.current_note is None or self.current_start is None:
            return
        duration = max(0.06, end_time - self.current_start)
        if duration >= 0.07:
            self.events.append((self.current_note, self.current_start, duration))
            note, _, _ = freq_to_note(440 * 2 ** ((self.current_note - 69) / 12))
            self.draw_roll()
        self.current_note = None
        self.current_start = None

    def toggle_record(self):
        if not self.running:
            return
        if not self.recording:
            self.recording = True
            self.record_start = time.monotonic()
            self.events = []
            self.audio_chunks = []
            self.draw_roll()
            self.rec_btn.config(text="■  Finish recording", bg="#9b3b4b")
            self.status_var.set("Recording — perform your melody")
        else:
            self.recording = False
            if self.current_note is not None:
                self._finish_note(time.monotonic())
            self.current_note = None
            self.current_start = None
            self.rec_btn.config(text="●  Record melody", bg="#263243")
            self.export_btn.config(state="normal" if self.events else "disabled")
            self.wav_btn.config(state="normal" if self.audio_chunks else "disabled")
            self.draw_roll()
            self.status_var.set(f"Recording finished — {len(self.events)} notes detected")

    def stop(self):
        if self.recording:
            self.toggle_record()
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.rec_btn.config(state="disabled")
        self.status_var.set("Stopped")

    def draw_roll(self):
        if not hasattr(self, "roll"):
            return
        canvas = self.roll
        canvas.delete("all")
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 180)
        label_w, top, bottom = 44, 8, 18
        usable_h = height - top - bottom
        pitches = [event[0] for event in self.events]
        if pitches:
            low, high = min(pitches), max(pitches)
            base = max(24, (low // 12) * 12 - 1)
            top_pitch = min(108, max(base + 11, (high // 12) * 12 + 11))
        else:
            base, top_pitch = 48, 71
        row_count = top_pitch - base + 1
        row_h = usable_h / max(row_count, 1)
        max_time = max((event[1] - getattr(self, "record_start", event[1]) + event[2] for event in self.events), default=8.0)
        max_time = max(8.0, max_time)
        timeline_w = width - label_w - 8
        for pitch in range(base, top_pitch + 1):
            y = top + (top_pitch - pitch) * row_h
            name, _, _ = freq_to_note(440 * 2 ** ((pitch - 69) / 12))
            is_black = "#" in name
            canvas.create_rectangle(0, y, label_w, y + row_h, fill="#252d38" if is_black else "#313b48", outline="#465160")
            canvas.create_text(label_w / 2, y + row_h / 2, text=name, fill="#c9d5e3", font=("Segoe UI", 8))
            canvas.create_rectangle(label_w, y, width, y + row_h, fill="#1b222c" if is_black else "#202833", outline="#303a47")
        for i in range(int(max_time / 0.5) + 1):
            x = label_w + (i * 0.5 / max_time) * timeline_w
            canvas.create_line(x, top, x, height - bottom, fill="#46515f" if i % 4 == 0 else "#303a47")
            if i % 4 == 0:
                canvas.create_text(x + 2, height - 7, text=f"{i * 0.5:.0f}s", anchor="w", fill="#8594a6", font=("Segoe UI", 8))
        for pitch, start, duration in self.events:
            relative = max(0, start - self.record_start)
            x1 = label_w + (relative / max_time) * timeline_w
            x2 = label_w + ((relative + duration) / max_time) * timeline_w
            y1 = top + (top_pitch - pitch) * row_h + 1
            y2 = y1 + max(5, row_h - 2)
            canvas.create_rectangle(x1, y1, max(x1 + 4, x2), y2, fill="#54b8e8", outline="#9bdcff", width=1)
            name, _, _ = freq_to_note(440 * 2 ** ((pitch - 69) / 12))
            if x2 - x1 > 35:
                canvas.create_text(x1 + 4, (y1 + y2) / 2, text=name, anchor="w", fill="#10202b", font=("Segoe UI", 8, "bold"))
        if hasattr(self, "roll_notes"):
            self.roll_notes.config(text=f"{len(self.events)} note events • {max_time:.1f} seconds")

    def export_midi(self):
        if not self.events:
            return
        path = filedialog.asksaveasfilename(defaultextension=".mid",
                    filetypes=[("MIDI files", "*.mid")], initialfile="hummify_melody.mid")
        if not path:
            return
        try:
            # MIDI tempo uses microseconds per beat; use a simple 120 BPM timeline.
            mid = mido.MidiFile(ticks_per_beat=480)
            track = mido.MidiTrack()
            mid.tracks.append(track)
            track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
            events = []
            origin = self.record_start
            for pitch, start, duration in self.events:
                start_tick = max(0, int((start - origin) * 960))
                end_tick = max(start_tick + 1, int((start - origin + duration) * 960))
                events.append((start_tick, True, pitch))
                events.append((end_tick, False, pitch))
            events.sort(key=lambda x: (x[0], x[1]))
            last_tick = 0
            for tick, is_on, pitch in events:
                delta = tick - last_tick
                track.append(mido.Message("note_on" if is_on else "note_off",
                                          note=pitch, velocity=90 if is_on else 0, time=delta))
                last_tick = tick
            mid.save(path)
            self.status_var.set(f"MIDI exported: {Path(path).name}")
            messagebox.showinfo("Export complete", "MIDI file saved. Import it into FL Studio.")
        except Exception as e:
            messagebox.showerror("MIDI export error", str(e))

    def save_wav(self):
        if not self.audio_chunks:
            return
        path = filedialog.asksaveasfilename(defaultextension=".wav",
                    filetypes=[("WAV audio", "*.wav")], initialfile="hummify_recording.wav")
        if not path:
            return
        try:
            audio = np.concatenate(self.audio_chunks)
            pcm = np.clip(audio * 32767, -32768, 32767).astype("<i2")
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(pcm.tobytes())
            self.status_var.set(f"WAV saved: {Path(path).name}")
        except Exception as e:
            messagebox.showerror("WAV export error", str(e))

    def close(self):
        try:
            self.stop()
        except Exception:
            pass
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = Hummify(root)
    root.mainloop()
