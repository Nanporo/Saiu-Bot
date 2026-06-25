import discord
from cogs.settings.settings_utils import load_settings, save_settings

class BotSettingsView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {"auto_push": False, "target_channel_ids": [], "allow_all_users_settings": False, "allow_all_users_join": False, "global_silent": False})
        
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
                    elif option.value == "allow_all_users_settings":
                        option.default = self.settings.get("allow_all_users_settings", False)
                    elif option.value == "allow_all_users_join":
                        option.default = self.settings.get("allow_all_users_join", False)
                    elif option.value == "global_silent":
                        option.default = self.settings.get("global_silent", False)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`🤖` 機器人設定",
            description="調整當前伺服器的機器人權限與系統廣播接收選項。",
            color=0x41809b
        )
        auto_push_status = "`🟢` 已啟用" if self.settings.get("auto_push") else "`🔴` 已停用"
        channel_ids = self.settings.get("target_channel_ids", [])
        channel_status = "\n".join([f"<#{c_id}>" for c_id in channel_ids]) if channel_ids else "⚠️ 尚未設定"
        
        allow_settings_status = "`🟢` 允許所有人" if self.settings.get("allow_all_users_settings") else "`🔴` 僅限管理員"
        allow_join_status = "`🟢` 允許所有人" if self.settings.get("allow_all_users_join") else "`🔴` 僅限管理員"
        global_silent_status = "`🟢` 已啟用" if self.settings.get("global_silent") else "`🔴` 已停用"
        
        embed.add_field(name="接收系統廣播", value=auto_push_status, inline=True)
        embed.add_field(name="/設定 指令權限", value=allow_settings_status, inline=True)
        embed.add_field(name="/加入 指令權限", value=allow_join_status, inline=True)
        embed.add_field(name="全局靜音通知", value=global_silent_status, inline=True)
        embed.add_field(name="廣播目標頻道", value=channel_status, inline=False)
        return embed

    @discord.ui.select(
        placeholder="點此開啟或關閉功能",
        min_values=0,
        max_values=4,
        options=[
            discord.SelectOption(label="接收系統廣播", value="auto_push", description="允許接收擁有者發送的系統廣播", emoji="📢"),
            discord.SelectOption(label="開放 /設定 指令權限", value="allow_all_users_settings", description="允許伺服器所有成員使用 /設定 指令", emoji="🔓"),
            discord.SelectOption(label="開放 /加入 指令權限", value="allow_all_users_join", description="允許伺服器所有成員使用 /加入 指令", emoji="🔓"),
            discord.SelectOption(label="全局靜音通知", value="global_silent", description="所有自動預警改為靜音發送", emoji="🔕")
        ],
        row=0
    )
    async def toggle_switches(self, interaction: discord.Interaction, select: discord.ui.Select):
        # 防呆機制：如果一般用戶被授予權限進來設定面板，要阻止他修改這項設定，必須由管理員來控制
        if not interaction.user.guild_permissions.administrator:
            setting_changed = ("allow_all_users_settings" in select.values) != self.settings.get("allow_all_users_settings", False)
            join_changed = ("allow_all_users_join" in select.values) != self.settings.get("allow_all_users_join", False)
            if setting_changed or join_changed:
                await interaction.response.send_message("❌ 只有伺服器管理員才能修改指令的權限狀態！", ephemeral=True)
                for option in select.options:
                    if option.value == "allow_all_users_settings":
                        option.default = self.settings.get("allow_all_users_settings", False)
                    elif option.value == "allow_all_users_join":
                        option.default = self.settings.get("allow_all_users_join", False)
                    elif option.value == "global_silent":
                        option.default = self.settings.get("global_silent", False)
                await interaction.message.edit(view=self)
                return

        self.settings["auto_push"] = "auto_push" in select.values
        self.settings["allow_all_users_settings"] = "allow_all_users_settings" in select.values
        self.settings["allow_all_users_join"] = "allow_all_users_join" in select.values
        self.settings["global_silent"] = "global_silent" in select.values
        
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        for option in select.options:
            option.default = option.value in select.values
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="選擇廣播目標頻道 (可多選，將覆蓋原設定)", min_values=0, max_values=25, row=1)
    async def select_target_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.settings["target_channel_ids"] = [c.id for c in select.values]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.settings.settings_main import SettingsView
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass