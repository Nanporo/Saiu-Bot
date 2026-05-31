import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import re
from modules.town_mapping import load_town_mapping

class EarthquakeAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.processed_eqs = set()
        self.town_mapping = load_town_mapping()
        self.check_eq_loop.start()

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('CWA_API_KEY', '')
        except Exception:
            return ''

    def cog_unload(self):
        self.check_eq_loop.cancel()

    # 保留此函式供 earthquake_list.py 呼叫最新地震列表使用
    async def fetch_earthquakes(self):
        api_key = self.get_api_key()
        if not api_key: return []

        eqs = []
        datasets = ["E-A0015-001", "E-A0016-001"]
        
        async with aiohttp.ClientSession() as session:
            for ds in datasets:
                url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{ds}?Authorization={api_key}&limit=10&format=JSON"
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            records = data.get("records", {}).get("Earthquake", [])
                            eqs.extend(records)
                except Exception:
                    pass
        return eqs

    @app_commands.command(name="加入地震通知", description="當本地震度及規模達標時推送簡單的地震通知")
    @app_commands.describe(
        location="請輸入縣市與鄉鎮市區（例如：臺北市信義區）",
        min_magnitude="最低地震規模閾值（預設 5.5）",
        min_intensity="本地最低震度（預設 3 級）"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def set_eq_alert(self, interaction: discord.Interaction, location: str, min_magnitude: float = 5.5, min_intensity: int = 3):
        await interaction.response.defer(ephemeral=True)

        location_name = location.replace("台", "臺").strip()
        
        if location_name in self.town_mapping:
            matches = self.town_mapping[location_name]
            if len(matches) == 1:
                location_name = matches[0][0]
            else:
                options = "、".join([m[0] for m in matches])
                await interaction.followup.send(f"❌ 「{location}」有符合多個地點 ({options})，請提供更完整的名稱。")
                return
        elif "縣" not in location_name and "市" not in location_name:
            await interaction.followup.send("❌ 找不到該地點，請提供包含「縣市」與「鄉鎮市區」的完整名稱（例如：臺南市永康區）。")
            return

        settings_path = 'guild_settings.json'
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception: 
            settings = {}

        guild_id = str(interaction.guild_id)
        settings.setdefault(guild_id, {}).setdefault('eq_alerts', {})
        
        if len(settings[guild_id]['eq_alerts']) >= 10:
            await interaction.followup.send("❌ 每個伺服器最多只能設定 10 個地震預警地點。")
            return

        settings[guild_id]['eq_alerts'][location_name] = {
            'channel_id': interaction.channel_id,
            'min_magnitude': min_magnitude,
            'min_intensity': min_intensity
        }

        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)

        await interaction.followup.send(f"✅ 已成功加入地震通知：**{location_name}**！\n當地震規模達 `{min_magnitude}` 且本地震度達 `{min_intensity} 級` 時，將自動通知此頻道。")

    @tasks.loop(minutes=1.0)
    async def check_eq_loop(self):
        api_key = self.get_api_key()
        if not api_key: return

        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception: 
            return

        has_alerts = any('eq_alerts' in d and d['eq_alerts'] for d in settings.values())
        if not has_alerts: return

        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/E-A0015-005?Authorization={api_key}&downloadType=WEB&format=JSON"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return
                    data = await response.json(content_type=None)
        except Exception as e:
            print(f"⚠️ [地震通知] 抓取資料失敗: {e}")
            return

        cwa = data.get("cwaopendata", {})
        identifier = cwa.get("identifier")
        
        if not identifier or identifier in self.processed_eqs:
            return
            
        self.processed_eqs.add(identifier)
        # 避免記憶體無限增長
        if len(self.processed_eqs) > 100:
            self.processed_eqs.pop()

        eq = cwa.get("Earthquake", {})
        mag_val = eq.get("Magnitude", {}).get("MagnitudeValue", "0")
        try: 
            mag = float(mag_val)
        except ValueError: 
            mag = 0.0

        # 解析各鄉鎮的震度
        eq_intensities = {}
        for c in eq.get("Intensity", {}).get("County", []):
            county_name = c.get("CountyName", "")
            for t in c.get("Town", []):
                town_name = t.get("TownName", "")
                intensity_str = t.get("StationIntensity", "0級")
                match = re.search(r'\d+', str(intensity_str))
                if match:
                    fullname = f"{county_name}{town_name}"
                    eq_intensities[fullname] = max(eq_intensities.get(fullname, 0), int(match.group()))

        # 檢查各伺服器的通知條件
        for guild_id, d in settings.items():
            for loc_name, alert_info in d.get('eq_alerts', {}).items():
                if mag < alert_info.get('min_magnitude', 5.5):
                    continue
                    
                min_int = alert_info.get('min_intensity', 3)
                loc_intensity = eq_intensities.get(loc_name, 0)
                
                if loc_intensity >= min_int:
                    channel = self.bot.get_channel(alert_info['channel_id'])
                    if channel:
                        content = "🏚️ 地震通知"
                        embed = discord.Embed(
                            title="", 
                            description=f"剛才發生了規模{mag}的地震。\n**{loc_name} **震度{loc_intensity}級。", 
                            color=0xff3846
                        )
                        self.bot.loop.create_task(channel.send(content=content, embed=embed))

    @check_eq_loop.before_loop
    async def before_check_eq(self):
        await self.bot.wait_until_ready()
        api_key = self.get_api_key()
        if not api_key: return
            
        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/E-A0015-005?Authorization={api_key}&downloadType=WEB&format=JSON"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        identifier = data.get("cwaopendata", {}).get("identifier")
                        if identifier:
                            self.processed_eqs.add(identifier)
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(EarthquakeAlertCog(bot))