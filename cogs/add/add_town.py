"""以鄉鎮市區加入降雨、氣溫、災防告警與空品通知。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView
from modules.location_matcher import match_location
from cogs.add.add_earthquake import EewModal
from cogs.add.add_hours import NotifyHoursSetupView
from cogs.add.add_rain import MinRainfallSetupView
from cogs.add.add_cbs import CBSTypesSetupView


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

        if alert_type == "rain":
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
        if self.alert_type == "rain":
            cooldown_str = self.cooldown.value.strip()
            if cooldown_str:
                try:
                    val = int(cooldown_str)
                    if val < 1 or val > 24:
                        await interaction.response.send_message(content="❌ 冷卻時間請輸入 1 至 24 小時之間的整數數字。", ephemeral=True)
                        return
                    cooldown_seconds = val * 3600
                except ValueError:
                    await interaction.response.send_message(content="❌ 冷卻時間請輸入有效的正整數數字（小時）。", ephemeral=True)
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
            if len(alerts) >= 20 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 20 個降雨預警地點的上限！", ephemeral=True)
                return
            alerts[loc_val] = {
                'channel_id': channel_id,
                'grid_x': grid_data[0],
                'grid_y': grid_data[1],
                'cooldown_time': cooldown_seconds,
                'min_rainfall': 1.0
            }
            msg = f"✅ 已成功將 **{loc_val}** 的降雨預警設定至此頻道！冷卻時間已設為 {cooldown_seconds // 3600} 小時。您可以繼續使用 `/設定` 來調整通知的時間段。"
        elif self.alert_type == "temp":
            alerts = settings[guild_id].setdefault('temp_alerts', {})
            if len(alerts) >= 20 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 20 個氣溫預警地點的上限！", ephemeral=True)
                return
            alerts[loc_val] = {'channel_id': channel_id}
            msg = f"✅ 已成功將 **{loc_val}** 的氣溫預警設定至此頻道！"
        elif self.alert_type == "cbs":
            if isinstance(settings[guild_id].get('cbs_alerts'), list):
                old_list = settings[guild_id].pop('cbs_alerts')
                settings[guild_id]['cbs_alerts'] = {}
                if old_list:
                    settings[guild_id]['cbs_alerts']['全台接收'] = {'channel_id': old_list[0]}
            alerts = settings[guild_id].setdefault('cbs_alerts', {})
            if len(alerts) >= 20 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 20 個災防告警地點的上限！", ephemeral=True)
                return
            if loc_val not in alerts or not isinstance(alerts[loc_val], dict):
                alerts[loc_val] = {
                    'channel_id': channel_id,
                    'receive_test': False,
                    'receive_mountain': False,
                    'allowed_types': []
                }
            else:
                alerts[loc_val]['channel_id'] = channel_id
            msg = f"✅ 已成功將 **{loc_val}** 的災防告警設定至此頻道！"
        elif self.alert_type == "aqi":
            alerts = settings[guild_id].setdefault('aqi_alerts', {})
            if len(alerts) >= 20 and loc_val not in alerts:
                await interaction.response.send_message(content="❌ 本伺服器已達到最多 20 個空氣品質預警地點的上限！", ephemeral=True)
                return
            alerts[loc_val] = {'channel_id': channel_id}
            msg = f"✅ 已成功將 **{loc_val}** 的空氣品質預警設定至此頻道！"

        save_all_settings(settings)

        if self.alert_type == "rain":
            msg += "\n\n💧 **請選擇最低預警雨量門檻：**\n當預估 1 小時累積雨量達到此門檻時才會發送預警通知（預設為 1.0 mm）："
            view = MinRainfallSetupView(guild_id, loc_val)
        elif self.alert_type == "temp":
            msg += "\n\n⏰ **請選擇允許通知的時段：**\n請在下方選單勾選允許發送預警的時段（預設為 24 小時全時段）："
            view = NotifyHoursSetupView(guild_id, self.alert_type, loc_val)
        elif self.alert_type == "cbs":
            msg += "\n\n🚨 **請選擇要推送的警報種類：**\n請在下方選單勾選允許推播的警報種類（可多選，若不勾選則代表全部接收）："
            view = CBSTypesSetupView(guild_id, loc_val)
        else:
            view = RoleSetupView(self.alert_type)
            msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)


class TownSetupButton(discord.ui.Button):
    def __init__(self, alert_type):
        label = "點此輸入地點 (可填寫: 全台接收)" if alert_type in ["cbs", "eew"] else "點此輸入鄉鎮市區名稱"
        super().__init__(label=label, style=discord.ButtonStyle.primary, emoji="✍️")
        self.alert_type = alert_type

    async def callback(self, interaction: discord.Interaction):
        if self.alert_type == "eew":
            await interaction.response.send_modal(EewModal())
        else:
            await interaction.response.send_modal(TownModal(self.alert_type))


async def setup(bot):
    pass
