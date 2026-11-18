# Hummify — lightweight humming-to-MIDI

A small, fully local Windows desktop prototype. It listens to a microphone, estimates a monophonic humming pitch, displays note names, records a melody, and exports MIDI/WAV.

## Requirements
- Windows 10/11
- Python 3.10+ (64-bit recommended)
- Microphone

## Run
1. Extract this folder.
2. Double-click `launch.bat`.
3. The first launch creates a local `.venv` and installs three Python packages.
4. Select your microphone and press **Start listening**.
5. Press **Record melody**, hum one note at a time, then **Finish recording**.
6. Click **Export MIDI** and import the `.mid` file into FL Studio.

## Keeping it small
- Tkinter is included with standard Windows Python; no Qt/PySide bundle.
- No AI model, cloud service, API key, or network service is used at runtime.
- Runtime dependencies are NumPy, sounddevice, and mido.
- The first setup needs internet only to download these packages. After installation, the app works offline.

## Notes and limitations
- This is V1: monophonic pitch transcription (one hummed note at a time).
- Pitch tracking is intended for clear, sustained humming. Background music, chords, breathy sounds, and very short notes can confuse it.
- Timing is currently estimated from detected pitch transitions and silence. Rhythm quantization, editable piano roll, and real-time virtual MIDI output are planned next.
- For best results, use a quiet room, keep the microphone close, and hum clearly without accompaniment.
- MIDI export uses a 120 BPM timing basis. You can adjust tempo in FL Studio after import.

## Troubleshooting
- If no microphone appears, check Windows Settings → System → Sound → Input and microphone privacy permissions.
- If audio initialization fails, close other apps using the microphone and try again.
- If `py` is not recognised, install Python 3.10+ from python.org and enable the PATH option.
\n## Piano roll output\nDetected notes are displayed as horizontal MIDI-style blocks on pitch rows against a time grid. Manual drag-editing and quantization are planned for a later version.\n