import discord
from cogs.settings.settings_utils import load_settings, save_settings

class TargetLocationSelectForTyphoon(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="步驟一：選擇要更改頻道的預警地點", options=options, min_values=1, max_values=1, row=0)
        if current_target:
            for opt in self.options:
                if opt.value == current_target:
                    opt.default = True
                    
    async def callback(self, interaction: discord.Interaction):
        self.view.target_loc = self.values[0]
        new_view = TyphoonAlertSettingsView(self.view.guild_id, self.view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TargetChannelSelectForTyphoon(discord.ui.ChannelSelect):
    def __init__(self, disabled=True):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="步驟二：選擇新的發送頻道", min_values=1, max_values=1, row=1, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('typhoon_alerts', {})
        if view.target_loc in alerts:
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['channel_id'] = self.values[0].id
            else:
                alerts[view.target_loc] = {'channel_id': self.values[0].id}
            view.settings['typhoon_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = TyphoonAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class ThresholdSelectForTyphoon(discord.ui.Select):
    def __init__(self, disabled=True, current_threshold=70):
        options = [discord.SelectOption(label=f"機率達 {i}% 觸發", value=str(i), default=(i == current_threshold)) for i in range(10, 101, 10)]
        super().__init__(placeholder="步驟三：選擇觸發機率門檻", options=options, min_values=1, max_values=1, row=2, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('typhoon_alerts', {})
        if view.target_loc in alerts:
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['threshold'] = int(self.values[0])
            else:
                alerts[view.target_loc] = {'channel_id': alerts[view.target_loc], 'threshold': int(self.values[0])}
            view.settings['typhoon_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
            
        new_view = TyphoonAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveTyphoonAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)), row=3)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'typhoon_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['typhoon_alerts']:
                    del settings['typhoon_alerts'][loc_to_remove]
            if not settings['typhoon_alerts']:
                del settings['typhoon_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        target = view.target_loc if view.target_loc not in self.values else None
        new_view = TyphoonAlertSettingsView(view.guild_id, target)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TyphoonAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        
        if 'typhoon_alert' in self.settings:
            old = self.settings.pop('typhoon_alert')
            self.settings.setdefault('typhoon_alerts', {})[old.get('location_name', '臺北市')] = old
            self.all_settings[self.guild_id] = self.settings
            save_settings(self.all_settings)

        alerts = self.settings.get('typhoon_alerts', {})
        if alerts:
            loc_options = [discord.SelectOption(label=loc, value=loc) for loc in alerts.keys()][:25]
            self.add_item(TargetLocationSelectForTyphoon(loc_options, target_loc))
            self.add_item(TargetChannelSelectForTyphoon(disabled=(target_loc is None)))
            current_threshold = alerts.get(target_loc, {}).get('threshold', 70) if isinstance(alerts.get(target_loc), dict) else 70
            self.add_item(ThresholdSelectForTyphoon(disabled=(target_loc is None), current_threshold=current_threshold))
            remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
            self.add_item(RemoveTyphoonAlertSelect(remove_options))
            
        back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🌀` 颱風侵襲設定", description="管理當前伺服器的颱風暴風圈侵襲機率自動通知頻道與狀態。", color=0x41809b)
        alerts = self.settings.get('typhoon_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            for loc, data in alerts.items():
                ch_id = data.get('channel_id') if isinstance(data, dict) else data
                threshold = data.get('threshold', 70) if isinstance(data, dict) else 70
                embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>\n門檻：`{threshold}%`", inline=True)
        else:
            embed.add_field(name="狀態", value="`🔴` 未設定", inline=False)
            embed.add_field(name="提示", value="請使用 `/加入` 來啟用此功能。", inline=False)
        return embed

    async def back_callback(self, interaction: discord.Interaction):
        from cogs.settings.settings_main import SettingsView
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass