import discord
from discord.ext import commands
from discord import app_commands
import json
import os

# 定義儲存各伺服器設定的檔案路徑
SETTINGS_FILE = 'guild_settings.json'

def load_settings():
    """讀取伺服器設定檔"""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(data):
    """寫入伺服器設定檔"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

class BroadcastSettingsView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {"auto_push": False, "target_channel_ids": []})
        
        # 兼容舊版設定檔
        if "target_channel_ids" not in self.settings:
            self.settings["target_channel_ids"] = []
            if self.settings.get("target_channel_id"):
                self.settings["target_channel_ids"].append(self.settings["target_channel_id"])
                
        # 初始化時，根據目前的設定狀態來決定下拉選單各選項的「預設打勾狀態」
        for child in self.children:
            if isinstance(child, discord.ui.Select) and child.placeholder == "點此開啟或關閉功能":
                for option in child.options:
                    if option.value == "auto_push":
                        option.default = self.settings.get("auto_push", False)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`📢` 系統廣播設定",
            description="調整當前伺服器的系統廣播接收與相關選項。",
            color=0x41809b
        )
        auto_push_status = "`🟢` 已啟用" if self.settings.get("auto_push") else "`🔴` 已停用"
        channel_ids = self.settings.get("target_channel_ids", [])
        channel_status = "\n".join([f"<#{c_id}>" for c_id in channel_ids]) if channel_ids else "⚠️ 尚未設定"
        
        embed.add_field(name="接收系統廣播", value=auto_push_status, inline=False)
        embed.add_field(name="廣播目標頻道", value=channel_status, inline=False)
        return embed

    @discord.ui.select(
        placeholder="點此開啟或關閉功能",
        min_values=0,
        max_values=1,
        options=[
            discord.SelectOption(label="接收系統廣播", value="auto_push", description="允許接收擁有者發送的系統廣播", emoji="📢")
        ],
        row=0
    )
    async def toggle_switches(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.settings["auto_push"] = "auto_push" in select.values
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        for option in select.options:
            option.default = option.value in select.values
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect, 
        channel_types=[discord.ChannelType.text], 
        placeholder="選擇廣播目標頻道 (可多選，將覆蓋原設定)", 
        min_values=0,
        max_values=25,
        row=1
    )
    async def select_target_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.settings["target_channel_ids"] = [c.id for c in select.values]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

class RemoveAlertSelect(discord.ui.Select):
    def __init__(self, view_instance, options):
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)), row=1)
        self.view_instance = view_instance
        
    async def callback(self, interaction: discord.Interaction):
        settings = self.view_instance.settings
        if 'rain_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['rain_alerts']:
                    del settings['rain_alerts'][loc_to_remove]
            if not settings['rain_alerts']:
                del settings['rain_alerts']
                
        self.view_instance.all_settings[self.view_instance.guild_id] = settings
        save_settings(self.view_instance.all_settings)
        
        # 重新整理面板
        new_view = RainAlertSettingsView(self.view_instance.guild_id)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RainAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        
        # 兼容舊版設定，轉移至新結構
        if 'rain_alert' in self.settings:
            old = self.settings.pop('rain_alert')
            self.settings.setdefault('rain_alerts', {})[old['location_name']] = {
                'channel_id': old['channel_id'],
                'grid_x': old['grid_x'],
                'grid_y': old['grid_y']
            }
            self.all_settings[self.guild_id] = self.settings
            save_settings(self.all_settings)

        alerts = self.settings.get('rain_alerts', {})
        
        # 若有設定預警地點，則動態加入解除選單
        if alerts:
            options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
            self.add_item(RemoveAlertSelect(self, options))
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`🌧️` 降雨預警設定",
            description="管理當前伺服器的降雨預警頻道與狀態。",
            color=0x41809b
        )
        alerts = self.settings.get('rain_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            for loc, data in alerts.items():
                embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{data['channel_id']}>", inline=True)
        else:
            embed.add_field(name="狀態", value="`🔴` 未設定", inline=False)
            embed.add_field(name="提示", value="請使用 `/設定降雨預警 <鄉鎮市區>` 來啟用此功能。", inline=False)
        return embed

    @discord.ui.select(
        cls=discord.ui.ChannelSelect, 
        channel_types=[discord.ChannelType.text], 
        placeholder="更改發送頻道 (將套用至所有地點)", 
        min_values=1, max_values=1, row=0
    )
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        alerts = self.settings.get('rain_alerts', {})
        if not alerts:
            await interaction.response.send_message("❌ 請先使用 `/設定降雨預警` 啟用功能後，再更改頻道！", ephemeral=True)
            return
            
        for loc in alerts:
            alerts[loc]['channel_id'] = select.values[0].id
            
        self.settings['rain_alerts'] = alerts
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        
        # 重新整理面板
        new_view = RainAlertSettingsView(self.guild_id)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

class SettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {"auto_push": False, "target_channel_ids": []})

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`⚙️` 伺服器設定",
            description="請從下方選單選擇要調整的項目。\n(點擊選單進入設定)",
            color=0x41809b
        )
        
        auto_push_status = "`🟢`已啟用" if self.settings.get("auto_push") else "`🔴`已停用"
        rain_status = "`🟢`已啟用" if ('rain_alerts' in self.settings or 'rain_alert' in self.settings) else "`🔴`已停用"
        
        embed.add_field(name="📢 系統廣播", value=f"{auto_push_status}", inline=True)
        embed.add_field(name="🌧️ 降雨預警", value=f"{rain_status}", inline=True)
        return embed

    @discord.ui.select(
        placeholder="請選擇要設定的項目",
        max_values=1,
        options=[
            discord.SelectOption(label="系統廣播設定", value="broadcast", description="設定接收擁有者廣播的頻道", emoji="📢"),
            discord.SelectOption(label="降雨預警設定", value="rain", description="管理降雨預警的發送頻道與狀態", emoji="🌧️")
        ],
        row=0
    )
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        """導航至不同的設定面板"""
        if select.values[0] == "broadcast":
            view = BroadcastSettingsView(self.guild_id)
            await interaction.response.edit_message(embed=view.build_embed(), view=view)
        elif select.values[0] == "rain":
            view = RainAlertSettingsView(self.guild_id)
            await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成", style=discord.ButtonStyle.success, row=1)
    async def close_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ **設定面板已關閉**", view=None)
        self.stop()

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="設定", description="（限管理員）調整伺服器的設定與廣播頻道")
    @app_commands.default_permissions(administrator=True) # 限管理員可用
    async def settings_command(self, interaction: discord.Interaction):
        # 確認指令是在伺服器內使用
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器當中使用。", ephemeral=True)
            return
            
        # 初始化 View 與 Embed
        view = SettingsView(interaction.guild.id)
        embed = view.build_embed()
        
        # 傳送設定面板 (設為 ephemeral=True 代表僅有呼叫的管理員能看見與操作)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))