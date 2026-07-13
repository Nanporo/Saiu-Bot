import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, timezone, timedelta
from modules.database import get_all_settings
from modules.cache_manager import load_cache
import logging
import math
from modules.location_matcher import town_mapping_cache

logger = logging.getLogger(__name__)

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371.0 # 地球半徑(公里)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

class AqiAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.alert_status = cache.get("aqi_status", {})  # 紀錄伺服器某測站當日是否已發送過預警
        self.check_aqi_loop.start()

    def save_state(self):
        return {"aqi_status": self.alert_status}

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('MOENV_API_KEY', '')
        except Exception:
            return ''

    def cog_unload(self):
        self.check_aqi_loop.cancel()

    @tasks.loop(minutes=30.0)
    async def check_aqi_loop(self):
        api_key = self.get_api_key()
        if not api_key: return

        try:
            settings = get_all_settings()
        except Exception: return

        # 若沒有伺服器設定空品預警，則不呼叫 API
        has_aqi_alerts = any('aqi_alerts' in d and d['aqi_alerts'] for d in settings.values())
        if not has_aqi_alerts: return

        url = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={api_key}"
        try:
            async with self.bot.session.get(url, ssl=False) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        records = data.get('records', [])
                    else:
                        records = data
                else:
                    return
        except Exception as e:
            logger.error(f"❌ 抓取 AQI 資料失敗 (Alert): {e}")
            return

        if not records: return

        # 將 API 回傳資料轉換為字典 (sitename -> record)
        aqi_data = {r.get('sitename'): r for r in records if r.get('sitename')}

        # 當前時間戳記
        now_ts = datetime.now(timezone(timedelta(hours=8))).timestamp()

        # 清理超過 24 小時的狀態，避免記憶體不斷增加
        keys_to_delete = []
        for k, ts in self.alert_status.items():
            # 兼容舊的 True/False 狀態，如果值是 bool 則轉換為 0 以觸發重新發送並蓋過
            if isinstance(ts, bool):
                ts = 0
            if now_ts - ts > 24 * 3600:
                keys_to_delete.append(k)
        for k in keys_to_delete:
            del self.alert_status[k]

        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            for loc_name, alert_info in d.get('aqi_alerts', {}).items():
                record = None
                nearest_msg = ""
                
                if loc_name in aqi_data:
                    record = aqi_data[loc_name]
                else:
                    # 嘗試利用經緯度尋找最近測站
                    matches = town_mapping_cache.get(loc_name, [])
                    target_lat = target_lon = None
                    for m in matches:
                        if m[0] == loc_name and m[1] is not None and m[2] is not None:
                            target_lat, target_lon = m[1], m[2]
                            break
                            
                    if target_lat and target_lon:
                        min_dist = float('inf')
                        for r in records:
                            try:
                                s_lat = float(r.get('latitude', 0))
                                s_lon = float(r.get('longitude', 0))
                                dist = haversine_dist(target_lat, target_lon, s_lat, s_lon)
                                if dist < min_dist:
                                    min_dist = dist
                                    record = r
                            except ValueError:
                                continue
                                
                        if record:
                            ref_sitename = record.get('sitename', '未知')
                            nearest_msg = f" (鄰近測站：{ref_sitename})"

                if not record:
                    continue

                try:
                    aqi_val = int(record.get('aqi', 0))
                except ValueError:
                    continue

                status_key_red = f"{guild_id}_{loc_name}_red"
                status_key_orange = f"{guild_id}_{loc_name}_orange"
                
                last_red = self.alert_status.get(status_key_red, 0)
                if isinstance(last_red, bool): last_red = 0
                
                last_orange = self.alert_status.get(status_key_orange, 0)
                if isinstance(last_orange, bool): last_orange = 0

                ch_id = alert_info.get('channel_id') if isinstance(alert_info, dict) else alert_info
                if not isinstance(ch_id, int): continue

                channel = self.bot.get_channel(ch_id)
                if not channel: continue

                if aqi_val > 150:
                    # 紅色警戒：距離上次紅害大於 8 小時
                    if now_ts - last_red > 8 * 3600:
                        content = "🔴 空氣品質不良預警 (對所有族群不健康)"
                        mention_role_id = d.get('aqi_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        embed = discord.Embed(title="", description=f"**{loc_name}** 當前空氣品質指標 (AQI){nearest_msg}：`🔴 {aqi_val}`\n建議留在室內並減少體力消耗活動，必要外出應配戴口罩。", color=discord.Color.red())
                        await channel.send(content=content, embed=embed, silent=global_silent)
                        logger.info(f"📢 [空品預警] 已發送紅害至 {channel.name} - {loc_name}")
                        self.alert_status[status_key_red] = now_ts
                        self.alert_status[status_key_orange] = now_ts # 發送紅害後，橘警時間也重置
                elif aqi_val > 100:
                    # 橘色警戒：距離上次橘警或紅害大於 8 小時
                    if now_ts - last_orange > 8 * 3600 and now_ts - last_red > 8 * 3600:
                        content = "🟠 空氣品質不良預警 (對敏感族群不健康)"
                        mention_role_id = d.get('aqi_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        embed = discord.Embed(title="", description=f"**{loc_name}** 當前空氣品質指標 (AQI){nearest_msg}：`🟠 {aqi_val}`\n敏感族群建議減少戶外劇烈活動。", color=discord.Color.orange())
                        await channel.send(content=content, embed=embed, silent=global_silent)
                        logger.info(f"📢 [空品預警] 已發送橘警至 {channel.name} - {loc_name}")
                        self.alert_status[status_key_orange] = now_ts

    @check_aqi_loop.before_loop
    async def before_check_aqi(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(AqiAlertCog(bot))
