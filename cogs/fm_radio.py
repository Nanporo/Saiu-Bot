"""Discord FM radio commands backed by a shared RTL-TCP receiver."""

from __future__ import annotations

import asyncio
import logging
import math
import queue
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

try:
    import numpy as np
except ImportError:  # pragma: no cover - production dependency check
    np = None

from modules.config import get_config
from modules.logging_utils import log_failure, log_recovery
from modules.ownercheck import is_owner


logger = logging.getLogger(__name__)

_config = get_config()
RTL_TCP_HOST: Final = str(_config.get("RTL_TCP_HOST") or "127.0.0.1")
RTL_TCP_PORT: Final = int(_config.get("RTL_TCP_PORT") or 1234)
IQ_SAMPLE_RATE: Final = 2_400_000
FM_DEMOD_SAMPLE_RATE: Final = 240_000
AUDIO_SAMPLE_RATE: Final = 48_000
IQ_DECIMATION: Final = IQ_SAMPLE_RATE // FM_DEMOD_SAMPLE_RATE
AUDIO_DECIMATION: Final = FM_DEMOD_SAMPLE_RATE // AUDIO_SAMPLE_RATE
RTL_TCP_RECV_BYTES: Final = 262_144
RTL_TCP_CONNECT_TIMEOUT: Final = 5.0
RTL_TCP_HANDSHAKE_TIMEOUT: Final = 10.0
RTL_TCP_STREAM_TIMEOUT: Final = 2.0
RTL_TCP_RETRY_MIN_SECONDS: Final = 2.0
RTL_TCP_RETRY_MAX_SECONDS: Final = 30.0
DISCORD_FRAME_BYTES: Final = 3_840  # 20 ms, 48 kHz, signed 16-bit stereo
SILENCE_FRAME: Final = bytes(DISCORD_FRAME_BYTES)
DISCORD_FRAME_MS: Final = 20
FM_RADIO_BUFFER_MS: Final = max(
    200,
    min(int(_config.get("FM_RADIO_BUFFER_MS") or 800), 3_000),
)
JITTER_BUFFER_FRAMES: Final = max(1, FM_RADIO_BUFFER_MS // DISCORD_FRAME_MS)
LISTENER_QUEUE_FRAMES: Final = JITTER_BUFFER_FRAMES + 25
UNDERRUN_GRACE_FRAMES: Final = 5
FRAME_WAIT_SECONDS: Final = 0.012
STREAM_RECOVERY_SECONDS: Final = 10.0
IDLE_DISCONNECT_SECONDS: Final = 180


@dataclass(frozen=True)
class RadioStation:
    label: str
    frequency_hz: int


STATIONS: Final = (
    RadioStation("FM 94.7 MHz 警察廣播電臺臺南分臺", 94_700_000),
    RadioStation("FM 101.3 MHz 漢聲廣播電臺嘉南轉播站網", 101_300_000),
    RadioStation("FM 104.9 MHz 警察廣播電臺全國治安交通網", 104_900_000),
    RadioStation("FM 107.3 MHz 漢聲廣播電臺高屏/南部轉播站網", 107_300_000),
    RadioStation("FM 107.7 MHz 國立教育廣播電臺", 107_700_000),
)
STATIONS_BY_LABEL: Final = {station.label: station for station in STATIONS}
DEFAULT_STATION: Final = STATIONS_BY_LABEL["FM 104.9 MHz 警察廣播電臺全國治安交通網"]

try:
    _owner_server_id = _config.OWNER_SERVER_ID
except Exception:
    _owner_server_id = 0
OWNER_GUILDS = [discord.Object(id=_owner_server_id)] if _owner_server_id else []


class RTLTCPSReceiver:
    """Receive RTL-TCP IQ samples and fan out demodulated FM audio frames."""

    def __init__(self, host: str, port: int, station: RadioStation):
        if np is None:
            raise RuntimeError("FM 2.4 MHz 解調需要安裝 NumPy")
        self.host = host
        self.port = port
        self._station = station
        self._listeners: set[queue.Queue[bytes]] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._retuning = threading.Event()
        self._socket: socket.socket | None = None
        self._has_connected = False
        self._connection_failed = False
        self._thread = threading.Thread(
            target=self._run,
            name="rtl-tcp-fm-radio",
            daemon=True,
        )
        self._thread.start()

    @property
    def station(self) -> RadioStation:
        with self._lock:
            return self._station

    def subscribe(self) -> queue.Queue[bytes]:
        listener: queue.Queue[bytes] = queue.Queue(maxsize=LISTENER_QUEUE_FRAMES)
        with self._lock:
            self._listeners.add(listener)
        self._wake.set()
        return listener

    def unsubscribe(self, listener: queue.Queue[bytes]) -> None:
        with self._lock:
            self._listeners.discard(listener)
        self._wake.set()

    def tune(self, station: RadioStation) -> None:
        with self._lock:
            if station == self._station:
                return
            self._station = station
            active_socket = self._socket
        # Closing the socket interrupts recv immediately; the worker reconnects
        # and configures the new frequency before distributing more audio.
        if active_socket is not None:
            self._retuning.set()
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                active_socket.close()
            except OSError:
                pass
        self._wake.set()

    def close(self) -> None:
        self._stopping.set()
        self._wake.set()
        with self._lock:
            active_socket = self._socket
        if active_socket is not None:
            try:
                active_socket.close()
            except OSError:
                pass
        self._thread.join(timeout=2)

    @staticmethod
    def _command(command: int, value: int) -> bytes:
        return struct.pack(">BI", command, value)

    @staticmethod
    def _recv_exact(connection: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            try:
                chunk = connection.recv(size - len(data))
            except socket.timeout as exc:
                raise TimeoutError(
                    f"RTL-TCP handshake timed out after {len(data)}/{size} bytes"
                ) from exc
            if not chunk:
                raise ConnectionError(
                    f"RTL-TCP connection closed during handshake "
                    f"({len(data)}/{size} bytes)"
                )
            data.extend(chunk)
        return bytes(data)

    def _connect(self, station: RadioStation) -> socket.socket:
        connection = socket.create_connection(
            (self.host, self.port),
            timeout=RTL_TCP_CONNECT_TIMEOUT,
        )
        try:
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4_194_304)
            connection.settimeout(RTL_TCP_HANDSHAKE_TIMEOUT)

            # TCP is full duplex. Send all receiver settings immediately instead of
            # waiting for the dongle-info header. Some Windows rtl_tcp builds start
            # their one-second USB worker watchdog as soon as they accept a client;
            # queuing the settings here ensures 2.4 MHz is applied as soon as the
            # command worker starts.
            commands = b"".join(
                (
                    self._command(0x02, IQ_SAMPLE_RATE),
                    self._command(0x01, station.frequency_hz),
                    self._command(0x03, 0),
                    self._command(0x08, 1),
                )
            )
            connection.sendall(commands)

            header = self._recv_exact(connection, 12)
            if header[:4] != b"RTL0":
                raise ConnectionError(
                    "RTL-TCP server returned an invalid handshake "
                    f"({header[:4].hex() or 'empty'})"
                )

            connection.settimeout(RTL_TCP_STREAM_TIMEOUT)
            return connection
        except Exception:
            connection.close()
            raise

    def _mark_stream_healthy(self, station: RadioStation) -> None:
        if self._connection_failed:
            log_recovery(
                logger,
                "rtl_tcp",
                "FM",
                f"RTL-TCP 已恢復連線 | 收聽 {station.label}",
            )
        elif not self._has_connected:
            logger.info(
                "📻 [FM] RTL-TCP 已連線 %s:%s | 收聽 %s",
                self.host,
                self.port,
                station.label,
            )
        self._has_connected = True
        self._connection_failed = False

    def _has_listeners(self) -> bool:
        with self._lock:
            return bool(self._listeners)

    def _broadcast(self, frame: bytes) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener.put_nowait(frame)
            except queue.Full:
                try:
                    listener.get_nowait()
                except queue.Empty:
                    pass
                try:
                    listener.put_nowait(frame)
                except queue.Full:
                    pass

    def _run(self) -> None:
        retry_delay = RTL_TCP_RETRY_MIN_SECONDS
        while not self._stopping.is_set():
            if not self._has_listeners():
                self._wake.wait(timeout=1)
                self._wake.clear()
                retry_delay = RTL_TCP_RETRY_MIN_SECONDS
                continue

            station = self.station
            stream_started: float | None = None
            planned_reconnect = False
            try:
                connection = self._connect(station)
                with self._lock:
                    self._socket = connection
                stream_started = time.monotonic()
                self._receive_audio(connection, station)
            except (ConnectionError, OSError, TimeoutError) as exc:
                planned_reconnect = self._retuning.is_set()
                self._retuning.clear()
                if not self._stopping.is_set() and not planned_reconnect:
                    log_failure(
                        logger,
                        "rtl_tcp",
                        "FM",
                        "RTL-TCP 串流中斷，將在背景持續重試",
                        exc,
                    )
                    self._connection_failed = True
            finally:
                with self._lock:
                    old_socket = self._socket
                    self._socket = None
                if old_socket is not None:
                    try:
                        old_socket.close()
                    except OSError:
                        pass

            if not self._stopping.is_set():
                if planned_reconnect:
                    retry_delay = RTL_TCP_RETRY_MIN_SECONDS
                    wait_seconds = 0.0
                elif (
                    stream_started is not None
                    and time.monotonic() - stream_started >= STREAM_RECOVERY_SECONDS
                ):
                    retry_delay = RTL_TCP_RETRY_MIN_SECONDS
                    wait_seconds = retry_delay
                else:
                    wait_seconds = retry_delay
                    retry_delay = min(
                        retry_delay * 2,
                        RTL_TCP_RETRY_MAX_SECONDS,
                    )
                self._wake.wait(timeout=wait_seconds)
                self._wake.clear()

    def _receive_audio(self, connection: socket.socket, station: RadioStation) -> None:
        if np is None:
            raise RuntimeError("FM 2.4 MHz 解調需要安裝 NumPy")

        stream_started = time.monotonic()
        iq_remainder = b""
        phase_remainder = np.empty(0, dtype=np.float32)
        pcm_buffer = bytearray()
        previous_iq = None
        deemphasis = 0.0
        dc_average = 0.0
        deemphasis_alpha = (1 / AUDIO_SAMPLE_RATE) / (75e-6 + (1 / AUDIO_SAMPLE_RATE))
        deviation_radians = 2 * math.pi * 75_000 / FM_DEMOD_SAMPLE_RATE

        while not self._stopping.is_set() and self._has_listeners():
            try:
                chunk = connection.recv(RTL_TCP_RECV_BYTES)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("RTL-TCP server closed the stream")

            data = iq_remainder + chunk
            iq_group_bytes = IQ_DECIMATION * 2
            usable_length = len(data) - (len(data) % iq_group_bytes)
            if not usable_length:
                iq_remainder = data
                continue
            iq_remainder = data[usable_length:]

            # RTL-TCP 以 2.4 MHz 送出交錯的 unsigned 8-bit IQ。使用 NumPy
            # 一次處理整個區塊，先用方框低通降至 240 kHz，再計算相位差。
            # 這可避免每秒執行 240 萬次 Python 迴圈而堵塞 TCP 接收端。
            iq = np.frombuffer(data, dtype=np.uint8, count=usable_length)
            iq = iq.reshape(-1, IQ_DECIMATION, 2).mean(axis=1, dtype=np.float32)
            complex_iq = (iq[:, 0] - 127.5) + 1j * (iq[:, 1] - 127.5)
            if previous_iq is not None:
                complex_iq = np.concatenate((previous_iq, complex_iq))
            previous_iq = complex_iq[-1:].copy()
            if complex_iq.size < 2:
                continue

            products = complex_iq[1:] * np.conjugate(complex_iq[:-1])
            phases = np.arctan2(products.imag, products.real).astype(np.float32, copy=False)
            if phase_remainder.size:
                phases = np.concatenate((phase_remainder, phases))

            phase_usable = phases.size - (phases.size % AUDIO_DECIMATION)
            if not phase_usable:
                phase_remainder = phases.copy()
                continue
            phase_remainder = phases[phase_usable:].copy()
            discriminators = phases[:phase_usable].reshape(-1, AUDIO_DECIMATION).mean(axis=1)

            for discriminator in discriminators.tolist():
                # 75 µs FM de-emphasis followed by a slow DC blocker.
                normalized = discriminator / deviation_radians
                deemphasis += deemphasis_alpha * (normalized - deemphasis)
                dc_average += 0.0005 * (deemphasis - dc_average)
                audio = max(-1.0, min(1.0, (deemphasis - dc_average) * 0.85))
                pcm_sample = int(audio * 32_767)
                pcm_buffer.extend(struct.pack("<hh", pcm_sample, pcm_sample))

                if len(pcm_buffer) >= DISCORD_FRAME_BYTES:
                    self._broadcast(bytes(pcm_buffer[:DISCORD_FRAME_BYTES]))
                    del pcm_buffer[:DISCORD_FRAME_BYTES]
                    if not self._has_connected:
                        if (
                            not self._connection_failed
                            or time.monotonic() - stream_started >= STREAM_RECOVERY_SECONDS
                        ):
                            self._mark_stream_healthy(station)
                    elif (
                        self._connection_failed
                        and time.monotonic() - stream_started >= STREAM_RECOVERY_SECONDS
                    ):
                        self._mark_stream_healthy(station)


class RadioAudioSource(discord.AudioSource):
    def __init__(self, receiver: RTLTCPSReceiver):
        self.receiver = receiver
        self.listener = receiver.subscribe()
        self._closed = False
        self._buffering = True
        self._consecutive_underruns = 0

    def read(self) -> bytes:
        if self._closed:
            return b""
        if self._buffering:
            if self.listener.qsize() < JITTER_BUFFER_FRAMES:
                return SILENCE_FRAME
            self._buffering = False
            self._consecutive_underruns = 0
        try:
            # RTL-TCP 可能以突發封包送達。短暫等待下一個音框，避免單一缺框
            # 就觸發完整重新緩衝，Discord 播放執行緒會在後續週期補回時間。
            frame = self.listener.get(timeout=FRAME_WAIT_SECONDS)
        except queue.Empty:
            self._consecutive_underruns += 1
            if self._consecutive_underruns >= UNDERRUN_GRACE_FRAMES:
                self._buffering = True
            return SILENCE_FRAME
        self._consecutive_underruns = 0
        return frame

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        if not self._closed:
            self._closed = True
            self.receiver.unsubscribe(self.listener)


class FMRadioCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.receiver = RTLTCPSReceiver(RTL_TCP_HOST, RTL_TCP_PORT, DEFAULT_STATION)
        self._idle_disconnect_tasks: dict[int, asyncio.Task[None]] = {}

    def cog_unload(self) -> None:
        for task in self._idle_disconnect_tasks.values():
            task.cancel()
        self._idle_disconnect_tasks.clear()
        for voice_client in tuple(self.bot.voice_clients):
            if voice_client.source and isinstance(voice_client.source, RadioAudioSource):
                voice_client.stop()
        self.receiver.close()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or before.channel == after.channel:
            return
        self._update_idle_disconnect(member.guild)

    def _cancel_idle_disconnect(self, guild_id: int) -> None:
        task = self._idle_disconnect_tasks.pop(guild_id, None)
        if task is not None:
            task.cancel()

    def _update_idle_disconnect(self, guild: discord.Guild) -> None:
        voice_client = guild.voice_client
        if (
            voice_client is None
            or not isinstance(voice_client.source, RadioAudioSource)
            or any(not member.bot for member in voice_client.channel.members)
        ):
            self._cancel_idle_disconnect(guild.id)
            return

        task = self._idle_disconnect_tasks.get(guild.id)
        if task is None or task.done():
            self._idle_disconnect_tasks[guild.id] = asyncio.create_task(
                self._disconnect_after_idle(guild.id, voice_client)
            )

    async def _disconnect_after_idle(
        self,
        guild_id: int,
        voice_client: discord.VoiceClient,
    ) -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
            guild = self.bot.get_guild(guild_id)
            if (
                guild is None
                or guild.voice_client is not voice_client
                or not isinstance(voice_client.source, RadioAudioSource)
                or any(not member.bot for member in voice_client.channel.members)
            ):
                return

            logger.info("📻 [FM] 伺服器 %s 頻道已無真人聽眾，結束廣播", guild_id)
            voice_client.stop()
            await voice_client.disconnect(force=True)
        except discord.HTTPException:
            logger.exception("❌ [FM] 伺服器 %s 閒置後離開語音頻道失敗", guild_id)
        finally:
            if self._idle_disconnect_tasks.get(guild_id) is asyncio.current_task():
                self._idle_disconnect_tasks.pop(guild_id, None)

    @staticmethod
    def _get_user_voice_channel(
        interaction: discord.Interaction,
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        voice_state = getattr(interaction.user, "voice", None)
        return voice_state.channel if voice_state else None

    @staticmethod
    def _can_join_and_speak(
        interaction: discord.Interaction,
        channel: discord.VoiceChannel | discord.StageChannel,
    ) -> bool:
        guild = interaction.guild
        bot_member = guild.me if guild else None
        permissions = channel.permissions_for(bot_member) if bot_member else None
        return bool(permissions and permissions.connect and permissions.speak)

    @staticmethod
    async def _send_private_error(
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        """回覆只有指令使用者可見的錯誤。"""
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
            return

        # 通過前置檢查後使用公開 defer，以便成功訊息保留「已使用指令」
        # 標籤。若後續連線才失敗，必須先移除公開等待訊息，再送私人錯誤。
        try:
            await interaction.delete_original_response()
        except discord.NotFound:
            pass
        await interaction.followup.send(message, ephemeral=True)

    @staticmethod
    async def _send_public_success(
        interaction: discord.Interaction,
        *,
        content: str,
        embed: discord.Embed,
    ) -> None:
        """編輯公開的原始回覆，保留 Discord 的「已使用指令」標籤。"""
        try:
            await interaction.edit_original_response(content=content, embed=embed, view=None)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("⚠️ [FM] 無法送出公開廣播狀態：%r", exc)
            await FMRadioCog._send_private_error(
                interaction,
                "❌ 無法在目前頻道顯示廣播狀態。",
            )

    @app_commands.command(name="收聽廣播", description="📻 開始或結束收聽廣播電台（預設為警廣，可能不定時切換來源） FM Radio")
    @app_commands.guild_only()
    @app_commands.describe(狀態="選擇開始或結束，請先加入語音頻道")
    @app_commands.choices(
        狀態=[
            app_commands.Choice(name="開始", value="start"),
            app_commands.Choice(name="結束", value="stop"),
        ]
    )
    async def listen_radio(
        self,
        interaction: discord.Interaction,
        狀態: app_commands.Choice[str],
    ) -> None:
        # 不在語音頻道的使用者不能操作開始或結束。
        channel = self._get_user_voice_channel(interaction)
        if channel is None:
            await self._send_private_error(interaction, "需要先加入語音頻道")
            return

        voice_client = interaction.guild.voice_client
        if 狀態.value == "stop":
            if (
                voice_client is None
                or not voice_client.is_connected()
                or not (voice_client.is_playing() or voice_client.is_paused())
                or not isinstance(voice_client.source, RadioAudioSource)
            ):
                await self._send_private_error(
                    interaction,
                    "❌ 目前機器人不在播放狀態！",
                )
                return
        elif not self._can_join_and_speak(interaction, channel):
            await self._send_private_error(
                interaction,
                "需要允許語音頻道加入和發言權限",
            )
            return

        # Discord 要求約 3 秒內送出初次回應。前置錯誤已用私人訊息攔截；
        # 通過後使用公開 defer，成功時編輯原始回覆，既避免語音連線期間
        # interaction token 逾時（10062），也保留「已使用指令」標籤。
        try:
            await interaction.response.defer()
        except discord.NotFound as exc:
            if exc.code == 10062:
                logger.warning("⚠️ [FM] 收聽廣播 interaction 已失效，略過本次指令")
                return
            raise

        source: RadioAudioSource | None = None

        if 狀態.value == "stop":
            self._cancel_idle_disconnect(interaction.guild_id)
            try:
                if voice_client is not None:
                    if voice_client.is_playing() or voice_client.is_paused():
                        voice_client.stop()
                    await voice_client.disconnect(force=True)
            except discord.Forbidden:
                await self._send_private_error(
                    interaction,
                    "需要允許語音頻道加入和發言權限",
                )
                return
            except (discord.ClientException, discord.HTTPException) as exc:
                logger.exception("❌ [FM] 無法結束語音播放")
                await self._send_private_error(
                    interaction,
                    f"❌ 無法結束播放廣播：{exc}",
                )
                return
            embed = discord.Embed(description="✅ FM 廣播電台已結束播放！", color=0x2A9683)
            await self._send_public_success(
                interaction,
                content="📻 收聽廣播",
                embed=embed,
            )
            return

        try:
            self._cancel_idle_disconnect(interaction.guild_id)
            if voice_client is None:
                voice_client = await channel.connect()
            elif voice_client.channel != channel:
                await voice_client.move_to(channel)

            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
            source = RadioAudioSource(self.receiver)
            voice_client.play(
                source,
                after=lambda error: self._after_playback(interaction.guild_id, error),
            )
            self._update_idle_disconnect(interaction.guild)
        except discord.Forbidden:
            if source is not None:
                source.cleanup()
            await self._send_private_error(
                interaction,
                "需要允許語音頻道加入和發言權限",
            )
            return
        except (
            discord.ClientException,
            discord.HTTPException,
            discord.opus.OpusNotLoaded,
            OSError,
            RuntimeError,
        ) as exc:
            if source is not None:
                source.cleanup()
            if voice_client is not None and voice_client.is_connected():
                try:
                    await voice_client.disconnect(force=True)
                except discord.HTTPException:
                    logger.warning("⚠️ [FM] 播放失敗後無法中斷語音連線", exc_info=True)
            logger.exception("❌ [FM] 無法開始語音播放")
            await self._send_private_error(
                interaction,
                f"❌ 無法開始播放廣播：{exc}",
            )
            return

        station = self.receiver.station
        embed = discord.Embed(
            description=f"🎧 正在為您播放：{station.label}",
            color=0x2A9683,
        )
        await self._send_public_success(
            interaction,
            content="📻 收聽廣播",
            embed=embed,
        )

    @app_commands.command(name="切換廣播來源", description="（限擁有者）切換 FM 廣播電臺")
    @app_commands.guilds(*OWNER_GUILDS)
    @app_commands.describe(電臺="要切換的 FM 廣播電臺")
    @app_commands.choices(
        電臺=[app_commands.Choice(name=station.label, value=station.label) for station in STATIONS]
    )
    async def switch_radio_source(
        self,
        interaction: discord.Interaction,
        電臺: app_commands.Choice[str],
    ) -> None:
        if not is_owner(interaction.user.id):
            await interaction.response.send_message(
                "❌ 你沒有權限使用此指令。", ephemeral=True
            )
            return

        station = STATIONS_BY_LABEL.get(電臺.value)
        if station is None:
            await interaction.response.send_message(
                "❌ 找不到指定的廣播電臺，請重新選擇。",
                ephemeral=True,
            )
            return
        self.receiver.tune(station)
        await interaction.response.send_message(
            f"✅ 已切換廣播來源為 **{station.label}**。",
            ephemeral=True,
        )

    @staticmethod
    def _after_playback(guild_id: int | None, error: Exception | None) -> None:
        if error:
            logger.error("❌ [FM] 伺服器 %s 語音播放中斷：%s", guild_id, error)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FMRadioCog(bot))
