import discord
from cogs.settings.settings_utils import load_settings, save_settings

class TargetLocationSelectForEq(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="步驟一：選擇要更改頻道的預警地點", options=options, min_values=1, max_values=1, row=0)
        if current_target:
            for opt in self.options:
                if opt.value == current_target:
                    opt.default = True
                    
    async def callback(self, interaction: discord.Interaction):
        self.view.target_loc = self.values[0]
        new_view = EqAlertSettingsView(self.view.guild_id, self.view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TargetChannelSelectForEq(discord.ui.ChannelSelect):
    def __init__(self, disabled=True):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="步驟二：選擇新的發送頻道", min_values=1, max_values=1, row=1, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('eq_alerts', {})
        if view.target_loc in alerts:
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['channel_id'] = self.values[0].id
            else:
                alerts[view.target_loc] = {'channel_id': self.values[0].id, 'min_magnitude': 5.5, 'min_intensity': 3}
            view.settings['eq_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = EqAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class MinMagnitudeSelectForEq(discord.ui.Select):
    def __init__(self, current_mag=5.5):
        options = []
        for mag in [4.5, 5.0, 5.5, 6.0, 6.5]:
            options.append(discord.SelectOption(
                label=f"規模 ≥ {mag:.1f}", 
                value=str(mag), 
                default=(mag == current_mag)
            ))
        super().__init__(placeholder="步驟三：選擇最低地震規模", options=options, min_values=1, max_values=1, row=2)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('eq_alerts', {})
        if view.target_loc in alerts:
            if not isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc] = {'channel_id': alerts[view.target_loc], 'min_magnitude': 5.5, 'min_intensity': 3}
            alerts[view.target_loc]['min_magnitude'] = float(self.values[0])
            view.settings['eq_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = EqAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class MinIntensitySelectForEq(discord.ui.Select):
    def __init__(self, current_int=3):
        options = []
        for i in range(1, 7):
            options.append(discord.SelectOption(
                label=f"震度 ≥ {i}級", 
                value=str(i), 
                default=(i == current_int)
            ))
        super().__init__(placeholder="步驟四：選擇最低震度", options=options, min_values=1, max_values=1, row=3)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('eq_alerts', {})
        if view.target_loc in alerts:
            if not isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc] = {'channel_id': alerts[view.target_loc], 'min_magnitude': 5.5, 'min_intensity': 3}
            alerts[view.target_loc]['min_intensity'] = int(self.values[0])
            view.settings['eq_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = EqAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveCurrentEqAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="解除此地點預警", emoji="🗑️", row=4)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'eq_alerts' in settings and view.target_loc in settings['eq_alerts']:
            del settings['eq_alerts'][view.target_loc]
            if not settings['eq_alerts']:
                del settings['eq_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        new_view = EqAlertSettingsView(view.guild_id, None)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveEqAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)), row=1)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'eq_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['eq_alerts']:
                    del settings['eq_alerts'][loc_to_remove]
            if not settings['eq_alerts']:
                del settings['eq_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        target = view.target_loc if view.target_loc not in self.values else None
        new_view = EqAlertSettingsView(view.guild_id, target)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class EqAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})

        alerts = self.settings.get('eq_alerts', {})
        if alerts:
            loc_options = [discord.SelectOption(label=loc, value=loc) for loc in alerts.keys()][:25]
            self.add_item(TargetLocationSelectForEq(loc_options, target_loc))
            
            if target_loc and target_loc in alerts:
                self.add_item(TargetChannelSelectForEq(disabled=False))
                
                data = alerts[target_loc]
                if isinstance(data, dict):
                    curr_mag = data.get('min_magnitude', 5.5)
                    curr_int = data.get('min_intensity', 3)
                else:
                    curr_mag = 5.5
                    curr_int = 3
                    
                self.add_item(MinMagnitudeSelectForEq(current_mag=curr_mag))
                self.add_item(MinIntensitySelectForEq(current_int=curr_int))
                self.add_item(RemoveCurrentEqAlertButton())
                
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=4)
            else:
                remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
                self.add_item(RemoveEqAlertSelect(remove_options))
                
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=2)
        else:
            back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=0)
            
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🏚️` 地震通知設定", description="管理當前伺服器的地震通知頻道與狀態。", color=0x41809b)
        alerts = self.settings.get('eq_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            for loc, data in alerts.items():
                if isinstance(data, dict):
                    ch_id = data.get('channel_id', '未知')
                    min_mag = data.get('min_magnitude', 5.5)
                    min_int = data.get('min_intensity', 3)
                else:
                    ch_id = data
                    min_mag, min_int = 5.5, 3
                embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>\n規模≥{min_mag} 且震度≥{min_int}級", inline=True)
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