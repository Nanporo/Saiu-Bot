import discord
from cogs.settings.utils import load_settings, save_settings

class TargetLocationSelectForRain(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="步驟一：選擇要更改頻道的預警地點", options=options, min_values=1, max_values=1, row=0)
        if current_target:
            for opt in self.options:
                if opt.value == current_target:
                    opt.default = True
                    
    async def callback(self, interaction: discord.Interaction):
        self.view.target_loc = self.values[0]
        new_view = RainAlertSettingsView(self.view.guild_id, self.view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TargetChannelSelectForRain(discord.ui.ChannelSelect):
    def __init__(self, disabled=True):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="步驟二：選擇新的發送頻道", min_values=1, max_values=1, row=1, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('rain_alerts', {})
        if view.target_loc in alerts:
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['channel_id'] = self.values[0].id
            else:
                alerts[view.target_loc] = {'channel_id': self.values[0].id}
            view.settings['rain_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = RainAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class NotifyTimeSelectForRain(discord.ui.Select):
    def __init__(self, disabled=True, current_hours=None):
        if current_hours is None:
            current_hours = list(range(24))
            
        options = []
        for i in range(24):
            is_default = i in current_hours
            options.append(discord.SelectOption(
                label=f"{i:02d}:00 ~ {(i+1)%24:02d}:00",
                value=str(i),
                default=is_default
            ))
            
        super().__init__(
            placeholder="步驟三：選擇允許通知的時段 (可多選)", 
            options=options, 
            min_values=0, 
            max_values=24, 
            row=2, 
            disabled=disabled
        )
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('rain_alerts', {})
        if view.target_loc in alerts:
            selected_hours = [int(v) for v in self.values]
            
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['notify_hours'] = selected_hours
                
            view.settings['rain_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
            
        new_view = RainAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)), row=3)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'rain_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['rain_alerts']:
                    del settings['rain_alerts'][loc_to_remove]
            if not settings['rain_alerts']:
                del settings['rain_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        target = view.target_loc if view.target_loc not in self.values else None
        new_view = RainAlertSettingsView(view.guild_id, target)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RainAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        
        if 'rain_alert' in self.settings:
            old = self.settings.pop('rain_alert')
            self.settings.setdefault('rain_alerts', {})[old['location_name']] = old
            self.all_settings[self.guild_id] = self.settings
            save_settings(self.all_settings)

        alerts = self.settings.get('rain_alerts', {})
        if alerts:
            loc_options = [discord.SelectOption(label=loc, value=loc) for loc in alerts.keys()][:25]
            self.add_item(TargetLocationSelectForRain(loc_options, target_loc))
            
            if target_loc:
                self.add_item(TargetChannelSelectForRain(disabled=False))
                
                curr_hours = list(range(24))
                if target_loc in alerts and isinstance(alerts[target_loc], dict):
                    if 'notify_hours' in alerts[target_loc]:
                        curr_hours = alerts[target_loc]['notify_hours']
                    else:
                        # 相容舊設定 (如 08:00 到 22:00)
                        start = alerts[target_loc].get('notify_start', '00:00')
                        end = alerts[target_loc].get('notify_end', '23:59')
                        if start != "00:00" or end != "23:59":
                            sh = int(start.split(':')[0])
                            eh = int(end.split(':')[0])
                            if sh <= eh:
                                curr_hours = list(range(sh, eh + 1))
                            else:
                                curr_hours = list(range(sh, 24)) + list(range(0, eh + 1))
                self.add_item(NotifyTimeSelectForRain(disabled=False, current_hours=curr_hours))
            
            remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
            self.add_item(RemoveAlertSelect(remove_options))
            
        back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, row=4)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🌧️` 降雨預警設定", description="管理當前伺服器的降雨預警頻道與狀態。", color=0x41809b)
        alerts = self.settings.get('rain_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            for loc, data in alerts.items():
                ch_id = data.get('channel_id') if isinstance(data, dict) else data
                time_range = ""
                if isinstance(data, dict):
                    if 'notify_hours' in data:
                        hours = sorted(data['notify_hours'])
                        if len(hours) < 24:
                            if len(hours) == 0:
                                time_range = "\n通知時間：皆不通知"
                            else:
                                parts = []
                                i = 0
                                while i < len(hours):
                                    start_h = hours[i]
                                    while i + 1 < len(hours) and hours[i+1] == hours[i] + 1:
                                        i += 1
                                    end_h = hours[i]
                                    if start_h == end_h:
                                        parts.append(f"{start_h:02d}")
                                    else:
                                        parts.append(f"{start_h:02d}-{end_h:02d}")
                                    i += 1
                                time_range = f"\n通知時間：{','.join(parts)}時"
                    else:
                        start = data.get('notify_start', '00:00')
                        end = data.get('notify_end', '23:59')
                        if start != "00:00" or end != "23:59":
                            time_range = f"\n通知時間：{start}~{end}"
                embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>{time_range}", inline=True)
        else:
            embed.add_field(name="狀態", value="`🔴` 未設定", inline=False)
            embed.add_field(name="提示", value="請使用 `/加入` 來啟用此功能。", inline=False)
        return embed

    async def back_callback(self, interaction: discord.Interaction):
        from cogs.settings.main import SettingsView
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass