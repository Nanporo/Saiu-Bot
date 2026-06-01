import discord
from discord.ext import commands
from discord import app_commands
import json
from location_matcher import match_location

COUNTIES = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣",
    "臺中市", "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市",
    "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣"
]

class CountySelect(discord.ui.Select):
    def __init__(self, alert_type):
        self.alert_type = alert_type
        options = [discord.SelectOption(label=c, value=c) for c in COUNTIES]
        super().__init__(placeholder="請選擇要通知的縣市...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        county = self.values[0]
        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            settings = {}
            
        if guild_id not in settings:
            settings[guild_id] = {}
            
        if self.alert_type == "suspension":
            settings[guild_id].setdefault('suspension_alerts', {})[county] = channel_id
            msg = f"✅ 已成功將 **{county}** 的停班課推播設定至此頻道！"
            
        elif self.alert_type == "typhoon":
            alerts = settings[guild_id].setdefault('typhoon_alerts', {})
            if len(alerts) >= 10:
                await interaction.response.edit_message(content="❌ 每個伺服器最多只能設定 10 個颱風通知地點。", view=None)
                return
            alerts[county] = {'channel_id': channel_id}
            msg = f"✅ 已成功設定！未來當發布 **{county}** 的颱風暴風圈侵襲機率達 75% 以上時，將會自動通知此頻道。"

        with open('guild_settings.json', 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
            
        await interaction.response.edit_message(content=msg, view=None)

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
            await interaction.response.edit_message(content=error_msg, view=None)
            return

        try:
            min_int = int(self.min_intensity.value)
            if min_int < 1 or min_int > 6:
                raise ValueError
        except ValueError:
            await interaction.response.edit_message(content="❌ 最低觸發震度請輸入 1 到 6 之間的數字。", view=None)
            return
            
        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            settings = {}
            
        if guild_id not in settings:
            settings[guild_id] = {}
            
        settings[guild_id].setdefault('eq_alerts', {})[loc_val] = {
            'channel_id': channel_id,
            'min_magnitude': 5.5,
            'min_intensity': min_int
        }
        
        with open('guild_settings.json', 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
            
        msg = f"✅ 已成功設定！當 **{loc_val}** 發生震度達 **{min_int}級** 以上的地震時，將會自動通知此頻道。"
        await interaction.response.edit_message(content=msg, view=None)

class TownModal(discord.ui.Modal):
    def __init__(self, alert_type):
        self.alert_type = alert_type
        super().__init__(title="設定鄉鎮市區")

    location = discord.ui.TextInput(
        label="請輸入縣市與鄉鎮市區",
        placeholder="例如：臺北市信義區、宜蘭縣羅東鎮...",
        required=True,
        max_length=20
    )

    async def on_submit(self, interaction: discord.Interaction):
        loc_val, error_msg = match_location(self.location.value)
        if error_msg:
            await interaction.response.edit_message(content=error_msg, view=None)
            return

        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            settings = {}
            
        if guild_id not in settings:
            settings[guild_id] = {}
            
        if self.alert_type == "rain":
            rain_cog = interaction.client.get_cog("RainForecastCog")
            if not rain_cog:
                await interaction.response.edit_message(content="❌ 降雨預報模組尚未載入，無法設定。", view=None)
                return
            grid_data, msg_or_loc = await rain_cog.get_location_grid(loc_val)
            if not grid_data:
                await interaction.response.edit_message(content=msg_or_loc, view=None)
                return
            loc_val = msg_or_loc
            settings[guild_id].setdefault('rain_alerts', {})[loc_val] = {
                'channel_id': channel_id,
                'grid_x': grid_data[0],
                'grid_y': grid_data[1]
            }
            msg = f"✅ 已成功將 **{loc_val}** 的降雨預警設定至此頻道！"
        elif self.alert_type == "temp":
            settings[guild_id].setdefault('temp_alerts', {})[loc_val] = {'channel_id': channel_id}
            msg = f"✅ 已成功將 **{loc_val}** 的氣溫預警設定至此頻道！"

        with open('guild_settings.json', 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
            
        await interaction.response.edit_message(content=msg, view=None)

class TownSetupButton(discord.ui.Button):
    def __init__(self, alert_type):
        super().__init__(label="點此輸入鄉鎮市區名稱", style=discord.ButtonStyle.primary, emoji="✍️")
        self.alert_type = alert_type

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TownModal(self.alert_type))

class EqSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="點此輸入地點與觸發條件", style=discord.ButtonStyle.primary, emoji="✍️")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EqModal())

class AlertSetupView(discord.ui.View):
    def __init__(self, alert_type):
        super().__init__(timeout=300)
        if alert_type in ["suspension", "typhoon"]:
            self.add_item(CountySelect(alert_type))
        elif alert_type == "earthquake":
            self.add_item(EqSetupButton())
        elif alert_type in ["rain", "temp"]:
            # 因為台灣鄉鎮市區高達368個，超過下拉選單的25個選項限制，改以「按鈕開啟填寫彈窗」實作
            self.add_item(TownSetupButton(alert_type))

class SettingsJoinCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="加入", description="在此頻道設定各種自動預警與推播通知")
    @app_commands.describe(alert_type="請選擇要設定的通知類型")
    @app_commands.choices(alert_type=[
        app_commands.Choice(name="🌧️ 降雨預警", value="rain"),
        app_commands.Choice(name="🌡️ 氣溫預警", value="temp"),
        app_commands.Choice(name="🏚️ 地震通知", value="earthquake"),
        app_commands.Choice(name="🌀 颱風機率", value="typhoon"),
        app_commands.Choice(name="🎒 停班課通知", value="suspension")
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def join_alert_command(self, interaction: discord.Interaction, alert_type: app_commands.Choice[str]):
        val = alert_type.value
        view = AlertSetupView(val)
        
        content = f"⚙️ **設定 {alert_type.name}**\n請透過下方的介面完成通知設定，設定過程僅有您可見："
        await interaction.response.send_message(content=content, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsJoinCog(bot))