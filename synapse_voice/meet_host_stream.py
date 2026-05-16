"""Host-side mic streamer for meet.subunit.ai meetings.

When the host kicks off a meeting in the Sonar Desktop, we:
  1. capture their mic via sounddevice (16 kHz mono PCM)
  2. pipe that PCM into a long-running ffmpeg subprocess that
     transcodes to WebM/Opus on the fly (matches what the PWA's
     MediaRecorder produces for guests)
  3. send WebM chunks over WebSocket to the same per-participant
     audio endpoint the guests use

That way the post-meeting pipeline sees a uniform set of streams —
host included — and the QR-Check-In name serves as the speaker label
exactly like for the guests.

The whole thing runs in background threads so the UI never blocks.
A `stop()` request lets the ffmpeg subprocess flush its container,
which keeps the final webm file valid for ffmpeg-decode on the server.
"""
from __future__ import annotations

import asyncio
import queue
import subprocess
import threading
from typing import Any, Optional

import numpy as np

# sounddevice is imported lazily inside `_open_mic` so the module can be
# imported on hosts without PortAudio (e.g. test runners, CI, server-side
# helpers). The Sonar Desktop, which is the only consumer, always has it.
sd: Any = None


_SAMPLE_RATE = 16000  # match recorder.py — matches Whisper's expectation
_CHUNK_FLUSH_BYTES = 4096  # ~1s of opus@32kbps; granular enough that ws.send is cheap


