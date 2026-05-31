import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import sys

# 引入在 cogs.typhoon 撰寫的共用邏輯
from cogs.typhoon import fetch_typhoon_data, get_typhoon_probabilities, TAIWAN_CITIES

class TyphoonAlarmCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_valid_time = None
        self.typhoon_alarm_task.start()
        
    def cog_unload(self):
        self.typhoon_alarm_task.cancel()
        
    @app_commands.command(name="加入颱風機率", description="在此頻道設定本地縣市，當發布最新的颱風暴風圈侵襲機率時自動通知")
    @app_commands.describe(location="請輸入縣市名稱（例如：臺北市、屏東縣）")
    @app_commands.default_permissions(manage_guild=True)
    async def set_typhoon_alert(self, interaction: discord.Interaction, location: str):
        await interaction.response.defer(ephemeral=True)

        location = location.replace("台", "臺").strip()
        valid_county = None
        for county in TAIWAN_CITIES.keys():
            if county in location:
                valid_county = county
                break
                
        if not valid_county:
            await interaction.followup.send("❌ 找不到符合的縣市，請輸入正確的縣市名稱（如：臺北市、宜蘭縣）。")
            return

        settings_path = 'guild_settings.json'
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            settings = {}

        guild_id = str(interaction.guild_id)
        if guild_id not in settings:
            settings[guild_id] = {}

        # 兼容舊版單一頻道設定，轉移為新字典架構
        if 'typhoon_alert' in settings[guild_id]:
            old_alert = settings[guild_id].pop('typhoon_alert')
            settings[guild_id].setdefault('typhoon_alerts', {})[old_alert.get('location_name', '臺北市')] = {'channel_id': old_alert['channel_id']}

        if 'typhoon_alerts' not in settings[guild_id]:
            settings[guild_id]['typhoon_alerts'] = {}
            
        if len(settings[guild_id]['typhoon_alerts']) >= 10:
            await interaction.followup.send("❌ 每個伺服器最多只能設定 10 個颱風通知地點。")
            return
            
        settings[guild_id]['typhoon_alerts'][valid_county] = {'channel_id': interaction.channel_id}

        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)

        await interaction.followup.send(f"✅ 已成功設定！\n未來當氣象署發布最新的颱風暴風圈侵襲機率（且 **{valid_county}** 機率達 75% 以上）時，將會自動通知此頻道。")

    # 每 2 小時檢查一次是否有更新
    @tasks.loop(hours=2)
    async def typhoon_alarm_task(self):
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            return

        has_alerts = any('typhoon_alerts' in d or 'typhoon_alert' in d for d in settings.values())
        if not has_alerts:
            return
            
        polygons, valid_time = await fetch_typhoon_data(self.bot.session)
        if not polygons or not valid_time:
            return
            
        # 如果發布時間與上次不同，代表有最新資料
        if valid_time != self.last_valid_time:
            self.last_valid_time = valid_time
            results = get_typhoon_probabilities(polygons)
            
            max_prob = results[0]['prob'] if results else 0
            
            # 只有在至少一個縣市機率大於等於 75% 時才發送警報，避免平時洗版
            if max_prob >= 75:
                for guild_id, d in settings.items():
                    alerts = d.get('typhoon_alerts', {})
                    if 'typhoon_alert' in d:
                        alerts[d['typhoon_alert'].get('location_name', '臺北市')] = {'channel_id': d['typhoon_alert']['channel_id']}
                        
                    for loc_name, alert_info in alerts.items():
                        loc_prob = next((r['prob'] for r in results if r['county'] == loc_name), 0)
                        
                        if loc_prob >= 75:
                            channel = self.bot.get_channel(int(alert_info['channel_id']))
                            if channel:
                                content = "🌀 颱風預警通知"
                                embed = discord.Embed(
                                    title="", 
                                    description=f"**{loc_name}** 的颱風暴風圈侵襲機率已達 `🔴 {loc_prob}%` 以上！\n使用 `/颱風侵襲機率` 查詢各地詳細機率，並提早做好防颱準備。", 
                                    color=discord.Color.red()
                                )
                                self.bot.loop.create_task(channel.send(content=content, embed=embed))

    @typhoon_alarm_task.before_loop
    async def before_task(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TyphoonAlarmCog(bot))