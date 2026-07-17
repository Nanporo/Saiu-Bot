import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import re
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

async def fetch_tvo_live(session):
    url = "https://tvo.ncree.narl.org.tw/decrypt/live"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    # 1. Fetch microearthquakes from HTML
    microearthquake_data = (None, None)
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                html = await resp.text()
                labels_match = re.search(r'labels:\[(.*?)\]', html)
                values_match = re.search(r'values:\[(.*?)\]', html)
                args_match = re.search(r'\}\((.*?)\)\);', html)
                
                if labels_match and values_match and args_match:
                    labels_str = labels_match.group(1).replace('"', '').replace('\\u002F', '/')
                    labels = labels_str.split(',')
                    
                    args_str = args_match.group(1)
                    args = args_str.split(',')
                    val_map = {
                        'a': args[0],
                        'b': args[1] if len(args) > 1 else 'null',
                        'c': args[2] if len(args) > 2 else 'false'
                    }
                    
                    values_str = values_match.group(1)
                    values = []
                    for v in values_str.split(','):
                        v = v.strip()
                        if v in val_map:
                            values.append(val_map[v])
                        else:
                            values.append(v)
                            
                    microearthquake_data = (labels, values)
    except Exception as e:
        logger.warning(f"⚠️ 獲取大屯火山即時資料失敗: {e}")
        
    # 2. Fetch other sensors from REST API concurrently
    api_base = "https://tvo.ncree.narl.org.tw/sys/api/MonitoringInformation/Load?MonitoringType={}&Page=1&Limit=1"
    
    sensor_types = [
        "溫泉水酸鹼度",
        "溫泉水導電度",
        "土壤氣",
        "地溫",
        "噴氣口",
        "地表變形"
    ]
    
    import urllib.parse
    import asyncio
    
    async def fetch_sensor(sensor_type):
        fetch_url = api_base.format(urllib.parse.quote(sensor_type))
        try:
            async with session.get(fetch_url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("count", 0) > 0 and data.get("data"):
                        return sensor_type, data["data"][0]
        except Exception as e:
            logger.warning(f"⚠️ 獲取大屯火山 {sensor_type} 失敗: {e}")
        return sensor_type, None

    sensor_results = await asyncio.gather(*(fetch_sensor(st) for st in sensor_types))
    sensor_dict = dict(sensor_results)
    
    return microearthquake_data, sensor_dict

class TVOCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="大屯火山監測", description="🌋 查詢大屯火山觀測站即時觀測資料 Volcano")
    async def tvo_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        microearthquake_data, sensor_dict = await fetch_tvo_live(self.bot.session)
        
        embed = discord.Embed(title="", color=0xe74c3c)
        embed.description = "**大屯火山觀測站即時觀測**\n"
        
        def to_discord_timestamp(date_str):
            if not date_str:
                return ""
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                ts = int(dt.timestamp())
                return f"<t:{ts}:D>"
            except Exception:
                pass
            try:
                month, day = map(int, date_str.split('/'))
                now = datetime.now(timezone(timedelta(hours=8)))
                dt = now.replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
                if dt > now:
                    dt = dt.replace(year=dt.year - 1)
                ts = int(dt.timestamp())
                return f"<t:{ts}:D>"
            except Exception:
                return date_str

        labels, values = microearthquake_data
        if labels and values and len(labels) == len(values):
            last_index = len(labels) - 1
            ts_str = to_discord_timestamp(labels[last_index])
            embed.add_field(
                name="🌋 微震監測", 
                value=f"總計 **{values[last_index]}** 個\n{ts_str}",
                inline=True
            )
        else:
            embed.add_field(name="🌋 微震監測", value="無資料", inline=True)

        def format_sensor(key, unit):
            data = sensor_dict.get(key)
            if data:
                date_str = data.get('monitoringDate', '')
                ts_str = to_discord_timestamp(date_str)
                val = data.get('monitoringValue')
                return f"**{val}** {unit}\n{ts_str}"
            return "無資料"

        embed.add_field(name="🧪 溫泉水酸鹼度", value=format_sensor("溫泉水酸鹼度", "pH"), inline=True)
        embed.add_field(name="⚡ 溫泉水導電度", value=format_sensor("溫泉水導電度", "μs/cm"), inline=True)
        embed.add_field(name="☁️ 土壤二氧化碳濃度", value=format_sensor("土壤氣", "%"), inline=True)
        embed.add_field(name="🌡️ 地溫", value=format_sensor("地溫", "°C"), inline=True)
        embed.add_field(name="♨️ 噴氣口溫度", value=format_sensor("噴氣口", "°C"), inline=True)
        
        # Crust deformation
        crust_data = sensor_dict.get("地表變形")
        if crust_data:
            ns_val = crust_data.get('monitoringValue')
            ew_val = crust_data.get('monitoringSecondValue')
            date_str = crust_data.get('monitoringDate', '')
            ts_str = to_discord_timestamp(date_str)
            embed.add_field(
                name="📐 地表變形（傾斜儀）",
                value=f"南北方向 **{ns_val}** μrad\n東西方向 **{ew_val}** μrad\n{ts_str}",
                inline=False
            )
        else:
            embed.add_field(name="📐 地表變形（傾斜儀）", value="無資料", inline=False)
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"大屯火山觀測站 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/tvo_logo.png")

        await interaction.followup.send(content="🌋 大屯火山監測資訊", embed=embed)

async def setup(bot):
    await bot.add_cog(TVOCog(bot))