class HostStreamer:
    """Streams the local mic to wss://…/v1/meetings/<code>/audio/<token>.

    Two background threads (the audio capture callback runs on its own
    PortAudio thread, separate from these):
      • feeder    — reads PCM from `self._pcm_queue` and writes it to
                    ffmpeg.stdin
      • forwarder — reads WebM bytes from ffmpeg.stdout and pushes them
                    to the WebSocket via an asyncio loop on its thread.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        code: str,
        join_token: str,
        device: Optional[int] = None,
    ) -> None:
        # endpoint comes in as https://transcribe.subunit.ai or http://…
        # for local dev. We swap http(s)→ws(s) and append the path.
        ws_base = endpoint.rstrip("/").replace("https://", "wss://", 1)
        ws_base = ws_base.replace("http://", "ws://", 1)
        self._ws_url = f"{ws_base}/v1/meetings/{code}/audio/{join_token}"
        self._device = device

        self._pcm_queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=64)
        self._stream: Any = None  # sd.InputStream once _open_mic runs
        self._ffmpeg: Optional[subprocess.Popen] = None
        self._feeder_thr: Optional[threading.Thread] = None
        self._forwarder_thr: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._error: Optional[str] = None
        self._level = 0.0  # 0..1 RMS for UI; useful even when guests don't drive the orb

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        # Codex review v0.9.2 #3: mark running BEFORE the failure-prone
        # mic open so stop() actually tears down ffmpeg + threads if
        # the mic init explodes halfway through.
        self._running = True
        try:
            self._spawn_ffmpeg()
            self._spawn_feeder()
            self._spawn_forwarder()
            self._open_mic()
        except Exception:
            self.stop(timeout=2.0)
            raise

    def stop(self, timeout: float = 4.0) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        # Close the mic first so no more samples flood the queue.
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:  # noqa: BLE001
                print(f"[host-stream] mic close failed: {e}", flush=True)
            self._stream = None
        # Codex review v0.9.2 #4: a put_nowait(None) sentinel CAN be
        # dropped if the queue is full at stop-time. The feeder then
        # blocks on a queue.get() that never resolves and ffmpeg.stdin
        # never closes → truncated webm. Drain the queue first, then
        # use a blocking put so the sentinel always lands.
        try:
            while True:
                self._pcm_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._pcm_queue.put(None, timeout=1.0)
        except queue.Full:
            # Last-resort: shove an out-of-band poison via the feeder thread.
            try:
                if self._ffmpeg is not None and self._ffmpeg.stdin is not None:
                    self._ffmpeg.stdin.close()
            except Exception:
                pass
        if self._feeder_thr is not None:
            self._feeder_thr.join(timeout=timeout)
        # ffmpeg's webm muxer writes the closing tags on stdin EOF.
        if self._ffmpeg is not None:
            try:
                self._ffmpeg.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._ffmpeg.kill()
                self._ffmpeg.wait(timeout=1.0)
        if self._forwarder_thr is not None:
            self._forwarder_thr.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Public state for the UI
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def level(self) -> float:
        return self._level

    @property
    def error(self) -> Optional[str]:
        return self._error

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _spawn_ffmpeg(self) -> None:
        # Read raw float32 PCM at 16kHz mono from stdin, encode to Opus,
        # mux into a WebM container, write streamable webm to stdout.
        # `-flush_packets 1` keeps latency low so we don't buffer minutes
        # of audio before the first ws chunk goes out.
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "f32le", "-ar", str(_SAMPLE_RATE), "-ac", "1",
            "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "32k", "-application", "voip",
            "-f", "webm",
            "-flush_packets", "1",
            "pipe:1",
        ]
        try:
            self._ffmpeg = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as e:
            self._error = "ffmpeg binary not found — install ffmpeg to stream host audio"
            raise RuntimeError(self._error) from e

    def _spawn_feeder(self) -> None:
        def run() -> None:
            assert self._ffmpeg is not None and self._ffmpeg.stdin is not None
            try:
                while True:
                    item = self._pcm_queue.get()
                    if item is None:
                        break
                    try:
                        self._ffmpeg.stdin.write(item)
                    except (BrokenPipeError, OSError) as e:
                        print(f"[host-stream] feeder broken pipe: {e}", flush=True)
                        break
            finally:
                try:
                    self._ffmpeg.stdin.close()
                except Exception:
                    pass
        self._feeder_thr = threading.Thread(target=run, name="HostStreamFeeder", daemon=True)
        self._feeder_thr.start()

    def _spawn_forwarder(self) -> None:
        async def pump() -> None:
            import websockets
            try:
                async with websockets.connect(
                    self._ws_url,
                    max_size=None,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    assert self._ffmpeg is not None and self._ffmpeg.stdout is not None
                    loop = asyncio.get_running_loop()
                    while True:
                        # Read in a thread so we don't block the loop.
                        chunk = await loop.run_in_executor(
                            None, self._ffmpeg.stdout.read, _CHUNK_FLUSH_BYTES
                        )
                        if not chunk:
                            break
                        try:
                            await ws.send(chunk)
                        except Exception as e:  # noqa: BLE001
                            print(f"[host-stream] ws send failed: {e}", flush=True)
                            break
                    # Best-effort polite close so the server flushes the file.
                    try:
                        await ws.close()
                    except Exception:
                        pass
            except Exception as e:  # noqa: BLE001
                self._error = f"WebSocket error: {e}"
                print(f"[host-stream] {self._error}", flush=True)

        def run() -> None:
            try:
                asyncio.run(pump())
            except Exception as e:  # noqa: BLE001
                self._error = f"WebSocket pump fatal: {e}"
                print(f"[host-stream] {self._error}", flush=True)

        self._forwarder_thr = threading.Thread(target=run, name="HostStreamForwarder", daemon=True)
        self._forwarder_thr.start()

    def _open_mic(self) -> None:
        global sd
        if sd is None:
            import sounddevice as _sd  # lazy: PortAudio link only when needed
            sd = _sd

        def cb(indata, _frames, _time, status):  # noqa: ARG001
            if status:
                # Overflow / underflow are non-fatal; just log.
                print(f"[host-stream] mic status: {status}", flush=True)
            if self._stop_event.is_set():
                return
            try:
                # Lock-free: queue.put_nowait raises Full if backlog grows,
                # which we treat as a dropped frame rather than blocking
                # the audio callback (would cause underruns).
                self._pcm_queue.put_nowait(indata.tobytes())
            except queue.Full:
                pass
            # RMS for any UI that wants to render a level meter.
            try:
                rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
                self._level = min(1.0, rms * 4.0)
            except Exception:
                pass

        self._stream = sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=cb,
            device=self._device,
        )
        self._stream.start()
