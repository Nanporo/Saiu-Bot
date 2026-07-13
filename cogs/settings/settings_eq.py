import discord
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect, ClearMentionRoleButton

class TargetLocationSelectForEq(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="選擇要編輯的預警地點", options=options, min_values=1, max_values=1)
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
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="選擇新的發送頻道", min_values=1, max_values=1, disabled=disabled)
        
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
        # 產生 1.0 到 6.5，間距 0.5 的選項
        mags = [i * 0.5 for i in range(2, 14)]
        for mag in mags:
            options.append(discord.SelectOption(
                label=f"規模 ≥ {mag:.1f}", 
                value=str(mag), 
                default=(mag == current_mag)
            ))
        super().__init__(placeholder="選擇最低地震規模", options=options, min_values=1, max_values=1)
        
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
        super().__init__(placeholder="選擇最低震度", options=options, min_values=1, max_values=1)
        
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

class ToggleFormatButtonForEq(discord.ui.Button):
    def __init__(self, is_detailed=False, row=None):
        label = "格式：詳細圖表" if is_detailed else "格式：一般簡易"
        style = discord.ButtonStyle.success if is_detailed else discord.ButtonStyle.secondary
        super().__init__(style=style, label=label, emoji="🖼️", row=row)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('eq_alerts', {})
        if view.target_loc in alerts:
            if not isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc] = {'channel_id': alerts[view.target_loc], 'min_magnitude': 5.5, 'min_intensity': 3}
            current = alerts[view.target_loc].get('detailed_format', False)
            alerts[view.target_loc]['detailed_format'] = not current
            view.settings['eq_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
            
        new_view = EqAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveCurrentEqAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="解除預警", emoji="🗑️")
        
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
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)))
        
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

        add_format_btn = False
        format_is_detailed = False

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
                    is_detailed = data.get('detailed_format', False)
                else:
                    curr_mag = 5.5
                    curr_int = 3
                    is_detailed = False
                    
                self.add_item(MinMagnitudeSelectForEq(current_mag=curr_mag))
                self.add_item(MinIntensitySelectForEq(current_int=curr_int))
                if target_loc != "全台接收":
                    add_format_btn = True
                    format_is_detailed = is_detailed
                
                if getattr(self, 'target_loc', None) is None:

                
                    self.add_item(SpecificMentionRoleSelect("eq_mention_role_id"))

                
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
            else:
                remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
                self.add_item(RemoveEqAlertSelect(remove_options))
                
                if getattr(self, 'target_loc', None) is None:

                
                    self.add_item(SpecificMentionRoleSelect("eq_mention_role_id"))

                
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
        else:
            if getattr(self, 'target_loc', None) is None:

                self.add_item(SpecificMentionRoleSelect("eq_mention_role_id"))

            back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
            
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
        
        if add_format_btn:
            self.add_item(ToggleFormatButtonForEq(is_detailed=format_is_detailed, row=4))
        if getattr(self, "target_loc", None) is None and self.settings.get("eq_mention_role_id"):
            self.add_item(ClearMentionRoleButton("eq_mention_role_id", row=4))
            
        if getattr(self, "target_loc", None) is not None and getattr(self, "target_loc", None) in alerts:
            self.add_item(RemoveCurrentEqAlertButton())
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🏚️` 地震通知設定", description="管理當前伺服器的地震通知頻道與狀態。", color=0x41809b)
        role_id = self.settings.get('eq_mention_role_id')
        role_status = f"<@&{role_id}>" if role_id else "⚠️ 未設定"
        alerts = self.settings.get('eq_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            embed.add_field(name="預警自動標記", value=role_status, inline=False)
            for loc, data in alerts.items():
                if isinstance(data, dict):
                    ch_id = data.get('channel_id', '未知')
                    min_mag = data.get('min_magnitude', 5.5)
                    min_int = data.get('min_intensity', 3)
                    fmt = "詳細圖表" if data.get('detailed_format', False) else "一般簡易"
                else:
                    ch_id = data
                    min_mag, min_int = 5.5, 3
                    fmt = "一般簡易"
                if loc == "全台接收":
                    embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>\n規模≥{min_mag} 且最大震度≥{min_int}級\n格式：詳細圖表 (固定)", inline=True)
                else:
                    embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>\n規模≥{min_mag} 且震度≥{min_int}級\n格式：{fmt}", inline=True)
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