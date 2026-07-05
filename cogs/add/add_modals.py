import discord
from modules.location_matcher import match_location
from modules.database import get_all_settings, save_all_settings

class EqModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="設定地震通知地點與震度")

    location = discord.ui.TextInput(
        label="請輸入縣市與鄉鎮市區",
        placeholder="例如：臺北市信義區...",
        required=True,
        max_length=20
    )
    
    min_intensity = discord.ui.TextInput(
        label="最低觸發震度 (請輸入數字 1~6)",
        placeholder="例如：3",
        default="3",
        required=True,
        max_length=1
    )

    async def on_submit(self, interaction: discord.Interaction):
        loc_val, error_msg = match_location(self.location.value)
        if error_msg:
            await interaction.response.send_message(content=error_msg, ephemeral=True)
            return

        try:
            min_int = int(self.min_intensity.value)
            if min_int < 1 or min_int > 6:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(content="❌ 最低觸發震度請輸入 1 到 6 之間的數字。", ephemeral=True)
            return
            
        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        
        settings = get_all_settings()
            
        if guild_id not in settings:
            settings[guild_id] = {}
            
        alerts = settings[guild_id].setdefault('eq_alerts', {})
        if len(alerts) >= 24 and loc_val not in alerts:
            await interaction.response.send_message(content="❌ 本伺服器已達到最多 24 個地震通知地點的上限！", ephemeral=True)
            return

        alerts[loc_val] = {
            'channel_id': channel_id,
            'min_magnitude': 5.5,
            'min_intensity': min_int
        }
        
        save_all_settings(settings)
            
        msg = f"✅ 已成功設定！當 **{loc_val}** 發生震度達 **{min_int}級** 以上的地震時，將會自動通知此頻道。"
        await interaction.response.edit_message(content=msg, view=None)

class EewModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="設定強震即時警報(Beta)")

    location = discord.ui.TextInput(
        label="請輸入縣市與鄉鎮市區",
        placeholder="例如：臺北市信義區...",
        required=True,
        max_length=20
    )
    
    min_intensity = discord.ui.TextInput(
        label="最低觸發預估震度 (數字 1~5)",
        placeholder="例如：3",
        default="3",
        required=False,
        max_length=1
    )
    
    min_magnitude = discord.ui.TextInput(
        label="最低觸發規模 (4.5~7.0 之間)",
        placeholder="例如：4.5",
        default="4.5",
        required=False,
        max_length=4
    )

    async def on_submit(self, interaction: discord.Interaction):
        loc_val, error_msg = match_location(self.location.value)
        if error_msg:
            await interaction.response.send_message(content=error_msg, ephemeral=True)
            return

        try:
            min_int = int(self.min_intensity.value)
            if min_int < 1 or min_int > 5:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(content="❌ 最低觸發震度請輸入 1 到 5 之間的數字。", ephemeral=True)
            return
            
        try:
            min_mag = float(self.min_magnitude.value) if self.min_magnitude.value else 4.5
            if min_mag < 4.5 or min_mag > 7.0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(content="❌ 規模請輸入 4.5 到 7.0 之間的有效數字。", ephemeral=True)
            return
            
        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        
        settings = get_all_settings()
        if guild_id not in settings:
            settings[guild_id] = {}
            
        if not settings[guild_id].get("eew_authorized", False):
            await interaction.response.send_message(content="❌ 本伺服器尚未獲得強震即時警報推播許可。請聯絡機器人擁有者。", ephemeral=True)
            return
            
        alerts = settings[guild_id].setdefault('eew_alerts', {})
        if len(alerts) >= 24 and loc_val not in alerts:
            await interaction.response.send_message(content="❌ 本伺服器已達到最多 24 個 EEW 通知地點的上限！", ephemeral=True)
            return

        alerts[loc_val] = {
            'channel_id': channel_id,
            'min_magnitude': min_mag,
            'min_intensity': min_int
        }
        
        save_all_settings(settings)
            
        msg = f"✅ 已成功設定！當 **{loc_val}** 預估震度達 **{min_int}級** 且規模達 **{min_mag}** 時，將會自動通知此頻道。"
        await interaction.response.edit_message(content=msg, view=None)

