import discord
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect, ClearMentionRoleButton

class TargetLocationSelectForTemp(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="選擇要編輯的預警地點", options=options, min_values=1, max_values=1)
        if current_target:
            for opt in self.options:
                if opt.value == current_target:
                    opt.default = True
                    
    async def callback(self, interaction: discord.Interaction):
        self.view.target_loc = self.values[0]
        new_view = TempAlertSettingsView(self.view.guild_id, self.view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TargetChannelSelectForTemp(discord.ui.ChannelSelect):
    def __init__(self, disabled=True):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="選擇新的發送頻道", min_values=1, max_values=1, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('temp_alerts', {})
        if view.target_loc in alerts:
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['channel_id'] = self.values[0].id
            else:
                alerts[view.target_loc] = {'channel_id': self.values[0].id}
            view.settings['temp_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = TempAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class NotifyTimeSelectForTemp(discord.ui.Select):
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
            row=0, 
            disabled=disabled
        )
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('temp_alerts', {})
        if view.target_loc in alerts:
            selected_hours = [int(v) for v in self.values]
            
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['notify_hours'] = selected_hours
            else:
                alerts[view.target_loc] = {
                    'channel_id': alerts[view.target_loc],
                    'notify_hours': selected_hours
                }
                
            view.settings['temp_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
            
        new_view = TempAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TempNotifyHoursButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="通知時段", emoji="⏰", row=4)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        new_view = TempNotifyHoursView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TempNotifyHoursView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        alerts = self.settings.get('temp_alerts', {})
        
        curr_hours = list(range(24))
        if target_loc in alerts and isinstance(alerts[target_loc], dict):
            if 'notify_hours' in alerts[target_loc]:
                curr_hours = alerts[target_loc]['notify_hours']

        self.add_item(NotifyTimeSelectForTemp(disabled=False, current_hours=curr_hours))
        
        back_btn = discord.ui.Button(label="返回地點設定", style=discord.ButtonStyle.secondary, emoji="↩️", row=1)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"`🌡️` 氣溫預警 - {self.target_loc} 通知時段設定",
            description="請在下方選單選取允許發送氣溫預警的時段（可多選 0~24 小時）。",
            color=0x41809b
        )
        return embed

    async def back_callback(self, interaction: discord.Interaction):
        new_view = TempAlertSettingsView(self.guild_id, self.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveCurrentTempAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="解除此地點", emoji="🗑️", row=4)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'temp_alerts' in settings and view.target_loc in settings['temp_alerts']:
            del settings['temp_alerts'][view.target_loc]
            if not settings['temp_alerts']:
                del settings['temp_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        new_view = TempAlertSettingsView(view.guild_id, None)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveTempAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)))
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'temp_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['temp_alerts']:
                    del settings['temp_alerts'][loc_to_remove]
            if not settings['temp_alerts']:
                del settings['temp_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        target = view.target_loc if view.target_loc not in self.values else None
        new_view = TempAlertSettingsView(view.guild_id, target)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TempAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})

        alerts = self.settings.get('temp_alerts', {})
        if alerts:
            loc_options = [discord.SelectOption(label=loc, value=loc) for loc in alerts.keys()][:25]
            self.add_item(TargetLocationSelectForTemp(loc_options, target_loc))
            
            if target_loc and target_loc in alerts:
                self.add_item(TargetChannelSelectForTemp(disabled=False))
                
                if getattr(self, 'target_loc', None) is None:
                    self.add_item(SpecificMentionRoleSelect("temp_mention_role_id"))

                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
                back_btn.callback = self.back_callback
                self.add_item(back_btn)
                self.add_item(TempNotifyHoursButton())
            else:
                remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
                self.add_item(RemoveTempAlertSelect(remove_options))
                
                if getattr(self, 'target_loc', None) is None:
                    self.add_item(SpecificMentionRoleSelect("temp_mention_role_id"))
                
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
                back_btn.callback = self.back_callback
                self.add_item(back_btn)
        else:
            if getattr(self, 'target_loc', None) is None:
                self.add_item(SpecificMentionRoleSelect("temp_mention_role_id"))

            back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
            back_btn.callback = self.back_callback
            self.add_item(back_btn)

        if getattr(self, "target_loc", None) is None and self.settings.get("temp_mention_role_id"):
            self.add_item(ClearMentionRoleButton("temp_mention_role_id", row=4))

        if getattr(self, "target_loc", None) is not None and getattr(self, "target_loc", None) in alerts:
            self.add_item(RemoveCurrentTempAlertButton())

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🌡️` 氣溫預警設定", description="管理當前伺服器的氣溫預警頻道與狀態。", color=0x41809b)
        role_id = self.settings.get('temp_mention_role_id')
        role_status = f"<@&{role_id}>" if role_id else "⚠️ 未設定"
        alerts = self.settings.get('temp_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            embed.add_field(name="預警自動標記", value=role_status, inline=False)
            for loc, data in alerts.items():
                ch_id = data.get('channel_id') if isinstance(data, dict) else data
                time_range = ""
                if isinstance(data, dict) and 'notify_hours' in data:
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
                embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>{time_range}", inline=True)
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