import discord
from discord.ext import commands, tasks
import logging
import time
from modules.database import get_all_settings
from modules.cache_manager import load_cache

logger = logging.getLogger(__name__)

class Status(commands.Cog):
    """機器人 Discord 狀態 (Presence) 管理模組"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.current_status_text = None

        now = time.time()
        cache = load_cache()

        # Priority 1: EEW (3 分鐘)
        eew_until = cache.get("status_eew_until", 0.0)
        eew_text = cache.get("status_eew_text")
        if eew_text and eew_until > now:
            self.eew_text = eew_text
            self.eew_until = eew_until
        else:
            self.eew_text = None
            self.eew_until = 0.0

        # Priority 2: 地震報告 (15 分鐘)
        eq_until = cache.get("status_eq_report_until", 0.0)
        eq_text = cache.get("status_eq_report_text")
        if eq_text and eq_until > now:
            self.eq_report_text = eq_text
            self.eq_report_until = eq_until
        else:
            self.eq_report_text = None
            self.eq_report_until = 0.0

        # Priority 3: 大雷雨即時訊息 (持續至結束，多報每 30 秒輪播)
        saved_thunderstorms = cache.get("status_active_thunderstorms", [])
        self.active_thunderstorms = [
            a for a in saved_thunderstorms
            if isinstance(a, dict) and (a.get("end_timestamp", 0) > now or a.get("end_timestamp", 0) == 0)
        ]
        self.thunderstorm_carousel_index = cache.get("status_thunderstorm_carousel_index", 0)
        self.last_carousel_time = 0.0

        # Priority 4: 颱風警報
        self.typhoon_text = cache.get("status_typhoon_text")

        if self.eew_text or self.eq_report_text or self.active_thunderstorms or self.typhoon_text:
            logger.info(
                f"💾 [狀態] 已從快取復原機器人狀態 (EEW: {self.eew_text}, 地震報告: {self.eq_report_text}, "
                f"大雷雨: {len(self.active_thunderstorms)} 則, 颱風: {self.typhoon_text})"
            )

        self.status_loop.start()

    def save_state(self) -> dict:
        """將 Status 的當前狀態與到期時間戳儲存為字典供快取管理員寫入硬碟"""
        now = time.time()

        has_valid_eew = bool(self.eew_text and self.eew_until > now)
        has_valid_eq = bool(self.eq_report_text and self.eq_report_until > now)

        valid_thunderstorms = [
            a for a in self.active_thunderstorms
            if isinstance(a, dict) and (a.get("end_timestamp", 0) > now or a.get("end_timestamp", 0) == 0)
        ]

        return {
            "status_eew_text": self.eew_text if has_valid_eew else None,
            "status_eew_until": self.eew_until if has_valid_eew else 0.0,
            "status_eq_report_text": self.eq_report_text if has_valid_eq else None,
            "status_eq_report_until": self.eq_report_until if has_valid_eq else 0.0,
            "status_active_thunderstorms": valid_thunderstorms,
            "status_thunderstorm_carousel_index": self.thunderstorm_carousel_index,
            "status_typhoon_text": self.typhoon_text,
        }

    def cog_unload(self):
        self.status_loop.cancel()

    def set_eew_alert(self, location: str, mag: float, duration: int = 180):
        """設定 EEW 強震即時警報動態狀態 (優先級 1，預設 3 分鐘)"""
        self.eew_text = f"地震速報：{location} M{mag}"
        self.eew_until = time.time() + duration
        logger.info(f"🤖 [狀態] 收到 EEW 速報連動：「{self.eew_text}」(持續 {duration} 秒)")

    def set_eq_report(self, location: str, mag: float, duration: int = 900):
        """設定地震報告動態狀態 (優先級 2，預設 15 分鐘)"""
        self.eq_report_text = f"地震報告：{location} M{mag}"
        self.eq_report_until = time.time() + duration
        logger.info(f"🤖 [狀態] 收到地震報告連動：「{self.eq_report_text}」(持續 {duration} 秒)")

    def update_thunderstorm_alerts(self, alerts: list):
        """更新大雷雨即時訊息列表 (優先級 3)"""
        self.active_thunderstorms = alerts
        if self.thunderstorm_carousel_index >= len(alerts):
            self.thunderstorm_carousel_index = 0

    def update_typhoon_alert(self, text: str | None):
        """更新颱風警報狀態 (優先級 4)"""
        self.typhoon_text = text

    @tasks.loop(seconds=5.0)
    async def status_loop(self):
        now = time.time()

        try:
            settings = get_all_settings()
        except Exception:
            settings = {}

        target_status = None

        # 優先級 1：EEW (強震即時警報)
        has_eew_enabled = any(d.get("eew_authorized", False) and d.get("eew_alerts") for d in settings.values())
        if has_eew_enabled and self.eew_text and now < self.eew_until:
            target_status = self.eew_text

        # 優先級 2：地震報告
        if not target_status:
            has_eq_enabled = any('eq_alerts' in d and d['eq_alerts'] for d in settings.values())
            if has_eq_enabled and self.eq_report_text and now < self.eq_report_until:
                target_status = self.eq_report_text

        # 優先級 3：大雷雨即時訊息
        if not target_status:
            has_rain_enabled = any(d.get('thunderstorm_alert') and d.get('rain_alerts') for d in settings.values())
            if has_rain_enabled:
                valid_ts = [
                    a for a in self.active_thunderstorms
                    if a.get("end_timestamp", 0) > now or a.get("end_timestamp", 0) == 0
                ]
                if valid_ts:
                    if len(valid_ts) == 1:
                        target_status = valid_ts[0]["text"]
                    else:
                        # 多報輪播：每 30 秒自動更新切換
                        if now - self.last_carousel_time >= 30.0:
                            self.thunderstorm_carousel_index = (self.thunderstorm_carousel_index + 1) % len(valid_ts)
                            self.last_carousel_time = now
                        else:
                            if self.thunderstorm_carousel_index >= len(valid_ts):
                                self.thunderstorm_carousel_index = 0
                        target_status = valid_ts[self.thunderstorm_carousel_index]["text"]

        # 優先級 4：颱風警報
        if not target_status:
            has_typhoon_enabled = any('typhoon_alerts' in d and d['typhoon_alerts'] for d in settings.values())
            if has_typhoon_enabled and self.typhoon_text:
                target_status = self.typhoon_text

        # 優先級 5：一般顯示 (預設)
        if not target_status:
            target_status = "Discord 天氣小助手"

        # 狀態發生改變時更新 Discord Presence
        if target_status != self.current_status_text:
            try:
                activity = discord.CustomActivity(name=target_status)
                await self.bot.change_presence(activity=activity)
                logger.info(f"🤖 [狀態] 已將機器人 Discord 狀態更新為：「{target_status}」")
                self.current_status_text = target_status
            except Exception as e:
                logger.error(f"❌ [狀態] 設定狀態時發生錯誤: {e}")

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(Status(bot))