class TownModal(discord.ui.Modal):
    def __init__(self, alert_type):
        self.alert_type = alert_type
        super().__init__(title="設定鄉鎮市區")

        self.location = discord.ui.TextInput(
            label="請輸入縣市與鄉鎮市區",
            placeholder="例如：臺北市信義區、宜蘭縣羅東鎮...",
            required=True,
            max_length=20
        )
        self.add_item(self.location)

        if alert_type in ["rain", "flood"]:
            self.cooldown = discord.ui.TextInput(
                label="冷卻時間 (小時)",
                placeholder="例如：2 (代表2小時內不重複發送)",
                required=False,
                default="2",
                max_length=2
            )
            self.add_item(self.cooldown)

    async def on_submit(self, interaction: discord.Interaction):
        if self.alert_type == "cbs" and self.location.value == "全台接收":
            loc_val = "全台接收"
        else:
            loc_val, error_msg = match_location(self.location.value)
            if error_msg:
                await interaction.response.send_message(content=error_msg, ephemeral=True)
                return

        cooldown_seconds = 7200
        if self.alert_type in ["rain", "flood"]:
            cooldown_str = self.cooldown.value.strip()
            if cooldown_str:
                try:
                    cooldown_seconds = int(cooldown_str) * 3600
                except ValueError:
                    await interaction.response.send_message(content="❌ 冷卻時間請輸入有效的數字（小時）。", ephemeral=True)
                    return

        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        
        settings = get_all_settings()
            
        if guild_id not in settings:
            settings[guild_id] = {}
            
        if self.alert_type == "rain":
            rain_cog = interaction.client.get_cog("RainForecastCog")
            if not rain_cog:
                await interaction.response.send_message(content="❌ 降雨預報模組尚未載入，無法設定。", ephemeral=True)
                return
            grid_data, msg_or_loc = await rain_cog.get_location_grid(loc_val)
            if not grid_data:
                await interaction.response.send_message(content=msg_or_loc, ephemeral=True)
                return
            loc_val = msg_or_loc
            alerts = settings[guild_id].setdefault('rain_alerts', {})
            if len(alerts) >= 24 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 24 個降雨預警地點的上限！", ephemeral=True)
                return
            alerts[loc_val] = {
                'channel_id': channel_id,
                'grid_x': grid_data[0],
                'grid_y': grid_data[1],
                'cooldown_time': cooldown_seconds
            }
            msg = f"✅ 已成功將 **{loc_val}** 的降雨預警設定至此頻道！冷卻時間已設為 {cooldown_seconds // 3600} 小時。您可以繼續使用 /設定 來調整通知的時間段。"
        elif self.alert_type == "temp":
            alerts = settings[guild_id].setdefault('temp_alerts', {})
            if len(alerts) >= 24 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 24 個氣溫預警地點的上限！", ephemeral=True)
                return
            alerts[loc_val] = {'channel_id': channel_id}
            msg = f"✅ 已成功將 **{loc_val}** 的氣溫預警設定至此頻道！"
        elif self.alert_type == "flood":
            alerts = settings[guild_id].setdefault('flood_alerts', {})
            if len(alerts) >= 24 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 24 個淹水預警地點的上限！", ephemeral=True)
                return
            alerts[loc_val] = {
                'channel_id': channel_id,
                'cooldown_time': cooldown_seconds
            }
            msg = f"✅ 已成功將 **{loc_val}** 的淹水預警設定至此頻道！冷卻時間已設為 {cooldown_seconds // 3600} 小時。您可以繼續使用 /設定 來調整通知的時間段。"
        elif self.alert_type == "cbs":
            if isinstance(settings[guild_id].get('cbs_alerts'), list):
                old_list = settings[guild_id].pop('cbs_alerts')
                settings[guild_id]['cbs_alerts'] = {}
                if old_list:
                    settings[guild_id]['cbs_alerts']['全台接收'] = {'channel_id': old_list[0]}
            alerts = settings[guild_id].setdefault('cbs_alerts', {})
            if len(alerts) >= 24 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 24 個災防告警地點的上限！", ephemeral=True)
                return
            alerts[loc_val] = {'channel_id': channel_id}
            msg = f"✅ 已成功將 **{loc_val}** 的災防告警設定至此頻道！"

        save_all_settings(settings)
            
        await interaction.response.edit_message(content=msg, view=None)

async def setup(bot):
    pass
