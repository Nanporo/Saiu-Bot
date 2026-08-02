import discord
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect, ClearMentionRoleButton

class TargetLocationSelectForEew(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="選擇要編輯的地點", options=options, min_values=1, max_values=1)
        if current_target:
            for opt in self.options:
                if opt.value == current_target:
                    opt.default = True
                    
    async def callback(self, interaction: discord.Interaction):
        self.view.target_loc = self.values[0]
        new_view = EewAlertSettingsView(self.view.guild_id, self.view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TargetChannelSelectForEew(discord.ui.ChannelSelect):
    def __init__(self, disabled=True):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="選擇新的發送頻道", min_values=1, max_values=1, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('eew_alerts', {})
        if view.target_loc in alerts:
            alerts[view.target_loc]['channel_id'] = self.values[0].id
            view.settings['eew_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = EewAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class MinMagnitudeSelectForEew(discord.ui.Select):
    def __init__(self, current_mag=4.5):
        options = []
        for mag in [4.5, 5.0, 5.5, 6.0, 6.5, 7.0]:
            options.append(discord.SelectOption(
                label=f"規模 ≥ {mag:.1f}", 
                value=str(mag), 
                default=(mag == current_mag)
            ))
        super().__init__(placeholder="選擇最低地震規模", options=options, min_values=1, max_values=1)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('eew_alerts', {})
        if view.target_loc in alerts:
            alerts[view.target_loc]['min_magnitude'] = float(self.values[0])
            view.settings['eew_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = EewAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class MinIntensitySelectForEew(discord.ui.Select):
    def __init__(self, current_int=3):
        options = []
        for i in range(1, 6):
            label = f"預估震度 ≥ {i}級" if i < 5 else f"預估震度 ≥ 5弱"
            options.append(discord.SelectOption(
                label=label, 
                value=str(i), 
                default=(i == current_int)
            ))
        super().__init__(placeholder="選擇最低震度", options=options, min_values=1, max_values=1)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('eew_alerts', {})
        if view.target_loc in alerts:
            alerts[view.target_loc]['min_intensity'] = int(self.values[0])
            view.settings['eew_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = EewAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveCurrentEewAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="解除此地點預警", emoji="🗑️")
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'eew_alerts' in settings and view.target_loc in settings['eew_alerts']:
            del settings['eew_alerts'][view.target_loc]
            if not settings['eew_alerts']:
                del settings['eew_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        new_view = EewAlertSettingsView(view.guild_id, None)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveEewAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除預警的地點 (可多選)", options=options, max_values=max(1, len(options)))
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'eew_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['eew_alerts']:
                    del settings['eew_alerts'][loc_to_remove]
            if not settings['eew_alerts']:
                del settings['eew_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        target = view.target_loc if view.target_loc not in self.values else None
        new_view = EewAlertSettingsView(view.guild_id, target)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class ToggleEewImageButton(discord.ui.Button):
    def __init__(self, is_enabled: bool):
        super().__init__(style=discord.ButtonStyle.secondary, label="切換圖片生成", emoji="🖼️")
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        current = settings.get('eew_image_enabled', False)
        settings['eew_image_enabled'] = not current
        
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        new_view = EewAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class ToggleEewQuickReportButton(discord.ui.Button):
    def __init__(self, is_enabled: bool):
        super().__init__(style=discord.ButtonStyle.secondary, label="切換震度速報", emoji="🏠")
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        current = settings.get('eew_quick_report_enabled', False)
        settings['eew_quick_report_enabled'] = not current
        
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        new_view = EewAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)


class EewAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})

        alerts = self.settings.get('eew_alerts', {})
        if alerts:
            loc_options = [discord.SelectOption(label=loc, value=loc) for loc in alerts.keys()][:25]
            self.add_item(TargetLocationSelectForEew(loc_options, target_loc))
            
            if target_loc and target_loc in alerts:
                self.add_item(TargetChannelSelectForEew(disabled=False))
                
                data = alerts[target_loc]
                curr_mag = data.get('min_magnitude', 4.0)
                curr_int = data.get('min_intensity', 2)
                    
                self.add_item(MinMagnitudeSelectForEew(current_mag=curr_mag))
                self.add_item(MinIntensitySelectForEew(current_int=curr_int))
                
                if getattr(self, 'target_loc', None) is None:

                    self.add_item(SpecificMentionRoleSelect("eew_mention_role_id"))

                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️")
                back_btn.callback = self.back_callback
                self.add_item(back_btn)
                if getattr(self, "target_loc", None) is None and self.settings.get("eew_mention_role_id"):
                    self.add_item(ClearMentionRoleButton("eew_mention_role_id"))
            else:
                remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
                self.add_item(RemoveEewAlertSelect(remove_options))
                
                if getattr(self, 'target_loc', None) is None:

                
                    self.add_item(SpecificMentionRoleSelect("eew_mention_role_id"))

                
                back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️")
                back_btn.callback = self.back_callback
                self.add_item(back_btn)
                if getattr(self, "target_loc", None) is None and self.settings.get("eew_mention_role_id"):
                    self.add_item(ClearMentionRoleButton("eew_mention_role_id"))
                
                is_img_enabled = self.settings.get('eew_image_enabled', False)
                self.add_item(ToggleEewImageButton(is_img_enabled))
                is_quick_report_enabled = self.settings.get('eew_quick_report_enabled', False)
                self.add_item(ToggleEewQuickReportButton(is_quick_report_enabled))
        else:
            if getattr(self, 'target_loc', None) is None:

                self.add_item(SpecificMentionRoleSelect("eew_mention_role_id"))

            back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️")
            back_btn.callback = self.back_callback
            self.add_item(back_btn)
            if getattr(self, "target_loc", None) is None and self.settings.get("eew_mention_role_id"):
                self.add_item(ClearMentionRoleButton("eew_mention_role_id"))
            
            is_quick_report_enabled = self.settings.get('eew_quick_report_enabled', False)
            self.add_item(ToggleEewQuickReportButton(is_quick_report_enabled))
            
        if getattr(self, "target_loc", None) is not None and getattr(self, "target_loc", None) in alerts:
            self.add_item(RemoveCurrentEewAlertButton())
            
    def build_embed(self) -> discord.Embed:
        role_id = self.settings.get('eew_mention_role_id')
        role_status = f"<@&{role_id}>" if role_id else "⚠️ 未設定"
        role_icon = "`🟢`" if role_id else "`🔴`"
        alerts = self.settings.get('eew_alerts', {})
        eew_auth = self.settings.get('eew_authorized', False)
        
        auth_str = "`🟢` **許可狀態**：已獲得許可" if eew_auth else "`🔴` **許可狀態**：未獲得許可"
        status_str = "`🟢` **啟用狀態**：已啟用" if alerts else "`🔴` **啟用狀態**：已關閉"
        role_str = f"{role_icon} **預警標記**：{role_status}"
        is_img = self.settings.get('eew_image_enabled', False)
        img_str = "`🟢` **圖片生成**：已開啟" if is_img else "`🔴` **圖片生成**：已關閉"
        is_quick_report = self.settings.get('eew_quick_report_enabled', False)
        quick_report_str = "`🟢` **震度速報**：已開啟" if is_quick_report else "`🔴` **震度速報**：已關閉"

        desc = (
            "管理當前伺服器的強震即時警報與頻道。\n\n"
            f"{auth_str}\n"
            f"{status_str}\n"
            f"{role_str}\n"
            f"{img_str}\n"
            f"{quick_report_str}\n"
        )

        embed = discord.Embed(title="`🚨` 強震即時警報設定", description=desc, color=0x41809b)

        if alerts:
            for loc, data in alerts.items():
                ch_id = data.get('channel_id', '未知')
                min_mag = data.get('min_magnitude', 4.0)
                min_int = data.get('min_intensity', 2)
                embed.add_field(name=f"📍 {loc}", value=f"發送至：<#{ch_id}>\n規模≥{float(min_mag):.1f} 且預估震度≥{min_int}級", inline=True)
        else:
            embed.add_field(name="提示", value="請使用 `/加入` 來設定此功能。", inline=False)
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
