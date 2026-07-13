import discord
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect, ClearMentionRoleButton

class TargetLocationSelectForRain(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="選擇要編輯的預警地點", options=options, min_values=1, max_values=1)
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
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="選擇新的發送頻道", min_values=1, max_values=1, disabled=disabled)
        
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
            placeholder="選擇允許通知的時段 (可多選)", 
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

class CooldownTimeSelectForRain(discord.ui.Select):
    def __init__(self, current_cooldown=7200):
        options = [
            discord.SelectOption(label="1 小時", value="3600", description="降雨趨緩後 1 小時內不發送預警", default=(current_cooldown == 3600)),
            discord.SelectOption(label="2 小時 (預設)", value="7200", description="降雨趨緩後 2 小時內不發送預警", default=(current_cooldown == 7200)),
            discord.SelectOption(label="3 小時", value="10800", description="降雨趨緩後 3 小時內不發送預警", default=(current_cooldown == 10800)),
            discord.SelectOption(label="4 小時", value="14400", description="降雨趨緩後 4 小時內不發送預警", default=(current_cooldown == 14400)),
            discord.SelectOption(label="6 小時", value="21600", description="降雨趨緩後 6 小時內不發送預警", default=(current_cooldown == 21600)),
            discord.SelectOption(label="12 小時", value="43200", description="降雨趨緩後 12 小時內不發送預警", default=(current_cooldown == 43200))
        ]
        if not any(opt.default for opt in options):
            options[1].default = True
        super().__init__(placeholder="選擇預警冷卻時間", options=options, min_values=1, max_values=1)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('rain_alerts', {})
        if view.target_loc in alerts:
            if not isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc] = {'channel_id': alerts[view.target_loc]}
            alerts[view.target_loc]['cooldown_time'] = int(self.values[0])
            view.settings['rain_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = RainAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveCurrentRainAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="解除此地點", emoji="🗑️")
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'rain_alerts' in settings and view.target_loc in settings['rain_alerts']:
            del settings['rain_alerts'][view.target_loc]
            if not settings['rain_alerts']:
                del settings['rain_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        new_view = RainAlertSettingsView(view.guild_id, None)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)))
        
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
            
            if target_loc and target_loc in alerts:
                self.add_item(TargetChannelSelectForRain(disabled=False))
                
                curr_hours = list(range(24))
                curr_cooldown = 7200
                if isinstance(alerts[target_loc], dict):
                    curr_cooldown = alerts[target_loc].get('cooldown_time', 7200)
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
                self.add_item(CooldownTimeSelectForRain(current_cooldown=curr_cooldown))
            
                if getattr(self, 'target_loc', None) is None:

            
                    self.add_item(SpecificMentionRoleSelect("rain_mention_role_id"))

            
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
            else:
                remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
                self.add_item(RemoveAlertSelect(remove_options))
                
                if getattr(self, 'target_loc', None) is None:

                
                    self.add_item(SpecificMentionRoleSelect("rain_mention_role_id"))

                
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
        else:
            if getattr(self, 'target_loc', None) is None:

                self.add_item(SpecificMentionRoleSelect("rain_mention_role_id"))

            back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
            
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
        if getattr(self, "target_loc", None) is None and self.settings.get("rain_mention_role_id"):
            self.add_item(ClearMentionRoleButton("rain_mention_role_id", row=4))
            
        if getattr(self, "target_loc", None) is not None and getattr(self, "target_loc", None) in alerts:
            self.add_item(RemoveCurrentRainAlertButton())
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🌧️` 降雨預警設定", description="管理當前伺服器的降雨預警頻道與狀態。", color=0x41809b)
        role_id = self.settings.get('rain_mention_role_id')
        role_status = f"<@&{role_id}>" if role_id else "⚠️ 未設定"
        alerts = self.settings.get('rain_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            embed.add_field(name="預警自動標記", value=role_status, inline=False)
            for loc, data in alerts.items():
                ch_id = data.get('channel_id') if isinstance(data, dict) else data
                time_range = ""
                cooldown_text = ""
                if isinstance(data, dict):
                    cooldown_secs = data.get('cooldown_time', 7200)
                    cooldown_text = f"\n冷卻時間：{cooldown_secs // 3600} 小時"
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
                                    parts.append(f"{start_h:02d}:00~{(end_h+1)%24:02d}:00")
                                    i += 1
                                time_range = f"\n通知時間：{', '.join(parts)}"
                    else:
                        start = data.get('notify_start', '00:00')
                        end = data.get('notify_end', '23:59')
                        if start != "00:00" or end != "23:59":
                            time_range = f"\n通知時間：{start}~{end}"
                embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>{time_range}{cooldown_text}", inline=True)
        else:
            embed.add_field(name="狀態", value="`🔴` 未設定", inline=False)
            embed.add_field(name="預警自動標記", value=role_status, inline=False)
            embed.add_field(name="提示", value="請使用 `/加入` 來啟用此功能。", inline=False)
        return embed

    async def back_callback(self, interaction: discord.Interaction):
        if getattr(self, 'target_loc', None) is not None:
            new_view = self.__class__(self.guild_id, None)
            await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)
        else:
            from cogs.settings.settings_main import SettingsView
            view = SettingsView(int(self.guild_id))
            await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass