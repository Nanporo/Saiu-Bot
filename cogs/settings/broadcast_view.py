import discord
from cogs.settings.utils import load_settings, save_settings

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
        options=[discord.SelectOption(label="接收系統廣播", value="auto_push", description="允許接收擁有者發送的系統廣播", emoji="📢")],
        row=0
    )
    async def toggle_switches(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.settings["auto_push"] = "auto_push" in select.values
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

    @discord.ui.button(label="返回", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.settings.main import SettingsView
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass