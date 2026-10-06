"""
NEON NEXUS v5 - De-Humanized Weapon Audio
Removes vocal-formant resonance. Replaces "thud" with inharmonic
metallic body, FM sub-bass, and machine-like transient layers.

Requirements: pip install pygame numpy
Optional (faster filters): pip install scipy
Run: python neon_nexus.py
"""

import pygame
import random
import math
import sys
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ---------- Configuration ----------
WIDTH, HEIGHT = 1280, 720
FPS = 60
BG_COLOR = (8, 8, 20)
NEON_CYAN = (0, 255, 255)
NEON_PINK = (255, 50, 200)
NEON_YELLOW = (255, 230, 60)
NEON_GREEN = (60, 255, 120)
NEON_RED = (255, 60, 60)

SAMPLE_RATE = 44100

# ---------- Controller ----------
STICK_DEADZONE = 0.22
TRIGGER_THRESHOLD = 0.4


# ---------- Utility ----------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def vec_len(x, y):
    return math.hypot(x, y)


def normalize(x, y):
    l = vec_len(x, y)
    return (0, 0) if l == 0 else (x / l, y / l)


# ============================================================
#        DE-HUMANIZED PROCEDURAL AUDIO ENGINE v5
# ============================================================
class AudioEngine:
    """
    Key philosophy:
      - NO chest formants (150-300 Hz). That band = human body.
      - Sub-bass lives BELOW 90 Hz (physical, not vocal).
      - Metallic resonances live at INHARMONIC ratios (1.414, 2.718, etc).
      - Decay curves are LAYERED (fast × slow) so they don't read as organic.
      - Every sound has an upward micro-chirp at attack = machine alert.
    """

    def __init__(self):
        pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 512)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(32)

        self.sounds = {}
        self.music_channel = pygame.mixer.Channel(0)
        self.music_volume = 0.55
        self.sfx_volume = 0.68
        self.muted = False
        self._build_all()

    # -------- Building blocks --------
    def _n(self, duration):
        return max(1, int(SAMPLE_RATE * duration))

    def _t(self, duration):
        return np.linspace(0, duration, self._n(duration), endpoint=False)

    def _sine(self, freq, duration, phase=0.0):
        t = self._t(duration)
        return np.sin(2 * np.pi * freq * t + phase)

    def _square(self, freq, duration, duty=0.5):
        t = self._t(duration)
        return np.where((t * freq) % 1.0 < duty, 1.0, -1.0)

    def _saw(self, freq, duration):
        t = self._t(duration)
        return 2.0 * ((t * freq) % 1.0) - 1.0

    def _triangle(self, freq, duration):
        t = self._t(duration)
        return 2.0 * np.abs(2.0 * ((t * freq) % 1.0) - 1.0) - 1.0

    def _noise(self, duration):
        return np.random.uniform(-1, 1, self._n(duration))

    def _pink_noise(self, duration):
        white = np.random.uniform(-1, 1, self._n(duration))
        b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
        a = [1, -2.494956002, 2.017265875, -0.522189400]
        out = np.zeros_like(white)
        for i in range(3, len(white)):
            out[i] = (b[0] * white[i] + b[1] * white[i - 1] + b[2] * white[i - 2] + b[3] * white[i - 3]
                      - a[1] * out[i - 1] - a[2] * out[i - 2] - a[3] * out[i - 3])
        return out / (np.max(np.abs(out)) + 1e-9)

    def _detuned_stack(self, freq, duration, voices=3, detune=0.012):
        out = np.zeros(self._n(duration))
        ph0 = random.random() * math.tau
        for i in range(voices):
            offset = (i - (voices - 1) / 2) * detune * freq
            out += self._sine(freq + offset, duration, ph0 + i * 1.7)
        return out / voices

    def _sweep(self, f0, f1, duration, kind="sine"):
        t = self._t(duration)
        if duration <= 0:
            return np.zeros(1)
        k = (f1 / max(f0, 1e-6)) ** (1.0 / duration)
        freq = f0 * (k ** t)
        phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
        if kind == "sine":
            return np.sin(phase)
        elif kind == "square":
            return np.sign(np.sin(phase))
        elif kind == "saw":
            return 2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0
        elif kind == "triangle":
            return 2.0 * np.abs(2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0) - 1.0
        return np.sin(phase)

    # -------- Inharmonic utilities --------
    def _ring_mod(self, carrier, modulator):
        n = min(len(carrier), len(modulator))
        return carrier[:n] * modulator[:n]

    def _ring_mod_am(self, carrier, modulator, depth=1.0):
        n = min(len(carrier), len(modulator))
        return carrier[:n] * (1.0 - depth + depth * modulator[:n])

    def _fm(self, carrier_freq, mod_freq, duration, index=5.0, mod_env_decay=None):
        t = self._t(duration)
        if mod_env_decay is not None:
            mod_amp = np.exp(-t / mod_env_decay)
        else:
            mod_amp = np.ones_like(t)
        modulator = index * mod_amp * np.sin(2 * np.pi * mod_freq * t)
        phase = 2 * np.pi * carrier_freq * t + modulator
        return np.sin(phase)

    def _fm_sub(self, freq, duration, mod_ratio=1.414, index=2.0, decay=0.08):
        """
        Machine sub-bass: FM with a NON-INTEGER ratio (√2) so the harmonic
        series is broken — reads as engineered, not biological.
        """
        t = self._t(duration)
        mod_amp = np.exp(-t / decay)
        modulator = index * mod_amp * np.sin(2 * np.pi * freq * mod_ratio * t)
        phase = 2 * np.pi * freq * t + modulator
        return np.sin(phase)

    # -------- Filters --------
    def _lowpass(self, wave, cutoff_hz):
        if cutoff_hz >= SAMPLE_RATE / 2:
            return wave
        rc = 1.0 / (2 * np.pi * cutoff_hz)
        dt = 1.0 / SAMPLE_RATE
        alpha = dt / (rc + dt)
        try:
            from scipy.signal import lfilter
            return lfilter([alpha], [1, alpha - 1], wave)
        except ImportError:
            out = np.empty_like(wave)
            prev = 0.0
            for i in range(len(wave)):
                prev = prev + alpha * (wave[i] - prev)
                out[i] = prev
            return out

    def _highpass(self, wave, cutoff_hz):
        if cutoff_hz <= 0:
            return wave
        rc = 1.0 / (2 * np.pi * cutoff_hz)
        dt = 1.0 / SAMPLE_RATE
        alpha = rc / (rc + dt)
        try:
            from scipy.signal import lfilter
            return lfilter([alpha, -alpha], [1, -alpha], wave)
        except ImportError:
            out = np.empty_like(wave)
            prev_in = 0.0
            prev_out = 0.0
            for i in range(len(wave)):
                out[i] = alpha * (prev_out + wave[i] - prev_in)
                prev_in = wave[i]
                prev_out = out[i]
            return out

    def _bandpass(self, wave, center_hz, q=1.0):
        bw = center_hz / max(q, 0.1)
        low = max(20.0, center_hz - bw / 2)
        high = min(SAMPLE_RATE / 2 - 100, center_hz + bw / 2)
        return self._highpass(self._lowpass(wave, high), low)

    def _notch(self, wave, center_hz, q=2.0):
        """
        Notch filter — removes a frequency band.
        Used to carve OUT the human formant region (200-350 Hz).
        """
        try:
            from scipy.signal import iirnotch, lfilter
            w0 = center_hz / (SAMPLE_RATE / 2)
            b, a = iirnotch(w0, q)
            return lfilter(b, a, wave)
        except ImportError:
            notch_band = self._bandpass(wave, center_hz, q=q)
            return wave - notch_band

    def _dehumanize(self, wave, low_band=180, high_band=380):
        """Carve out the human vocal/chest formant band entirely."""
        return self._notch(wave, math.sqrt(low_band * high_band), q=1.2)

    # -------- Envelope --------
    def _env(self, wave, attack=0.005, hold=0.0, decay=0.0, sustain=1.0,
             release=0.05, curve="exp"):
        n = len(wave)
        env = np.zeros(n)
        a = min(n, max(1, int(attack * SAMPLE_RATE)))
        h = min(n - a, max(0, int(hold * SAMPLE_RATE)))
        d = min(n - a - h, max(0, int(decay * SAMPLE_RATE)))
        r = min(n - a - h - d, max(1, int(release * SAMPLE_RATE)))

        if a > 0:
            env[:a] = np.linspace(0, 1, a)
        if h > 0:
            env[a:a + h] = 1.0
        if d > 0:
            if curve == "exp":
                env[a + h:a + h + d] = sustain + (1 - sustain) * np.exp(-5 * np.linspace(0, 1, d))
            else:
                env[a + h:a + h + d] = np.linspace(1, sustain, d)
        s_start = a + h + d
        s_end = n - r
        if s_end > s_start:
            env[s_start:s_end] = sustain
        if r > 0:
            if curve == "exp":
                env[n - r:] = sustain * np.exp(-5 * np.linspace(0, 1, r))
            else:
                env[n - r:] = np.linspace(sustain, 0, r)
        return wave * env

    def _dual_decay(self, wave, fast=0.02, slow=0.15, mix=0.5):
        n = len(wave)
        t = np.linspace(0, n / SAMPLE_RATE, n)
        fast_e = np.exp(-t / fast)
        slow_e = np.exp(-t / slow)
        return wave * (fast_e * (1 - mix) + slow_e * mix)

    def _soft_clip(self, wave, drive=1.5):
        return np.tanh(wave * drive)

    def _mix(self, *components):
        valid = [c for c in components if c is not None]
        if not valid:
            return np.zeros(1)
        max_len = max(len(c) for c in valid)
        out = np.zeros(max_len)
        for c in valid:
            out[:len(c)] += c
        return out

    def _to_sound(self, wave, volume=1.0, stereo_spread=0.0):
        peak = np.max(np.abs(wave))
        if peak > 0:
            wave = wave / peak
        wave = np.clip(wave, -1, 1) * volume
        wave_int = (wave * 32767).astype(np.int16)

        if stereo_spread > 0:
            delay_samples = int(stereo_spread * SAMPLE_RATE)
            if delay_samples > 0 and delay_samples < len(wave_int):
                left = wave_int
                right = np.concatenate((np.zeros(delay_samples, dtype=np.int16),
                                        wave_int[:-delay_samples]))
            else:
                left = right = wave_int
        else:
            left = right = wave_int

        stereo = np.ascontiguousarray(np.column_stack((left, right)))
        return pygame.sndarray.make_sound(stereo)

    # ============================================================
    #           DE-HUMANIZED WEAPON BUILDERS
    # ============================================================

    def _build_laser(self):
        dur = 0.18

        sub = self._fm_sub(freq=55, duration=0.12, mod_ratio=1.414, index=3.5, decay=0.04)
        sub = self._lowpass(sub, 110)
        sub = self._env(sub, attack=0.001, release=0.11, curve="exp")
        sub *= 1.0

        carrier = self._saw(440, 0.13)
        modulator = self._sine(440 * 1.414, 0.13)
        metallic = self._ring_mod(carrier, modulator)
        metallic = self._bandpass(metallic, 1400, q=1.8)
        metallic = self._env(metallic, attack=0.001, release=0.12, curve="exp")
        metallic *= 0.55

        bell = self._fm(carrier_freq=2400, mod_freq=2400 * 1.618, duration=0.055,
                        index=5.0, mod_env_decay=0.015)
        bell = self._bandpass(bell, 3100, q=2.5)
        bell = self._env(bell, attack=0.0003, release=0.05, curve="exp")
        bell *= 0.35

        crack = self._sweep(4500, 1800, 0.08, "square")
        crack = self._lowpass(crack, 6000)
        crack = self._highpass(crack, 800)
        crack = self._env(crack, attack=0.0005, release=0.075, curve="exp")
        crack *= 0.4

        chirp = self._sweep(3000, 5500, 0.006, "sine")
        chirp = self._env(chirp, attack=0.0003, release=0.005, curve="exp")
        chirp *= 0.45

        snap = self._noise(0.007)
        snap = self._highpass(snap, 4500)
        snap = self._env(snap, attack=0.0002, release=0.006, curve="exp")
        snap *= 0.55

        mix = self._mix(sub, metallic, bell, crack, chirp, snap)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._dual_decay(mix, fast=0.015, slow=0.10, mix=0.55)
        mix = self._soft_clip(mix, drive=1.7)
        return self._to_sound(mix, 0.55, stereo_spread=0.0006)

    def _build_enemy_laser(self):
        dur = 0.28

        sub = self._fm_sub(freq=48, duration=0.2, mod_ratio=0.707, index=4.0, decay=0.08)
        sub = self._lowpass(sub, 95)
        sub = self._env(sub, attack=0.002, release=0.17, curve="exp")
        sub *= 0.9

        carrier = self._sweep(1400, 900, 0.24, "sine")
        modulator = self._sine(17, 0.24)
        warble = self._ring_mod_am(carrier, modulator, depth=0.9)
        warble = self._env(warble, attack=0.004, release=0.2, curve="exp")
        warble *= 0.6

        overtone = self._fm(1000, 1000 * 2.718, 0.22, index=4.5, mod_env_decay=0.12)
        overtone = self._bandpass(overtone, 2200, q=1.6)
        overtone = self._env(overtone, attack=0.005, release=0.18, curve="exp")
        overtone *= 0.42

        sizzle = self._pink_noise(0.26)
        sizzle = self._highpass(sizzle, 1800)
        sizzle = self._bandpass(sizzle, 3400, q=0.8)
        sizzle = self._env(sizzle, attack=0.003, release=0.22, curve="exp")
        sizzle *= 0.5

        spat = self._sweep(4000, 800, 0.04, "sine")
        spat = self._env(spat, attack=0.0005, release=0.035, curve="exp")
        spat *= 0.3

        mix = self._mix(sub, warble, overtone, sizzle, spat)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._dual_decay(mix, fast=0.02, slow=0.14, mix=0.6)
        mix = self._soft_clip(mix, drive=1.6)
        return self._to_sound(mix, 0.5, stereo_spread=0.0022)

    def _build_explosion(self):
        dur = 0.85

        sub = self._fm_sub(freq=42, duration=0.6, mod_ratio=1.414, index=5.0, decay=0.25)
        sub = self._lowpass(sub, 95)
        sub = self._env(sub, attack=0.001, release=0.5, curve="exp")
        sub *= 1.25

        debris_carrier = self._noise(dur) * 0.6
        debris_mod = self._sine(313, dur)
        debris = self._ring_mod(debris_carrier, debris_mod)
        debris = self._bandpass(debris, 2600, q=2.2)
        debris = self._dual_decay(debris, fast=0.04, slow=0.4, mix=0.7)
        debris *= 0.55

        shriek = self._fm(900, 900 * 1.732, 0.5, index=6.0, mod_env_decay=0.18)
        shriek = self._bandpass(shriek, 2200, q=1.5)
        shriek = self._env(shriek, attack=0.002, decay=0.15, sustain=0.25, release=0.3, curve="exp")
        shriek *= 0.4

        crackle = self._noise(dur)
        crackle = self._highpass(crackle, 3500)
        crackle = self._env(crackle, attack=0.001, decay=0.08, sustain=0.06, release=0.35, curve="exp")
        crackle *= 0.3

        punch = self._noise(0.008)
        punch = self._highpass(punch, 2000)
        punch = self._env(punch, attack=0.0002, release=0.007, curve="exp")
        punch *= 0.7

        mix = self._mix(sub, debris, shriek, crackle, punch)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._soft_clip(mix, drive=1.8)
        return self._to_sound(mix, 0.68, stereo_spread=0.003)

    def _build_player_hit(self):
        dur = 0.42

        sub = self._fm_sub(freq=52, duration=0.28, mod_ratio=1.414, index=4.5, decay=0.06)
        sub = self._lowpass(sub, 105)
        sub = self._env(sub, attack=0.001, release=0.25, curve="exp")
        sub *= 1.0

        clang_carrier = self._saw(520, 0.28)
        clang_mod = self._sine(520 * 1.618, 0.28)
        clang = self._ring_mod(clang_carrier, clang_mod)
        clang = self._bandpass(clang, 1800, q=2.0)
        clang = self._dual_decay(clang, fast=0.02, slow=0.2, mix=0.6)
        clang *= 0.5

        alarm = self._fm(1400, 2200, 0.18, index=5.0, mod_env_decay=0.06)
        alarm = self._bandpass(alarm, 2400, q=1.8)
        alarm = self._env(alarm, attack=0.001, release=0.16, curve="exp")
        alarm *= 0.4

        sizzle = self._noise(0.3)
        sizzle = self._highpass(sizzle, 2800)
        sizzle = self._env(sizzle, attack=0.001, release=0.25, curve="exp")
        sizzle *= 0.35

        mix = self._mix(sub, clang, alarm, sizzle)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._soft_clip(mix, drive=1.8)
        return self._to_sound(mix, 0.7, stereo_spread=0.002)

    def _build_dash(self):
        dur = 0.4

        sub = self._fm_sub(freq=48, duration=dur, mod_ratio=1.414, index=3.0, decay=0.28)
        sub = self._lowpass(sub, 100)
        sub = self._env(sub, attack=0.005, decay=0.15, sustain=0.5, release=0.22, curve="exp")
        sub *= 0.85

        body = self._sweep(500, 2400, dur * 0.75, "sine")
        body = self._env(body, attack=0.01, release=dur * 0.6, curve="exp")
        body *= 0.4

        air = self._pink_noise(dur)
        air = self._bandpass(air, 1800, q=0.7)
        air = self._highpass(air, 600)
        air = self._env(air, attack=0.02, decay=0.2, sustain=0.45, release=0.22, curve="exp")
        air *= 0.7

        mix = self._mix(sub, body, air)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._soft_clip(mix, drive=1.5)
        return self._to_sound(mix, 0.5, stereo_spread=0.004)

    def _build_powerup(self):
        notes = [659.25, 783.99, 987.77, 1174.66]
        wave = np.array([])
        for f in notes:
            base = self._sine(f, 0.1) + 0.35 * self._sine(f * 2, 0.1)
            shimmer = self._ring_mod(self._sine(f * 1.5, 0.1), self._sine(f * 0.5, 0.1))
            note = base + 0.25 * shimmer + 0.3 * self._sine(f / 2, 0.1)
            note = self._env(note, attack=0.004, release=0.06, curve="exp")
            wave = np.concatenate((wave, note))
        wave = self._highpass(wave, 400)
        wave = self._soft_clip(wave, 1.3)
        return self._to_sound(wave, 0.5)

    def _build_heal(self):
        dur = 0.55
        w = self._sweep(600, 1100, dur, "sine")
        w += 0.35 * self._sweep(1200, 2200, dur, "sine")
        w += 0.15 * self._ring_mod(self._sweep(750, 1500, dur, "sine"), self._sine(9, dur))
        w = self._highpass(w, 500)
        w = self._env(w, attack=0.02, release=0.35, curve="exp")
        w = self._soft_clip(w, 1.2)
        return self._to_sound(w, 0.5)

    def _build_shield(self):
        dur = 0.6

        sub = self._fm_sub(freq=55, duration=dur, mod_ratio=1.414, index=2.5, decay=0.3)
        sub = self._lowpass(sub, 110)
        sub = self._env(sub, attack=0.01, decay=0.2, sustain=0.4, release=0.35, curve="exp")
        sub *= 0.5

        carrier = self._sweep(800, 2200, dur, "sine")
        modulator = self._sine(11, dur)
        charge = self._ring_mod_am(carrier, modulator, depth=0.65)
        charge = self._highpass(charge, 500)
        charge = self._env(charge, attack=0.03, decay=0.15, sustain=0.6, release=0.3, curve="exp")
        charge *= 0.6

        shimmer = (self._sine(1200, dur) * np.exp(-np.linspace(0, 3, self._n(dur)))
                   + 0.5 * self._sine(1800, dur) * np.exp(-np.linspace(0, 4, self._n(dur)))
                   + 0.3 * self._sine(2400, dur) * np.exp(-np.linspace(0, 5, self._n(dur))))
        shimmer *= 0.5

        mix = self._mix(sub, charge, shimmer)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._soft_clip(mix, 1.3)
        return self._to_sound(mix, 0.5)

    def _build_shield_hit(self):
        dur = 0.25

        sub = self._fm_sub(freq=60, duration=0.1, mod_ratio=1.414, index=2.0, decay=0.03)
        sub = self._lowpass(sub, 110)
        sub = self._env(sub, attack=0.001, release=0.08, curve="exp")
        sub *= 0.55

        zap = self._sweep(2600, 600, dur, "square")
        zap = self._lowpass(zap, 5000)
        zap = self._highpass(zap, 700)
        zap = self._env(zap, attack=0.001, release=0.18, curve="exp")
        zap *= 0.5

        tink = self._ring_mod(self._sine(1800, dur), self._sine(1800 * 1.618, dur))
        tink = self._bandpass(tink, 3200, q=2.5)
        tink = self._env(tink, attack=0.0005, release=0.2, curve="exp")
        tink *= 0.45

        sizzle = self._noise(dur)
        sizzle = self._highpass(sizzle, 2200)
        sizzle = self._env(sizzle, attack=0.001, release=0.2, curve="exp")
        sizzle *= 0.4

        mix = self._mix(sub, zap, tink, sizzle)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._soft_clip(mix, 1.6)
        return self._to_sound(mix, 0.58, stereo_spread=0.0018)

    def _build_wave_start(self):
        n1 = (self._detuned_stack(523.25, 0.18, voices=3, detune=0.01)
              + 0.5 * self._sine(1046.5, 0.18)
              + 0.3 * self._sine(1568, 0.18))
        n1 = self._highpass(n1, 350)
        n1 = self._env(n1, attack=0.006, release=0.12, curve="exp")

        n2 = (self._detuned_stack(783.99, 0.38, voices=3, detune=0.01)
              + 0.5 * self._sine(1568, 0.38)
              + 0.3 * self._sine(2350, 0.38))
        n2 = self._highpass(n2, 350)
        n2 = self._env(n2, attack=0.008, release=0.28, curve="exp")

        w = np.concatenate((n1, n2))
        w = self._soft_clip(w, 1.35)
        return self._to_sound(w, 0.55)

    def _build_game_over(self):
        notes = [523.25, 466.16, 415.30, 349.23, 261.63]
        wave = np.array([])
        for f in notes:
            note = (self._detuned_stack(f, 0.3, voices=2, detune=0.008)
                    + 0.35 * self._sine(f * 2, 0.3))
            note = self._highpass(note, 300)
            note = self._env(note, attack=0.01, release=0.22, curve="exp")
            wave = np.concatenate((wave, note))
        wave = self._soft_clip(wave, 1.3)
        return self._to_sound(wave, 0.58)

    def _build_player_death(self):
        dur = 1.4

        sub = self._fm_sub(freq=60, duration=dur, mod_ratio=1.414, index=6.0, decay=0.5)
        sweep_down = self._sweep(1.0, 0.4, dur, "sine")
        sub = sub * self._lowpass(sweep_down, 50)
        sub = self._lowpass(sub, 100)
        sub = self._env(sub, attack=0.001, release=1.0, curve="exp")
        sub *= 1.1

        wail = self._fm(400, 400 * 1.732, 1.0, index=8.0, mod_env_decay=0.5)
        wail = self._bandpass(wail, 1400, q=1.2)
        wail = self._env(wail, attack=0.005, release=0.9, curve="exp")
        wail *= 0.6

        cascade = self._ring_mod(self._sweep(2200, 220, 0.9, "saw"), self._sine(137, 0.9))
        cascade = self._bandpass(cascade, 1800, q=2.0)
        cascade = self._dual_decay(cascade, fast=0.05, slow=0.4, mix=0.7)
        cascade *= 0.5

        hiss = self._noise(dur)
        hiss = self._highpass(hiss, 2800)
        hiss = self._env(hiss, attack=0.01, decay=0.2, sustain=0.08, release=0.8, curve="exp")
        hiss *= 0.3

        mix = self._mix(sub, wail, cascade, hiss)
        mix = self._dehumanize(mix, low_band=180, high_band=380)
        mix = self._soft_clip(mix, 1.9)
        return self._to_sound(mix, 0.72, stereo_spread=0.005)

    def _build_ui_click(self):
        w = self._sine(1400, 0.04) + 0.4 * self._sine(2800, 0.04)
        w += 0.3 * self._ring_mod(self._sine(2100, 0.04), self._sine(2900, 0.04))
        w = self._highpass(w, 600)
        w = self._env(w, attack=0.001, release=0.03, curve="exp")
        w = self._soft_clip(w, 1.2)
        return self._to_sound(w, 0.32)

    def _build_music_loop(self):
        duration = 8.0
        n = self._n(duration)
        t = np.linspace(0, duration, n, endpoint=False)

        drone = (0.30 * np.sin(2 * np.pi * 27.5 * t)
                 + 0.25 * np.sin(2 * np.pi * 55 * t)
                 + 0.12 * np.sin(2 * np.pi * 82.4 * t))

        pad = (0.10 * self._detuned_stack(440, duration, voices=4, detune=0.006)
               + 0.08 * self._detuned_stack(523.25, duration, voices=4, detune=0.006)
               + 0.08 * self._detuned_stack(659.25, duration, voices=4, detune=0.006))
        pad *= (0.65 + 0.35 * np.sin(2 * np.pi * 0.125 * t))

        arp_notes = [880, 1046.50, 1318.51, 1760]
        arp = np.zeros(n)
        step = int(0.25 * SAMPLE_RATE)
        for i in range(0, n, step):
            f = arp_notes[(i // step) % len(arp_notes)]
            env_len = min(step, n - i)
            env = np.exp(-np.linspace(0, 5, env_len))
            idx = np.arange(env_len)
            seg = np.sin(2 * np.pi * f * idx / SAMPLE_RATE) * env * 0.10
            arp[i:i + env_len] += seg

        kick = np.zeros(n)
        beat = int(0.5 * SAMPLE_RATE)
        for i in range(0, n, beat):
            env_len = min(int(0.16 * SAMPLE_RATE), n - i)
            env = np.exp(-np.linspace(0, 8, env_len))
            mod = 2.5 * env * np.sin(2 * np.pi * 45 * 1.414 * np.arange(env_len) / SAMPLE_RATE)
            freq = np.linspace(120, 42, env_len)
            phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE + mod
            kick[i:i + env_len] += np.sin(phase) * env * 0.55

        mix = drone + pad + arp + kick
        mix = np.tanh(mix * 1.1)
        fade = int(0.3 * SAMPLE_RATE)
        mix[:fade] *= np.linspace(0, 1, fade)
        mix[-fade:] *= np.linspace(1, 0, fade)
        return self._to_sound(mix, self.music_volume)

    def _build_all(self):
        self.sounds["laser"] = self._build_laser()
        self.sounds["enemy_laser"] = self._build_enemy_laser()
        self.sounds["explosion"] = self._build_explosion()
        self.sounds["player_hit"] = self._build_player_hit()
        self.sounds["dash"] = self._build_dash()
        self.sounds["powerup"] = self._build_powerup()
        self.sounds["heal"] = self._build_heal()
        self.sounds["shield"] = self._build_shield()
        self.sounds["shield_hit"] = self._build_shield_hit()
        self.sounds["wave_start"] = self._build_wave_start()
        self.sounds["game_over"] = self._build_game_over()
        self.sounds["player_death"] = self._build_player_death()
        self.sounds["ui_click"] = self._build_ui_click()
        self.sounds["music"] = self._build_music_loop()

        for name, snd in self.sounds.items():
            if name != "music":
                snd.set_volume(self.sfx_volume)

    # -------- Playback --------
    def play(self, name, volume_scale=1.0):
        if self.muted:
            return
        snd = self.sounds.get(name)
        if snd:
            ch = pygame.mixer.find_channel(True)
            if ch:
                ch.set_volume(clamp(self.sfx_volume * volume_scale, 0, 1))
                ch.play(snd)

    def play_laser(self):
        self.play("laser", volume_scale=random.uniform(0.9, 1.0))

    def play_explosion(self):
        self.play("explosion", volume_scale=random.uniform(0.88, 1.0))

    def start_music(self):
        if self.muted:
            return
        try:
            self.music_channel.play(self.sounds["music"], loops=-1)
            self.music_channel.set_volume(self.music_volume)
        except Exception as e:
            print("Music error:", e)

    def toggle_mute(self):
        self.muted = not self.muted
        if self.muted:
            pygame.mixer.pause()
        else:
            pygame.mixer.unpause()
        return self.muted

    def duck_music(self, amount=0.4):
        self.music_channel.set_volume(self.music_volume * amount)

    def unduck_music(self):
        self.music_channel.set_volume(self.music_volume)


# ============================================================
#                       GAME OBJECTS
# ============================================================
@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    color: Tuple[int, int, int]
    size: float
    gravity: float = 0.0
    drag: float = 0.98

    def update(self, dt):
        self.vx *= self.drag
        self.vy *= self.drag
        self.vy += self.gravity * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.life -= dt

    def draw(self, surf):
        if self.life <= 0:
            return
        alpha = clamp(self.life / self.max_life, 0, 1)
        r = max(1, int(self.size * alpha))
        c = (int(self.color[0] * alpha), int(self.color[1] * alpha), int(self.color[2] * alpha))
        pygame.draw.circle(surf, c, (int(self.x), int(self.y)), r)


class ParticleSystem:
    def __init__(self):
        self.particles: List[Particle] = []

    def emit(self, x, y, count, color, speed=200, life=0.6, size=3,
             spread=math.tau, direction=0, gravity=0):
        for _ in range(count):
            ang = direction + random.uniform(-spread / 2, spread / 2)
            spd = random.uniform(speed * 0.3, speed)
            self.particles.append(Particle(
                x=x, y=y,
                vx=math.cos(ang) * spd,
                vy=math.sin(ang) * spd,
                life=life, max_life=life,
                color=color,
                size=random.uniform(size * 0.5, size),
                gravity=gravity,
            ))

    def update(self, dt):
        for p in self.particles:
            p.update(dt)
        self.particles = [p for p in self.particles if p.life > 0]

    def draw(self, surf):
        for p in self.particles:
            p.draw(surf)


class Entity:
    def __init__(self, x, y, radius):
        self.x, self.y = x, y
        self.vx, self.vy = 0.0, 0.0
        self.radius = radius
        self.alive = True

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt


class Bullet(Entity):
    def __init__(self, x, y, vx, vy, color=NEON_CYAN, damage=1, friendly=True):
        super().__init__(x, y, 4)
        self.vx, self.vy = vx, vy
        self.color = color
        self.damage = damage
        self.friendly = friendly
        self.life = 2.0

    def update(self, dt):
        super().update(dt)
        self.life -= dt
        if self.life <= 0 or not (-50 < self.x < WIDTH + 50 and -50 < self.y < HEIGHT + 50):
            self.alive = False

    def draw(self, surf):
        pygame.draw.circle(surf, (0, 80, 90), (int(self.x), int(self.y)), self.radius + 3)
        pygame.draw.circle(surf, self.color, (int(self.x), int(self.y)), self.radius)
        pygame.draw.circle(surf, (255, 255, 255), (int(self.x), int(self.y)), max(1, self.radius - 2))
        for i in range(1, 5):
            a = 1 - i / 5
            r = max(1, int(self.radius * a))
            tx = int(self.x - self.vx * 0.010 * i)
            ty = int(self.y - self.vy * 0.010 * i)
            c = (int(self.color[0] * a * 0.7), int(self.color[1] * a * 0.7), int(self.color[2] * a * 0.7))
            pygame.draw.circle(surf, c, (tx, ty), r)


class Player(Entity):
    def __init__(self, x, y):
        super().__init__(x, y, 14)
        self.angle = 0
        self.accel = 900
        self.max_speed = 420
        self.friction = 0.90
        self.fire_cooldown = 0.0
        self.fire_rate = 0.14
        self.hp = 5
        self.max_hp = 5
        self.invuln = 0.0
        self.dash_cooldown = 0.0
        self.dash_active = 0.0
        self.dash_dir = (0, 0)
        self.score = 0
        self.virtual_mouse = (WIDTH // 2, HEIGHT // 2)
        # Ship color palette. Index 0 = default black/red.
        self.color_palette = [
            ((255, 40, 40),  (10, 0, 0)),    # red outline, black body (default)
            ((0, 255, 255),  (0, 40, 50)),   # cyan
            ((60, 255, 120), (0, 40, 20)),   # green
            ((255, 230, 60), (50, 40, 0)),   # yellow
            ((255, 50, 200), (50, 0, 40)),   # pink
            ((180, 120, 255),(30, 10, 50)),  # violet
            ((255, 255, 255),(20, 20, 20)),  # white
        ]
        self.color_index = 0

    def handle_input(self, keys, dt):
        ax, ay = 0, 0
        if keys[pygame.K_a] or keys[pygame.K_LEFT]:
            ax -= 1
        if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
            ax += 1
        if keys[pygame.K_w] or keys[pygame.K_UP]:
            ay -= 1
        if keys[pygame.K_s] or keys[pygame.K_DOWN]:
            ay += 1
        ax, ay = normalize(ax, ay)
        self.vx += ax * self.accel * dt
        self.vy += ay * self.accel * dt

        mx, my = self.virtual_mouse
        self.angle = math.atan2(my - self.y, mx - self.x)

    def try_dash(self):
        if self.dash_cooldown <= 0:
            self.dash_active = 0.18
            self.dash_cooldown = 0.9
            self.dash_dir = normalize(self.vx, self.vy)
            if self.dash_dir == (0, 0):
                self.dash_dir = (math.cos(self.angle), math.sin(self.angle))
            self.invuln = max(self.invuln, 0.25)
            return True
        return False

    def update(self, dt):
        if self.dash_active > 0:
            self.dash_active -= dt
            self.vx = self.dash_dir[0] * 1400
            self.vy = self.dash_dir[1] * 1400
        else:
            self.vx *= self.friction
            self.vy *= self.friction

        spd = vec_len(self.vx, self.vy)
        if spd > self.max_speed and self.dash_active <= 0:
            k = self.max_speed / spd
            self.vx *= k
            self.vy *= k

        super().update(dt)
        self.x = clamp(self.x, self.radius, WIDTH - self.radius)
        self.y = clamp(self.y, self.radius, HEIGHT - self.radius)

        self.fire_cooldown -= dt
        self.dash_cooldown -= dt
        self.invuln -= dt

    def shoot(self):
        """Spawn bullets. NOTE: cooldown is managed by the caller
        (Game._fire_player). Do NOT check fire_cooldown here."""
        bullets = []
        for offset in (-0.08, 0.08):
            a = self.angle + offset
            bullets.append(Bullet(
                self.x + math.cos(a) * 20,
                self.y + math.sin(a) * 20,
                math.cos(a) * 900,
                math.sin(a) * 900,
                color=NEON_CYAN,
                damage=1,
            ))
        return bullets

    def take_damage(self, amount=1):
        if self.invuln > 0:
            return False
        self.hp -= amount
        self.invuln = 1.2
        if self.hp <= 0:
            self.alive = False
        return True

    def draw(self, surf):
        flame_dir = math.atan2(-self.vy, -self.vx)
        if vec_len(self.vx, self.vy) > 40:
            for _ in range(2):
                a = flame_dir + random.uniform(-0.4, 0.4)
                pygame.draw.circle(surf, NEON_PINK,
                                   (int(self.x + math.cos(a) * 12), int(self.y + math.sin(a) * 12)),
                                   random.randint(2, 4))

        tip = (self.x + math.cos(self.angle) * 20, self.y + math.sin(self.angle) * 20)
        left = (self.x + math.cos(self.angle + 2.5) * 14, self.y + math.sin(self.angle + 2.5) * 14)
        right = (self.x + math.cos(self.angle - 2.5) * 14, self.y + math.sin(self.angle - 2.5) * 14)

        outline, body = self.color_palette[self.color_index]

        color = outline
        if self.invuln > 0 and int(self.invuln * 20) % 2 == 0:
            color = (255, 255, 255)   # white flash while invulnerable

        pygame.draw.polygon(surf, color, [tip, left, right], 2)     # outline
        pygame.draw.polygon(surf, body, [tip, left, right])         # body
        pygame.draw.circle(surf, outline, (int(self.x), int(self.y)), 4)  # core


class Enemy(Entity):
    def __init__(self, x, y, kind="chaser"):
        super().__init__(x, y, 14)
        self.kind = kind
        self.hp = 2 if kind == "chaser" else 3
        self.speed = 140 if kind == "chaser" else 90
        self.color = NEON_PINK if kind == "chaser" else NEON_YELLOW
        self.angle = 0
        self.fire_cooldown = random.uniform(0.5, 1.5)

    def update(self, dt, player, spawn_bullet):
        dx, dy = player.x - self.x, player.y - self.y
        dist = vec_len(dx, dy) or 1
        nx, ny = dx / dist, dy / dist
        self.angle = math.atan2(dy, dx)

        if self.kind == "chaser":
            self.vx = nx * self.speed
            self.vy = ny * self.speed
        elif self.kind == "shooter":
            target = 220
            if dist > target + 20:
                self.vx, self.vy = nx * self.speed, ny * self.speed
            elif dist < target - 20:
                self.vx, self.vy = -nx * self.speed, -ny * self.speed
            else:
                self.vx = -ny * self.speed * 0.7
                self.vy = nx * self.speed * 0.7
            self.fire_cooldown -= dt
            if self.fire_cooldown <= 0 and dist < 600:
                self.fire_cooldown = random.uniform(1.2, 2.0)
                spawn_bullet(self.x, self.y, nx * 380, ny * 380)

        super().update(dt)

    def draw(self, surf):
        r = self.radius
        pts = []
        for i in range(4):
            a = self.angle + i * math.tau / 4 + pygame.time.get_ticks() * 0.002
            pts.append((self.x + math.cos(a) * r, self.y + math.sin(a) * r))
        pygame.draw.polygon(surf, self.color, pts, 2)
        pygame.draw.polygon(surf, (self.color[0] // 4, self.color[1] // 4, self.color[2] // 4), pts)
        pygame.draw.circle(surf, self.color, (int(self.x), int(self.y)), 3)

    def hit(self, dmg):
        self.hp -= dmg
        if self.hp <= 0:
            self.alive = False
            return True
        return False


class PowerUp(Entity):
    def __init__(self, x, y, kind):
        super().__init__(x, y, 12)
        self.kind = kind
        self.life = 12.0
        self.angle = 0

    def update(self, dt, player):
        self.angle += dt * 3
        dx, dy = player.x - self.x, player.y - self.y
        if vec_len(dx, dy) < 200:
            nx, ny = normalize(dx, dy)
            self.vx = nx * 200
            self.vy = ny * 200
        else:
            self.vx *= 0.95
            self.vy *= 0.95
        super().update(dt)
        self.life -= dt
        if self.life <= 0:
            self.alive = False

    def draw(self, surf):
        colors = {"hp": NEON_GREEN, "rapid": NEON_YELLOW, "shield": NEON_CYAN}
        c = colors.get(self.kind, (255, 255, 255))
        r = self.radius + math.sin(self.angle * 2) * 2
        pts = []
        for i in range(6):
            a = self.angle + i * math.tau / 6
            pts.append((self.x + math.cos(a) * r, self.y + math.sin(a) * r))
        pygame.draw.polygon(surf, c, pts, 2)


class Camera:
    def __init__(self):
        self.shake = 0.0

    def add_shake(self, amount):
        self.shake = min(20, self.shake + amount)

    def update(self, dt):
        self.shake = max(0, self.shake - dt * 40)

    def offset(self):
        if self.shake <= 0:
            return (0, 0)
        return (random.uniform(-self.shake, self.shake),
                random.uniform(-self.shake, self.shake))


class Starfield:
    def __init__(self, count=120):
        self.stars = [(random.uniform(0, WIDTH), random.uniform(0, HEIGHT),
                       random.uniform(0.3, 1.0), random.uniform(10, 60)) for _ in range(count)]

    def update(self, dt, cam_offset):
        new = []
        for x, y, b, spd in self.stars:
            x -= spd * dt
            if x < -5:
                x = WIDTH + 5
                y = random.uniform(0, HEIGHT)
            new.append((x, y, b, spd))
        self.stars = new

    def draw(self, surf):
        for x, y, b, _ in self.stars:
            c = int(200 * b)
            surf.set_at((int(x), int(y)), (c, c, c))


# ============================================================
#                       CONTROLLER
# ============================================================
class ControllerState:
    """
    Wraps an optional pygame joystick. All reads are safe even
    if no controller is connected. Hot-plug aware.
    """
    def __init__(self):
        self.joystick: Optional[pygame.joystick.JoystickType] = None
        self.active = False
        self._init_joysticks()

    def _init_joysticks(self):
        try:
            pygame.joystick.init()
        except Exception:
            return
        if pygame.joystick.get_count() > 0:
            self._attach(0)

    def _attach(self, index: int):
        try:
            js = pygame.joystick.Joystick(index)
            js.init()
            self.joystick = js
            self.active = True
            print(f"Controller connected: {js.get_name()}")
        except Exception as e:
            print("Controller init failed:", e)
            self.joystick = None
            self.active = False

    def handle_event(self, event):
        if event.type == pygame.JOYDEVICEADDED:
            if self.joystick is None:
                self._attach(event.device_index)
        elif event.type == pygame.JOYDEVICEREMOVED:
            if self.joystick is not None and event.instance_id == self.joystick.get_instance_id():
                self.joystick = None
                self.active = False
                print("Controller disconnected.")

    def _axis(self, idx: int) -> float:
        if not self.active or self.joystick is None:
            return 0.0
        try:
            if idx >= self.joystick.get_numaxes():
                return 0.0
            v = self.joystick.get_axis(idx)
            return 0.0 if abs(v) < STICK_DEADZONE else v
        except Exception:
            return 0.0

    def _button(self, idx: int) -> bool:
        if not self.active or self.joystick is None:
            return False
        try:
            if idx >= self.joystick.get_numbuttons():
                return False
            return bool(self.joystick.get_button(idx))
        except Exception:
            return False

    def _hat(self, idx: int) -> Tuple[int, int]:
        if not self.active or self.joystick is None:
            return (0, 0)
        try:
            if idx >= self.joystick.get_numhats():
                return (0, 0)
            return self.joystick.get_hat(idx)
        except Exception:
            return (0, 0)

    def move_vector(self) -> Tuple[float, float]:
        x = self._axis(0)
        y = self._axis(1)
        if x == 0.0 and y == 0.0:
            return (0.0, 0.0)
        return normalize(x, y)

    def aim_vector(self) -> Tuple[float, float]:
        x = self._axis(2)
        y = self._axis(3)
        if x == 0.0 and y == 0.0:
            return (0.0, 0.0)
        return normalize(x, y)

    def fire_held(self) -> bool:
        rt = 0.0
        try:
            if self.active and self.joystick and self.joystick.get_numaxes() > 5:
                rt = self.joystick.get_axis(5)
        except Exception:
            rt = 0.0
        return rt > TRIGGER_THRESHOLD or self._button(0)

    def dash_pressed(self) -> bool:
        return self._button(1) or self._button(4)

    def restart_pressed(self) -> bool:
        return self._button(7) or self._button(0)

    def mute_pressed(self) -> bool:
        return self._button(3) or self._button(6)

    def dpup_pressed(self) -> bool:
        """D-pad Up. Works whether the pad reports it as a button or a hat."""
        if self._button(11):
            return True
        hx, hy = self._hat(0)
        return hy > 0


# ============================================================
#                          GAME
# ============================================================
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("NEON NEXUS v5 - De-Humanized Audio")

        # Real display (what the OS shows). Sized to native desktop res.
        self.desktop_sizes = pygame.display.get_desktop_sizes()
        dw, dh = self.desktop_sizes[0] if self.desktop_sizes else (WIDTH, HEIGHT)

        self.fullscreen = True
        if self.fullscreen:
            self.display = pygame.display.set_mode((dw, dh), pygame.FULLSCREEN)
        else:
            self.display = pygame.display.set_mode((WIDTH, HEIGHT))

        # Virtual surface: game logic always draws here at 1280x720.
        self.screen = pygame.Surface((WIDTH, HEIGHT)).convert()

        # Compute letterbox scale + offset for the virtual surface.
        self._recalc_viewport()

        self.clock = pygame.time.Clock()

        print("Synthesizing de-humanized layered audio... (a few seconds)")
        self.audio = AudioEngine()
        print("Audio ready.")

        self.controller = ControllerState()

        self.font_big = pygame.font.SysFont("Consolas", 48, bold=True)
        self.font = pygame.font.SysFont("Consolas", 22, bold=True)
        self.small = pygame.font.SysFont("Consolas", 16)
        self.tiny = pygame.font.SysFont("Consolas", 13)

        self.virtual_mouse = (WIDTH // 2, HEIGHT // 2)

        self.reset()
        self.audio.start_music()

    def _recalc_viewport(self):
        """Compute letterbox scale + centering offset for the virtual surface."""
        dw, dh = self.display.get_size()
        scale = min(dw / WIDTH, dh / HEIGHT)
        vw = int(WIDTH * scale)
        vh = int(HEIGHT * scale)
        self._viewport_scale = scale
        self._viewport_rect = pygame.Rect((dw - vw) // 2, (dh - vh) // 2, vw, vh)

    def _toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            dw, dh = self.desktop_sizes[0] if self.desktop_sizes else (WIDTH, HEIGHT)
            self.display = pygame.display.set_mode((dw, dh), pygame.FULLSCREEN)
        else:
            self.display = pygame.display.set_mode((WIDTH, HEIGHT))
        self._recalc_viewport()

    def _screen_to_virtual(self, sx, sy):
        """Map a real-window mouse position to virtual 1280x720 coords."""
        rx, ry = self._viewport_rect.x, self._viewport_rect.y
        scale = self._viewport_scale or 1.0
        return ((sx - rx) / scale, (sy - ry) / scale)

    def reset(self):
        self.player = Player(WIDTH // 2, HEIGHT // 2)
        self.bullets: List[Bullet] = []
        self.enemies: List[Enemy] = []
        self.powerups: List[PowerUp] = []
        self.particles = ParticleSystem()
        self.camera = Camera()
        self.stars = Starfield()
        self.wave = 0
        self.wave_timer = 2.0
        self.enemies_to_spawn = 0
        self.spawn_timer = 0
        self.game_over = False
        self.time_survived = 0.0
        self.rapid_timer = 0.0
        self.shield = 0
        self._hit_sfx_cd = 0.0
        self._gameover_timer = -1.0
        self._pad_dash_prev = False
        self._pad_restart_prev = False
        self._pad_mute_prev = False
        self._pad_dpup_prev = False
        self._input_lock_dash = 0.0
        self._input_lock_mute = 0.0

    def spawn_bullet(self, x, y, vx, vy):
        self.bullets.append(Bullet(x, y, vx, vy, color=NEON_RED, friendly=False))
        self.audio.play("enemy_laser", volume_scale=0.55)

    def spawn_enemy(self):
        side = random.randint(0, 3)
        if side == 0:
            x, y = random.uniform(0, WIDTH), -30
        elif side == 1:
            x, y = WIDTH + 30, random.uniform(0, HEIGHT)
        elif side == 2:
            x, y = random.uniform(0, WIDTH), HEIGHT + 30
        else:
            x, y = -30, random.uniform(0, HEIGHT)

        kind = "chaser"
        if self.wave >= 2 and random.random() < 0.35:
            kind = "shooter"
        self.enemies.append(Enemy(x, y, kind))

    def start_wave(self):
        self.wave += 1
        self.enemies_to_spawn = 3 + self.wave * 2
        self.spawn_timer = 0
        self.audio.play("wave_start")

    def _fire_player(self):
        """Unified fire path — spawns bullets + muzzle flash + SFX."""
        if self.game_over or not self.player.alive:
            return
        if self.player.fire_cooldown > 0:
            return
        rate = 0.06 if self.rapid_timer > 0 else self.player.fire_rate
        self.player.fire_cooldown = rate
        self.bullets.extend(self.player.shoot())
        self.audio.play_laser()

        a = self.player.angle
        mx = self.player.x + math.cos(a) * 20
        my = self.player.y + math.sin(a) * 20

        # Bright muzzle flash (short life, large, white)
        self.particles.emit(mx, my, 6, (255, 255, 255),
                            speed=120, life=0.08, size=6, spread=0.9, direction=a)
        # Cyan sparks streaming forward
        self.particles.emit(mx, my, 8, NEON_CYAN,
                            speed=320, life=0.18, size=3, spread=0.5, direction=a)
        # A couple of pink backsparks (recoil feel)
        self.particles.emit(mx, my, 3, NEON_PINK,
                            speed=180, life=0.14, size=2, spread=1.2, direction=a + math.pi)

        self.camera.add_shake(1.2)

    def _cycle_ship_color(self):
        self.player.color_index = (self.player.color_index + 1) % len(self.player.color_palette)
        self.audio.play("ui_click")
        self.particles.emit(
            self.player.x, self.player.y, 10,
            self.player.color_palette[self.player.color_index][0],
            speed=180, life=0.3, size=3, spread=math.tau
        )

    def _apply_controller_input(self, dt):
        c = self.controller
        if not c.active:
            return

        mx, my = c.move_vector()
        if mx != 0.0 or my != 0.0:
            self.player.vx += mx * self.player.accel * dt
            self.player.vy += my * self.player.accel * dt

        ax, ay = c.aim_vector()
        if ax != 0.0 or ay != 0.0:
            self.player.angle = math.atan2(ay, ax)

        if c.fire_held():
            self._fire_player()

        dash_now = c.dash_pressed()
        if dash_now and not self._pad_dash_prev and not self.game_over:
            if self._input_lock_dash <= 0:
                if self.player.try_dash():
                    self.audio.play("dash")
                    self._input_lock_dash = 0.15
        self._pad_dash_prev = dash_now

        mute_now = c.mute_pressed()
        if mute_now and not self._pad_mute_prev:
            if self._input_lock_mute <= 0:
                muted = self.audio.toggle_mute()
                if not muted:
                    self.audio.play("ui_click")
                self._input_lock_mute = 0.15
        self._pad_mute_prev = mute_now

        # --- D-pad Up: cycle ship color ---
        dpup_now = c.dpup_pressed()
        if dpup_now and not self._pad_dpup_prev and not self.game_over:
            self._cycle_ship_color()
        self._pad_dpup_prev = dpup_now

        restart_now = c.restart_pressed()
        if restart_now and not self._pad_restart_prev and self.game_over:
            self.audio.unduck_music()
            self.audio.play("ui_click")
            self.reset()
        self._pad_restart_prev = restart_now

    def update(self, dt):
        if self._hit_sfx_cd > 0:
            self._hit_sfx_cd -= dt
        if self._input_lock_dash > 0:
            self._input_lock_dash -= dt
        if self._input_lock_mute > 0:
            self._input_lock_mute -= dt

        self.virtual_mouse = self._screen_to_virtual(*pygame.mouse.get_pos())
        if self.player:
            self.player.virtual_mouse = self.virtual_mouse

        if self.game_over:
            if self._gameover_timer > 0:
                self._gameover_timer -= dt
                if self._gameover_timer <= 0:
                    self.audio.play("game_over")
            self._apply_controller_input(dt)
            return

        self.time_survived += dt
        keys = pygame.key.get_pressed()
        self.player.handle_input(keys, dt)
        self._apply_controller_input(dt)
        self.player.update(dt)

        if pygame.mouse.get_pressed()[0]:
            self._fire_player()

        if self.enemies_to_spawn > 0:
            self.spawn_timer -= dt
            if self.spawn_timer <= 0:
                self.spawn_enemy()
                self.enemies_to_spawn -= 1
                self.spawn_timer = max(0.15, 0.6 - self.wave * 0.05)
        else:
            if not self.enemies:
                self.wave_timer -= dt
                if self.wave_timer <= 0:
                    self.start_wave()
                    self.wave_timer = 2.0
                    self.particles.emit(WIDTH // 2, HEIGHT // 2, 30, NEON_CYAN,
                                        speed=400, life=0.8, size=4)
                    self.camera.add_shake(6)

        for e in self.enemies:
            e.update(dt, self.player, self.spawn_bullet)

        for b in self.bullets:
            b.update(dt)
            if not b.alive:
                continue
            if b.friendly:
                for e in self.enemies:
                    if not e.alive:
                        continue
                    if vec_len(b.x - e.x, b.y - e.y) < e.radius + b.radius:
                        b.alive = False
                        self.particles.emit(b.x, b.y, 8, e.color, speed=260, life=0.4, size=3)
                        if e.hit(b.damage):
                            self.player.score += 10
                            self.particles.emit(e.x, e.y, 30, e.color, speed=380, life=0.8, size=4)
                            self.camera.add_shake(4)
                            self.audio.play_explosion()
                        break
            else:
                if vec_len(b.x - self.player.x, b.y - self.player.y) < self.player.radius + b.radius:
                    b.alive = False
                    if self.shield > 0:
                        self.shield -= 1
                        self.particles.emit(self.player.x, self.player.y, 20, NEON_CYAN,
                                            speed=300, life=0.5, size=3)
                        self.camera.add_shake(3)
                        self.audio.play("shield_hit")
                    elif self.player.take_damage() and self._hit_sfx_cd <= 0:
                        self._hit_sfx_cd = 0.1
                        self.particles.emit(self.player.x, self.player.y, 30, NEON_RED,
                                            speed=400, life=0.8, size=4)
                        self.camera.add_shake(10)
                        self.audio.play("player_hit")

        self.bullets = [b for b in self.bullets if b.alive]

        for e in self.enemies:
            if vec_len(e.x - self.player.x, e.y - self.player.y) < e.radius + self.player.radius:
                if self.shield > 0:
                    self.shield -= 1
                    self.audio.play("shield_hit")
                    self.particles.emit(self.player.x, self.player.y, 20, NEON_CYAN,
                                        speed=300, life=0.5, size=3)
                    e.hit(10)
                    self.audio.play_explosion()
                elif self.player.take_damage() and self._hit_sfx_cd <= 0:
                    self._hit_sfx_cd = 0.1
                    self.particles.emit(self.player.x, self.player.y, 25, NEON_RED,
                                        speed=400, life=0.8, size=4)
                    self.camera.add_shake(12)
                    self.audio.play("player_hit")
                    e.hit(10)
                    self.audio.play_explosion()

        for p in self.powerups:
            p.update(dt, self.player)
            if vec_len(p.x - self.player.x, p.y - self.player.y) < p.radius + self.player.radius:
                p.alive = False
                if p.kind == "hp":
                    self.player.hp = min(self.player.max_hp, self.player.hp + 1)
                    self.audio.play("heal")
                elif p.kind == "rapid":
                    self.rapid_timer = 8.0
                    self.audio.play("powerup")
                elif p.kind == "shield":
                    self.shield += 2
                    self.audio.play("shield")
                self.particles.emit(p.x, p.y, 20, NEON_GREEN, speed=300, life=0.6, size=3)
                self.player.score += 5
        self.powerups = [p for p in self.powerups if p.alive]

        new_enemies = []
        for e in self.enemies:
            if e.alive:
                new_enemies.append(e)
            else:
                if random.random() < 0.15:
                    kind = random.choice(["hp", "rapid", "shield"])
                    self.powerups.append(PowerUp(e.x, e.y, kind))
        self.enemies = new_enemies

        self.particles.update(dt)
        self.camera.update(dt)
        self.stars.update(dt, self.camera.offset())

        if self.rapid_timer > 0:
            self.rapid_timer -= dt

        if not self.player.alive:
            self.game_over = True
            self.audio.play("player_death")
            self.audio.duck_music(0.25)
            self._gameover_timer = 0.7
            self.particles.emit(self.player.x, self.player.y, 80, NEON_CYAN,
                                speed=600, life=1.4, size=5)
            self.camera.add_shake(20)

    def draw(self):
        self.screen.fill(BG_COLOR)
        ox, oy = self.camera.offset()
        self.stars.draw(self.screen)

        for i in range(0, WIDTH, 80):
            pygame.draw.line(self.screen, (15, 20, 40), (i, 0), (i, HEIGHT))
        for j in range(0, HEIGHT, 80):
            pygame.draw.line(self.screen, (15, 20, 40), (0, j), (WIDTH, j))

        world = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        self.particles.draw(world)
        for p in self.powerups:
            p.draw(world)
        for b in self.bullets:
            b.draw(world)
        for e in self.enemies:
            e.draw(world)
        if self.player.alive:
            self.player.draw(world)

        if self.shield > 0:
            r = self.player.radius + 10 + math.sin(pygame.time.get_ticks() * 0.01) * 2
            pygame.draw.circle(world, NEON_CYAN, (int(self.player.x), int(self.player.y)), int(r), 2)

        self.screen.blit(world, (ox, oy))
        self.draw_hud()
        if self.game_over:
            self.draw_game_over()

        # Scale the 1280x720 virtual surface into the real window (letterboxed).
        self.display.fill((0, 0, 0))
        if self._viewport_rect.size == (WIDTH, HEIGHT):
            self.display.blit(self.screen, self._viewport_rect.topleft)
        else:
            scaled = pygame.transform.scale(self.screen, self._viewport_rect.size)
            self.display.blit(scaled, self._viewport_rect.topleft)
        pygame.display.flip()

    def draw_hud(self):
        for i in range(self.player.max_hp):
            c = NEON_RED if i < self.player.hp else (40, 40, 40)
            pygame.draw.rect(self.screen, c, (20 + i * 26, 20, 20, 20), border_radius=4)
            pygame.draw.rect(self.screen, (100, 100, 100), (20 + i * 26, 20, 20, 20), 1, border_radius=4)

        s = self.font.render(f"SCORE: {self.player.score}", True, NEON_CYAN)
        self.screen.blit(s, (20, 55))

        w = self.font.render(f"WAVE: {self.wave}", True, NEON_PINK)
        self.screen.blit(w, (WIDTH - w.get_width() - 20, 20))

        t = self.small.render(f"TIME: {self.time_survived:.1f}s", True, (150, 150, 200))
        self.screen.blit(t, (WIDTH - t.get_width() - 20, 55))

        if self.player.dash_cooldown > 0:
            frac = 1 - self.player.dash_cooldown / 0.9
            pygame.draw.rect(self.screen, (40, 40, 40), (20, HEIGHT - 40, 120, 14), border_radius=6)
            pygame.draw.rect(self.screen, NEON_YELLOW, (20, HEIGHT - 40, int(120 * frac), 14), border_radius=6)
        else:
            pygame.draw.rect(self.screen, NEON_YELLOW, (20, HEIGHT - 40, 120, 14), border_radius=6)
        lbl = self.small.render("DASH [SPACE]", True, (220, 220, 220))
        self.screen.blit(lbl, (20, HEIGHT - 62))

        if self.rapid_timer > 0:
            r = self.small.render(f"RAPID FIRE: {self.rapid_timer:.1f}s", True, NEON_YELLOW)
            self.screen.blit(r, (WIDTH // 2 - r.get_width() // 2, HEIGHT - 40))

        if self.shield > 0:
            s = self.small.render(f"SHIELD: {self.shield}", True, NEON_CYAN)
            self.screen.blit(s, (WIDTH - s.get_width() - 20, HEIGHT - 40))

        if self.audio.muted:
            m = self.small.render("[MUTED - M]", True, (255, 100, 100))
            self.screen.blit(m, (WIDTH // 2 - m.get_width() // 2, HEIGHT - 40))

        if self.controller.active and self.controller.joystick is not None:
            cn = self.controller.joystick.get_name()[:24]
            c = self.tiny.render(f"PAD: {cn}", True, NEON_GREEN)
            self.screen.blit(c, (20, HEIGHT - 84))

        # Ship color indicator
        swatch = self.player.color_palette[self.player.color_index][0]
        pygame.draw.rect(self.screen, swatch, (WIDTH - 40, HEIGHT - 84, 20, 20), border_radius=4)
        pygame.draw.rect(self.screen, (100, 100, 100), (WIDTH - 40, HEIGHT - 84, 20, 20), 1, border_radius=4)
        cl = self.tiny.render("DPAD-UP: COLOR", True, (180, 180, 180))
        self.screen.blit(cl, (WIDTH - cl.get_width() - 20, HEIGHT - 108))

    def draw_game_over(self):
        overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self.screen.blit(overlay, (0, 0))

        title = self.font_big.render("GAME OVER", True, NEON_PINK)
        self.screen.blit(title, (WIDTH // 2 - title.get_width() // 2, HEIGHT // 2 - 100))

        score = self.font.render(f"FINAL SCORE: {self.player.score}", True, NEON_CYAN)
        self.screen.blit(score, (WIDTH // 2 - score.get_width() // 2, HEIGHT // 2 - 20))

        wave = self.font.render(f"WAVES SURVIVED: {self.wave}", True, NEON_YELLOW)
        self.screen.blit(wave, (WIDTH // 2 - wave.get_width() // 2, HEIGHT // 2 + 20))

        hint = "PRESS [R] OR START TO RESTART  |  [ESC] TO QUIT  |  [M] OR Y TO MUTE"
        restart = self.small.render(hint, True, (200, 200, 200))
        self.screen.blit(restart, (WIDTH // 2 - restart.get_width() // 2, HEIGHT // 2 + 90))

    def run(self):
        running = True
        while running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.05)

            for event in pygame.event.get():
                self.controller.handle_event(event)
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_F11:
                        self._toggle_fullscreen()
                    elif event.key == pygame.K_r and self.game_over:
                        self.audio.unduck_music()
                        self.audio.play("ui_click")
                        self.reset()
                    elif event.key == pygame.K_SPACE and not self.game_over:
                        if self._input_lock_dash <= 0:
                            if self.player.try_dash():
                                self.audio.play("dash")
                                self._input_lock_dash = 0.15
                    elif event.key == pygame.K_m:
                        if self._input_lock_mute <= 0:
                            muted = self.audio.toggle_mute()
                            if not muted:
                                self.audio.play("ui_click")
                            self._input_lock_mute = 0.15
                    elif event.key == pygame.K_c and not self.game_over:
                        self._cycle_ship_color()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        self._fire_player()

            self.update(dt)
            self.draw()

        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    Game().run()