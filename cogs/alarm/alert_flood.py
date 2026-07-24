import discord
from discord.ext import commands, tasks
from discord import app_commands
import time
from datetime import datetime, timezone, timedelta
from modules.database import get_all_settings
from modules.cache_manager import load_cache
from modules.http_client import fetch_json
import logging

logger = logging.getLogger(__name__)

class FloodForecastCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.alert_status = cache.get("flood_status", {})
        self.latest_flood_data = []
        self.last_flood_status = None
        self.check_flood_loop.start()

    def save_state(self):
        return {"flood_status": self.alert_status}

    def cog_unload(self):
        self.check_flood_loop.cancel()

    async def fetch_all_stations(self):
        url = "https://sta.colife.org.tw/STA_WaterResource_v2/v1.0/Datastreams?$expand=Thing,Thing/Locations,Observations($orderby=phenomenonTime%20desc;$top=1)%20&$filter=Thing/properties/authority_type%20eq%20%27%E6%B0%B4%E5%88%A9%E7%BD%B2%27%20%20and%20substringof(%27Datastream_Category_type=%E6%B7%B9%E6%B0%B4%E6%84%9F%E6%B8%AC%E5%99%A8%27,description)%20and%20substringof(%27Datastream_Category=%E6%B7%B9%E6%B0%B4%E6%B7%B1%E5%BA%A6%27,description)%20&$count=true&$top=1000"
        try:
            data = await fetch_json(url)
            if self.last_flood_status not in (None, 200):
                logger.info("✅ [淹水預警] API 已恢復正常連線 (狀態碼: 200)")
            self.last_flood_status = 200
            self.latest_flood_data = data.get("value", [])
            return self.latest_flood_data
        except Exception as e:
            err_str = f"EXC_{type(e).__name__}"
            if self.last_flood_status != err_str:
                logger.error(f"⚠️ [淹水預警] 獲取資料失敗: {e!r}")
                self.last_flood_status = err_str
            return None

    def get_max_depth(self, loc_name: str, stations: list):
        """取得該鄉鎮市區所有測站的最大淹水深度與測站名稱"""
        town_name = loc_name[3:] if len(loc_name) > 3 else loc_name
        max_depth = 0.0
        max_station_name = ""
        found = False

        for st in stations:
            thing = st.get("Thing", {})
            props = thing.get("properties", {})
            st_name = props.get("stationName", "")
            
            # 簡單判斷：如果站名包含鄉鎮名稱
            if town_name in st_name:
                found = True
                obs = st.get("Observations", [])
                if obs:
                    try:
                        val = float(obs[0].get("result", 0))
                        if 2.0 <= val < 1000.0:
                            val = round(val, 1)
                            if val > max_depth:
                                max_depth = val
                                max_station_name = st_name
                    except ValueError:
                        pass
        return found, max_depth, max_station_name

    @tasks.loop(minutes=5.0)
    async def check_flood_loop(self):
        try:
            settings = get_all_settings()
            import json
            with open('config.json', 'r', encoding='utf-8') as f:
                api_key = json.load(f).get('CWA_API_KEY', '')
        except Exception:
            return

        stations = await self.fetch_all_stations()
        if not stations:
            return

        fetched_rainfall = False
        current_rainfall_data = None

        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            alerts = d.get('flood_alerts', {})
            
            for loc_name, alert_info in alerts.items():
                if not isinstance(alert_info, dict) or 'channel_id' not in alert_info:
                    continue

                status_key = f"{guild_id}_{loc_name}"
                found, max_depth, max_station_name = self.get_max_depth(loc_name, stations)
                
                # 如果該地區根本沒有測站，就不處理
                if not found:
                    continue
                    
                current_threshold = 0.0
                icon = "💧"
                color = discord.Color.blue()
                
                if max_depth >= 50.0:
                    current_threshold = 50.0
                    icon = "🔴"
                    color = discord.Color.red()
                elif max_depth >= 30.0:
                    current_threshold = 30.0
                    icon = "🟠"
                    color = discord.Color.orange()
                elif max_depth >= 10.0:
                    current_threshold = 10.0
                    icon = "🟡"
                    color = discord.Color.gold()
                elif max_depth >= 2.0:
                    current_threshold = 2.0
                    icon = "💧"
                    color = discord.Color.blue()

                prev_data = self.alert_status.get(status_key, {})
                if isinstance(prev_data, dict):
                    prev_threshold = prev_data.get('threshold', 0.0)
                    cooldown_until = prev_data.get('cooldown_until', 0.0)
                else:
                    prev_threshold = 0.0
                    cooldown_until = 0.0

                current_time = time.time()
                is_cooling_down = current_time < cooldown_until

                in_quiet_hours = False
                if 'notify_hours' in alert_info:
                    now_tw = datetime.now(timezone(timedelta(hours=8)))
                    if now_tw.hour not in alert_info['notify_hours']:
                        in_quiet_hours = True

                if current_threshold > 0.0:
                    # 淹水中：如果水深升級，或者剛開始淹水且不在冷卻中
                    if prev_threshold == 0.0 or current_threshold > prev_threshold:
                        if prev_threshold == 0.0 and is_cooling_down:
                            self.alert_status[status_key] = {"threshold": current_threshold, "cooldown_until": cooldown_until}
                            continue
                            
                        if not in_quiet_hours:
                            channel = self.bot.get_channel(alert_info['channel_id'])
                            if channel:
                                is_raining = True
                                if prev_threshold == 0.0 and api_key:
                                    if not fetched_rainfall:
                                        from modules.cwa_api import fetch_current_rainfall
                                        try:
                                            current_rainfall_data = await fetch_current_rainfall(self.bot.session, api_key)
                                        except Exception as e:
                                            logger.warning(f"⚠️ [淹水預警] 獲取實測雨量失敗: {e!r}")
                                        fetched_rainfall = True
                                        
                                    if current_rainfall_data is not None:
                                        actual_rain = 0.0
                                        for st in current_rainfall_data:
                                            geo_info = st.get('GeoInfo', {})
                                            if f"{geo_info.get('CountyName', '')}{geo_info.get('TownName', '')}" == loc_name:
                                                try:
                                                    val = float(st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99'))
                                                    if val > actual_rain:
                                                        actual_rain = val
                                                except ValueError:
                                                    pass
                                        if actual_rain <= 0.0:
                                            is_raining = False
                                
                                if not is_raining:
                                    logger.info(f"📢 [淹水預警] {loc_name} 雖測得積水，但無降雨紀錄，略過發送。")
                                    continue

                                message_content = "🌊 積淹水預警通知" if prev_threshold == 0.0 else "🌊 淹水深度加劇通知"
                                mention_role_id = d.get('flood_mention_role_id')
                                if mention_role_id:
                                    message_content += f" <@&{mention_role_id}>"
                                embed = discord.Embed(
                                    title="",
                                    description=f"**{loc_name}** 偵測到積淹水情況！\n最深測站：{max_station_name}\n淹水深度：`{icon} {max_depth} cm`",
                                    color=color
                                )
                                if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                                    logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                                else:
                                    await channel.send(content=message_content, embed=embed, silent=global_silent)
                                guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                                logger.info(f"📢 [淹水預警] 已發送至 {guild_name} ({channel.name}) - {loc_name}")

                        self.alert_status[status_key] = {"threshold": current_threshold, "cooldown_until": cooldown_until}
                    else:
                        # 維持淹水，不重複發送，但更新記錄的門檻
                        self.alert_status[status_key] = {"threshold": prev_threshold, "cooldown_until": cooldown_until}
                else:
                    # 水退了：原本有淹水，現在退到 0
                    if prev_threshold > 0.0:
                        cooldown_seconds = alert_info.get('cooldown_time', 7200)
                        if is_cooling_down:
                            self.alert_status[status_key] = {"threshold": 0.0, "cooldown_until": current_time + cooldown_seconds}
                        else:
                            if not in_quiet_hours:
                                channel = self.bot.get_channel(alert_info['channel_id'])
                                if channel:
                                    message_content = "✅ 淹水消退通知"
                                    embed = discord.Embed(
                                        title="",
                                        description=f"**{loc_name}** 的積淹水情況已經消退！",
                                        color=discord.Color.green()
                                    )
                                    if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                                        logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                                    else:
                                        await channel.send(content=message_content, embed=embed, silent=True)
                            self.alert_status[status_key] = {"threshold": 0.0, "cooldown_until": current_time + cooldown_seconds}

    @check_flood_loop.before_loop
    async def before_check_flood(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(FloodForecastCog(bot))
