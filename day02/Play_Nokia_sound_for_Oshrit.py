import math
import os
import struct
import subprocess
import tempfile
import wave

from music21 import note, stream, tempo

print("ba")
print("Oshrita".capitalize())

SAMPLE_RATE = 44100

# Nokia ringtone (Gran Vals): (pitch, length in quarter notes)
NOKIA_TUNE = [
    ("E5", 0.5), ("D5", 0.5), ("F#4", 1), ("G#4", 1),
    ("C#5", 0.5), ("B4", 0.5), ("D4", 1), ("E4", 1),
    ("B4", 0.5), ("A4", 0.5), ("C#4", 1), ("E4", 1),
    ("A4", 3),
]


def build_stream():
    # 12 quarter notes at 144 BPM = 5 seconds
    s = stream.Stream()
    s.append(tempo.MetronomeMark(number=144))
    for pitch, length in NOKIA_TUNE:
        s.append(note.Note(pitch, quarterLength=length))
    return s


def write_wav(s, path):
    frames = bytearray()
    for n in s.flatten().notes:
        seconds = n.seconds
        freq = n.pitch.frequency
        count = int(SAMPLE_RATE * seconds)
        for i in range(count):
            # short fade in/out so notes don't click
            envelope = min(1.0, i / 200, (count - i) / 800)
            sample = 0.4 * envelope * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
            frames += struct.pack("<h", int(sample * 32767))
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(bytes(frames))


def main():
    print("This is the main function.")
    s = build_stream()
    path = os.path.join(tempfile.gettempdir(), "nokia.wav")
    write_wav(s, path)
    subprocess.run(["afplay", path])


if __name__ == "__main__":
    print("This is the main module.")
    main()
