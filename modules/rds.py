"""Streaming RDS1 receiver for 240 kHz FM multiplex samples.

The DSP pipeline follows the receive stages described at
https://pysdr.org/content/rds . All filters and synchronizers retain state
between calls; audio playback never waits for this decoder.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass, replace

try:
    import numpy as np
    from scipy import signal
except ImportError:  # RDS is optional; FM audio only needs NumPy.
    np = None
    signal = None

logger = logging.getLogger(__name__)
RDS_TIMEOUT = 60.0
RDS_PACKET_SAMPLES = 12_000  # 50 ms at 240 kHz
RDS_QUEUE_PACKETS = 160  # Eight seconds, independent of TCP packet boundaries
RDS_CONFIRM_SECONDS = 5.0

# IEC 62106 / EN 50067, Annex E, basic (G0) character repertoire.
_G0_HIGH = (
    "áàéèíìóòúùÑÇŞβ¡Ĳ"
    "âäêëîïôöûüñçşǧıĳ"
    "ªα©‰Ǧěňőπ€£$←↑→↓"
    "º¹²³±İńűµ¿÷°¼½¾§"
    "ÁÀÉÈÍÌÓÒÚÙŘČŠŽÐĿ"
    "ÂÄÊËÎÏÔÖÛÜřčšžđŀ"
    "ÃÅÆŒŷÝÕØÞŊŔĆŚŹŦð"
    "ãåæœŵýõøþŋŕćśźŧ "
)


def decode_text(data: bytes) -> str:
    chars = []
    for code in data:
        if code == 13:
            break
        if code >= 128:
            chars.append(_G0_HIGH[code - 128])
        elif code < 32 or code == 127:
            chars.append(" ")
        else:
            chars.append({36: "¤", 94: "―", 96: "‖", 126: "¯"}.get(code, chr(code)))
    return "".join(chars).strip()


@dataclass(frozen=True)
class RDSInfo:
    pi: int | None = None
    ps: str = ""
    radiotext: str = ""
    pty: int | None = None
    tp: bool | None = None
    ta: bool | None = None
    updated_at: float | None = None  # monotonic time of last validated group

    def fresh(self, now: float | None = None) -> bool:
        return self.updated_at is not None and (
            (time.monotonic() if now is None else now) - self.updated_at < RDS_TIMEOUT
        )


@dataclass(frozen=True)
class RDSDiagnostics:
    invalid_pi: int | None = None
    invalid_pi_at: float | None = None
    queue_overflows: int = 0

    def recent_invalid_pi(self) -> bool:
        return self.invalid_pi_at is not None and time.monotonic() - self.invalid_pi_at < RDS_TIMEOUT


# Polynomial division of a complete 26-bit codeword gives the offset word.
_OFFSETS = (0x0FC, 0x198, 0x168, 0x1B4)


def _remainder(word: int) -> int:
    for bit in range(25, 9, -1):
        if word & (1 << bit):
            word ^= 0x5B9 << (bit - 10)
    return word


class RDSGroups:
    """Bit synchronization, CRC validation and group/text assembly."""

    def __init__(self):
        self.info = RDSInfo()
        self.diagnostics = RDSDiagnostics()
        self._bits: list[int] = []
        self._ps: dict[int, bytes] = {}
        self._rt: dict[int, bytes] = {}
        self._rt_key = None
        self._candidate = None

    def feed_bits(self, bits) -> None:
        self._bits.extend(int(bit) for bit in bits)
        # Require a complete A/B/C(or C')/D sequence, not isolated CRC hits.
        pos = 0
        while len(self._bits) - pos >= 104:
            words = []
            for block in range(4):
                word = 0
                start = pos + block * 26
                for bit in self._bits[start:start + 26]:
                    word = (word << 1) | bit
                expected = _OFFSETS[block]
                if block == 2 and words[1] & 0x0800:
                    expected = 0x350
                if _remainder(word) != expected:
                    break
                words.append(word >> 10)
            if len(words) == 4 and (not words[1] & 0x0800 or words[2] == words[0]):
                self._accept(words)
                pos += 104
            else:
                pos += 1
        del self._bits[:pos]

    def _accept(self, words) -> None:
        # Country identifier zero is unassigned. A CRC-valid block alone must
        # not publish PI 0000 or replace a known station on a single outlier.
        pi = words[0]
        if pi < 0x1000:
            self.diagnostics = replace(self.diagnostics, invalid_pi=pi, invalid_pi_at=time.monotonic())
            self._candidate = None
            return
        now = time.monotonic()
        if self.info.pi == pi and self.info.fresh(now):
            self._candidate = None
            self._assemble(words)
            return
        if (
            self._candidate is None
            or self._candidate[0][0] != pi
            or now - self._candidate[1] > RDS_CONFIRM_SECONDS
        ):
            self._candidate = (words, now)
            return
        first, _ = self._candidate
        self._candidate = None
        self._assemble(first)
        self._assemble(words)

    def _assemble(self, words) -> None:
        a, b, c, d = words
        now = time.monotonic()
        if self.info.pi != a or not self.info.fresh(now):
            self.info = RDSInfo(pi=a)
            self._ps.clear()
            self._rt.clear()
            self._rt_key = None
        self.info = replace(self.info, pty=(b >> 5) & 31, tp=bool(b & 0x400), updated_at=now)
        group = b >> 12
        version_b = bool(b & 0x800)
        if group == 0:
            segment = b & 3
            value = d.to_bytes(2, "big")
            if segment in self._ps and self._ps[segment] != value:
                self._ps.clear()
                self.info = replace(self.info, ps="")
            self._ps[segment] = value
            self.info = replace(self.info, ta=bool(b & 0x10))
            if len(self._ps) == 4:
                self.info = replace(self.info, ps=decode_text(b"".join(self._ps[i] for i in range(4))))
        elif group == 2:
            key = (version_b, bool(b & 0x10))
            if key != self._rt_key:
                self._rt_key = key
                self._rt.clear()
                self.info = replace(self.info, radiotext="")
            segment = b & 15
            value = d.to_bytes(2, "big") if version_b else c.to_bytes(2, "big") + d.to_bytes(2, "big")
            if segment in self._rt and self._rt[segment] != value:
                self._rt.clear()
                self.info = replace(self.info, radiotext="")
            self._rt[segment] = value
            contiguous = b""
            for i in range(16):
                if i not in self._rt:
                    break
                contiguous += self._rt[i]
                if 13 in contiguous or i == 15:
                    self.info = replace(self.info, radiotext=decode_text(contiguous))
                    break


class RDSDecoder:
    """Stateful MPX -> RDS decoder. Call only from a single worker."""

    def __init__(self):
        if signal is None:
            raise RuntimeError("RDS 解析需要 NumPy 與 SciPy")
        self._lowpass = signal.firwin(161, 3000, fs=240_000)
        self._resample_taps = signal.firwin(481, 1 / 24) * 19
        self.reset()

    def reset(self) -> None:
        self.groups = RDSGroups()
        self._alternate_groups = RDSGroups()
        self._sample_count = 0
        self._filter_state = np.zeros(160, dtype=np.complex128)
        self._resample_history = np.zeros(48, dtype=np.complex128)
        self._resample_pending = np.empty(0, dtype=np.complex128)
        self._carrier_phase = 0.0
        self._carrier_freq = 0.0
        self._power = 0.01
        self._matched_state = np.zeros(7)
        self._timing_buffer = np.empty(0)
        self._timing_pos = 8.0
        self._omega = 8.0
        self._last_chip = 0.0
        self._chip_count = 0
        self._last_bits = [None, None]

    @property
    def snapshot(self) -> RDSInfo:
        return max((self.groups.info, self._alternate_groups.info), key=lambda info: info.updated_at or 0)

    @property
    def diagnostics(self) -> RDSDiagnostics:
        return max(
            (self.groups.diagnostics, self._alternate_groups.diagnostics),
            key=lambda value: value.invalid_pi_at or 0,
        )

    def feed(self, samples) -> RDSInfo:
        x = np.asarray(samples, dtype=np.float64)
        if not x.size:
            return self.snapshot
        if not np.all(np.isfinite(x)):
            self.reset()
            return self.snapshot
        n = np.arange(x.size) + self._sample_count
        mixed = x * np.exp(-2j * np.pi * (57_000 / 240_000) * (n % 80))
        filtered, self._filter_state = signal.lfilter(
            self._lowpass, [1.0], mixed, zi=self._filter_state,
        )
        baseband = filtered[(-self._sample_count) % 10::10]
        self._sample_count += x.size
        pending = np.concatenate((self._resample_pending, baseband))
        count = pending.size // 24 * 24
        self._resample_pending = pending[count:].copy()
        if not count:
            return self.snapshot
        # 48 history samples cover the FIR support and align both rational
        # resampling phases (19/24); discard only causal history output.
        joined = np.concatenate((self._resample_history, pending[:count]))
        resampled = signal.upfirdn(self._resample_taps, joined, up=19, down=24)
        resampled = resampled[38:38 + count * 19 // 24]
        self._resample_history = joined[-48:].copy()
        # Costas carrier recovery at 19 kHz, before biphase matched filtering.
        real = np.empty(resampled.size)
        phase, freq, power = self._carrier_phase, self._carrier_freq, self._power
        for i, sample in enumerate(resampled):
            power += 0.001 * (abs(sample) ** 2 - power)
            z = sample * complex(math.cos(phase), -math.sin(phase)) / math.sqrt(max(power, 1e-10))
            error = max(-1.0, min(1.0, (1 if z.real >= 0 else -1) * z.imag))
            freq = max(-0.04, min(0.04, freq + 0.00002 * error))
            phase = (phase + freq + 0.01 * error) % (2 * math.pi)
            real[i] = z.real
        self._carrier_phase, self._carrier_freq, self._power = phase, freq, power
        # Integrate each biphase half-symbol. Recover the 2375 chip/s clock
        # with a Gardner detector; trying both pair phases resolves the
        # inherent half-symbol ambiguity using complete group CRCs.
        matched, self._matched_state = signal.lfilter(
            np.ones(8) / 8, [1.0], real, zi=self._matched_state,
        )
        buf = np.concatenate((self._timing_buffer, matched))
        pos = self._timing_pos
        bits = [[], []]

        def interpolate(position):
            index = int(position)
            fraction = position - index
            return buf[index] * (1 - fraction) + buf[index + 1] * fraction

        while pos + 1 < buf.size:
            chip = interpolate(pos)
            midpoint = interpolate(pos - self._omega / 2)
            error = max(-1.0, min(1.0, (self._last_chip - chip) * midpoint))
            self._omega = max(7.98, min(8.02, self._omega + 0.0001 * error))
            pair = self._chip_count % 2
            bit = int(self._last_chip - chip >= 0)
            if self._chip_count and self._last_bits[pair] is not None:
                bits[pair].append(bit ^ self._last_bits[pair])
            self._last_bits[pair] = bit
            self._last_chip = chip
            self._chip_count += 1
            pos += self._omega + 0.03 * error
        consumed = max(0, min(int(pos) - 8, buf.size))
        self._timing_buffer = buf[consumed:].copy()
        self._timing_pos = pos - consumed
        self.groups.feed_bits(bits[0])
        self._alternate_groups.feed_bits(bits[1])
        return self.snapshot


class RDSWorker:
    """Bounded, nonblocking MPX fanout with generation-safe snapshots."""

    def __init__(self):
        self.available = signal is not None
        self._lock = threading.Lock()
        self._generation = 0
        self._revision = 0
        self._info = RDSInfo()
        self._diagnostics = RDSDiagnostics()
        self._queue: queue.Queue = queue.Queue(maxsize=RDS_QUEUE_PACKETS)
        self._pending = None
        self._pending_count = 0
        self._overflow_count = 0
        self._stopping = threading.Event()
        self._thread = None
        if self.available:
            self._thread = threading.Thread(target=self._run, name="fm-rds", daemon=True)
            self._thread.start()
        else:
            logger.warning("📻 [FM] 缺少 SciPy，停用 RDS 解析")

    @property
    def snapshot(self) -> RDSInfo:
        with self._lock:
            return self._info if self._info.fresh() else RDSInfo()

    @property
    def diagnostics(self) -> RDSDiagnostics:
        with self._lock:
            return replace(self._diagnostics, queue_overflows=self._overflow_count)

    def reset(self) -> int:
        with self._lock:
            self._generation += 1
            self._revision += 1
            self._info = RDSInfo()
            self._pending_count = 0
            self._overflow_count = 0
            self._diagnostics = RDSDiagnostics()
            self._drain()
            return self._generation

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def submit(self, samples, generation: int) -> None:
        if not self.available or self._stopping.is_set():
            return
        # Accumulate complete 50 ms packets. TCP recv() can yield arbitrarily
        # small chunks; counting them as queue slots made the buffer shrink to
        # milliseconds and repeatedly reset the carrier/bit synchronizers.
        with self._lock:
            if generation != self._generation:
                return
            if self._pending is None:
                self._pending = np.empty(RDS_PACKET_SAMPLES, dtype=np.float32)
            start = 0
            while start < samples.size:
                count = min(RDS_PACKET_SAMPLES - self._pending_count, samples.size - start)
                self._pending[self._pending_count:self._pending_count + count] = samples[start:start + count]
                self._pending_count += count
                start += count
                if self._pending_count < RDS_PACKET_SAMPLES:
                    continue
                packet = self._pending.copy()
                self._pending_count = 0
                try:
                    self._queue.put_nowait((generation, self._revision, packet))
                except queue.Full:
                    self._revision += 1
                    self._overflow_count += 1
                    self._info = RDSInfo()
                    self._diagnostics = RDSDiagnostics()
                    self._drain()
                    self._queue.put_nowait((generation, self._revision, None))
                    if self._overflow_count == 1:
                        logger.warning("📻 [FM] RDS 解碼緩衝超過 8 秒，重新同步；請檢查網路突發延遲或 CPU 負載")
                    return

    def close(self) -> None:
        self._stopping.set()
        self.reset()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        decoder = None
        continuity = None
        failed = False
        while not self._stopping.is_set():
            try:
                item_generation, revision, samples = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    if (item_generation, revision) != (self._generation, self._revision):
                        continue
                if decoder is None:
                    decoder = RDSDecoder()
                if continuity != (item_generation, revision) or samples is None:
                    decoder.reset()
                    continuity = (item_generation, revision)
                if samples is None:
                    continue
                info = decoder.feed(samples)
                with self._lock:
                    if continuity == (self._generation, self._revision) and not self._stopping.is_set():
                        self._info = info
                        self._diagnostics = getattr(decoder, "diagnostics", RDSDiagnostics())
                failed = False
            except Exception:
                if not failed:
                    logger.exception("📻 [FM] RDS 解碼失敗，重新同步")
                failed = True
                decoder = None
                with self._lock:
                    if (item_generation, revision) == (self._generation, self._revision):
                        self._info = RDSInfo()
                        self._diagnostics = RDSDiagnostics()
