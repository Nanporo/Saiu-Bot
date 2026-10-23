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

from modules.config import get_config
from modules.logging_utils import log_failure, log_recovery
from modules.ownercheck import is_owner


logger = logging.getLogger(__name__)

_config = get_config()
RTL_TCP_HOST: Final = str(_config.get("RTL_TCP_HOST") or "127.0.0.1")
RTL_TCP_PORT: Final = int(_config.get("RTL_TCP_PORT") or 1234)
IQ_SAMPLE_RATE: Final = 240_000
AUDIO_SAMPLE_RATE: Final = 48_000
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
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise ConnectionError("RTL-TCP connection closed during handshake")
            data.extend(chunk)
        return bytes(data)

    def _connect(self, station: RadioStation) -> socket.socket:
        connection = socket.create_connection((self.host, self.port), timeout=5)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1_048_576)
        connection.settimeout(2)
        header = self._recv_exact(connection, 12)
        if header[:4] != b"RTL0":
            connection.close()
            raise ConnectionError("RTL-TCP server returned an invalid handshake")

        # RTL-TCP command protocol: frequency, sample rate, automatic gain, AGC.
        connection.sendall(self._command(0x01, station.frequency_hz))
        connection.sendall(self._command(0x02, IQ_SAMPLE_RATE))
        connection.sendall(self._command(0x03, 0))
        connection.sendall(self._command(0x08, 1))
        return connection

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
        while not self._stopping.is_set():
            if not self._has_listeners():
                self._wake.wait(timeout=1)
                self._wake.clear()
                continue

            station = self.station
            try:
                connection = self._connect(station)
                with self._lock:
                    self._socket = connection
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
                self._wake.wait(timeout=2)
                self._wake.clear()

    def _receive_audio(self, connection: socket.socket, station: RadioStation) -> None:
        stream_started = time.monotonic()
        iq_remainder = b""
        pcm_buffer = bytearray()
        previous_i = 0.0
        previous_q = 0.0
        have_previous = False
        decimation_total = 0.0
        decimation_count = 0
        deemphasis = 0.0
        dc_average = 0.0
        deemphasis_alpha = (1 / AUDIO_SAMPLE_RATE) / (75e-6 + (1 / AUDIO_SAMPLE_RATE))
        deviation_radians = 2 * math.pi * 75_000 / IQ_SAMPLE_RATE

        while not self._stopping.is_set() and self._has_listeners():
            try:
                chunk = connection.recv(16_384)
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("RTL-TCP server closed the stream")

            data = iq_remainder + chunk
            even_length = len(data) - (len(data) % 2)
            iq_remainder = data[even_length:]
            samples = memoryview(data)[:even_length]

            for offset in range(0, even_length, 2):
                current_i = samples[offset] - 127.5
                current_q = samples[offset + 1] - 127.5
                if have_previous:
                    real = previous_i * current_i + previous_q * current_q
                    imaginary = previous_i * current_q - previous_q * current_i
                    decimation_total += math.atan2(imaginary, real)
                    decimation_count += 1
                else:
                    have_previous = True
                previous_i = current_i
                previous_q = current_q

                if decimation_count != IQ_SAMPLE_RATE // AUDIO_SAMPLE_RATE:
                    continue

                discriminator = decimation_total / decimation_count
                decimation_total = 0.0
                decimation_count = 0

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
        """回覆只有指令使用者可見的錯誤，並結束延遲回覆狀態。"""
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
            return
        try:
            # /收聽廣播一律以 ephemeral=True defer，因此編輯原始回覆可確保
            # 錯誤訊息不會出現在公開頻道。
            await interaction.edit_original_response(content=message, embed=None, view=None)
        except discord.NotFound:
            await interaction.followup.send(message, ephemeral=True)

    @staticmethod
    async def _send_public_success(
        interaction: discord.Interaction,
        *,
        content: str,
        embed: discord.Embed,
    ) -> None:
        """送出不引用私人 interaction 回覆的獨立公開訊息。"""
        channel = interaction.channel
        if channel is None:
            await FMRadioCog._send_private_error(
                interaction,
                "❌ 無法在目前頻道顯示廣播狀態。",
            )
            return

        try:
            # 公開 follow-up 會引用 ephemeral 原始回覆；其他使用者無權讀取
            # 該原始回覆時，Discord 便會顯示「無法載入訊息」。改由頻道直接
            # 發送，讓成功通知成為完全獨立的訊息。
            await channel.send(content=content, embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("⚠️ [FM] 無法送出公開廣播狀態：%r", exc)
            await FMRadioCog._send_private_error(
                interaction,
                "❌ 無法在目前頻道顯示廣播狀態。",
            )
            return

        try:
            await interaction.delete_original_response()
        except discord.NotFound:
            pass

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

        # Discord 要求約 3 秒內送出初次回應；先 defer，再執行任何檢查或
        # 語音連線工作，避免繁忙時 interaction token 逾時（10062）。延遲
        # 回覆設為私人，確保後續任何連線錯誤都只有指令使用者能看到。
        try:
            await interaction.response.defer(ephemeral=True)
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
