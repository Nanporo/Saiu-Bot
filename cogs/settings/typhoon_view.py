import discord
from cogs.settings.utils import load_settings, save_settings

class TargetChannelSelectForTyphoon(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="選擇新的發送頻道", min_values=1, max_values=1, row=0)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.settings['typhoon_alert'] = {'channel_id': self.values[0].id}
        view.all_settings[view.guild_id] = view.settings
        save_settings(view.all_settings)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

class RemoveTyphoonAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="停用颱風侵襲機率通知", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if 'typhoon_alert' in view.settings:
            del view.settings['typhoon_alert']
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

class TyphoonAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        
        self.add_item(TargetChannelSelectForTyphoon())
        if 'typhoon_alert' in self.settings:
            self.add_item(RemoveTyphoonAlertButton())
            
        back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🌀` 颱風侵襲設定", description="管理當前伺服器的颱風暴風圈侵襲機率自動通知頻道與狀態。", color=0x41809b)
        alert = self.settings.get('typhoon_alert')
        if alert:
            loc_name = alert.get('location_name', '未指定 (預設為臺北市)')
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            embed.add_field(name="發送頻道與地點", value=f"📍 {loc_name} - <#{alert['channel_id']}>", inline=False)
        else:
            embed.add_field(name="狀態", value="`🔴` 未設定", inline=False)
            embed.add_field(name="提示", value="請使用 `/加入颱風機率 <縣市>` 來啟用此功能。", inline=False)
        return embed

    async def back_callback(self, interaction: discord.Interaction):
        from cogs.settings.main import SettingsView
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass