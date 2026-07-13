import discord
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect

class TargetLocationSelectForCBS(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="步驟一：選擇要更改頻道的區域", options=options, min_values=1, max_values=1, row=0)
        if current_target:
            for opt in self.options:
                if opt.value == current_target:
                    opt.default = True
                    
    async def callback(self, interaction: discord.Interaction):
        self.view.target_loc = self.values[0]
        new_view = CBSAlertSettingsView(self.view.guild_id, self.view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TargetChannelSelectForCBS(discord.ui.ChannelSelect):
    def __init__(self, disabled=True):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="步驟二：選擇新的發送頻道", min_values=1, max_values=1, row=1, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('cbs_alerts', {})
        if view.target_loc in alerts:
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['channel_id'] = self.values[0].id
            else:
                alerts[view.target_loc] = {'channel_id': self.values[0].id}
            view.settings['cbs_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = CBSAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class OptionsSelectForCBS(discord.ui.Select):
    def __init__(self, target_loc, alerts_dict, disabled=True):
        options = [
            discord.SelectOption(label="接收測試與演練", value="test", description="開啟後將會收到系統測試與演習告警", emoji="📢"),
            discord.SelectOption(label="接收山區告警", value="mountain", description="開啟後將會收到如山區暴雨溪水暴漲等警示", emoji="⛰️"),
            discord.SelectOption(label="大雷雨即時訊息", value="thunderstorm", description="", emoji="🌩️"),
            discord.SelectOption(label="地震速報", value="earthquakeew", description="", emoji="🏚️"),
            discord.SelectOption(label="颱風強風告警", value="hurricfrcwnd", description="", emoji="🌀"),
            discord.SelectOption(label="淹水警戒", value="flood", description="", emoji="🌊"),
            discord.SelectOption(label="公路警戒訊息", value="roadclose", description="", emoji="⛔"),
            discord.SelectOption(label="土石流警戒", value="debrisflow", description="", emoji="⛰️"),
            discord.SelectOption(label="水庫放水警戒", value="reservoirdis", description="", emoji="🚰"),
            discord.SelectOption(label="堰塞湖警戒", value="barrierlake", description="", emoji="🏞️"),
            discord.SelectOption(label="防空警報 (飛彈/空襲)", value="airraidalert", description="", emoji="🚀"),
            discord.SelectOption(label="海嘯警報", value="tsunami", description="", emoji="🌊"),
            discord.SelectOption(label="巨浪告警", value="largesurf", description="", emoji="🌊"),
            discord.SelectOption(label="核子事故", value="nuclear", description="", emoji="☢️"),
            discord.SelectOption(label="緊急警報", value="emergalert", description="", emoji="🚨")
        ]
        
        if not disabled and target_loc in alerts_dict:
            alert_data = alerts_dict[target_loc]
            if isinstance(alert_data, dict):
                if alert_data.get('receive_test', False):
                    options[0].default = True
                if alert_data.get('receive_mountain', False):
                    options[1].default = True
                allowed = alert_data.get('allowed_types', [])
                for opt in options[2:]:
                    if opt.value in allowed:
                        opt.default = True
                    
        super().__init__(placeholder="步驟三：設定過濾與接收類別", options=options, min_values=0, max_values=15, row=2, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('cbs_alerts', {})
        if view.target_loc in alerts:
            alert_data = alerts[view.target_loc]
            if not isinstance(alert_data, dict):
                alert_data = {'channel_id': alert_data}
                
            alert_data['receive_test'] = 'test' in self.values
            alert_data['receive_mountain'] = 'mountain' in self.values
            alert_data['allowed_types'] = [v for v in self.values if v not in ('test', 'mountain')]
            
            alerts[view.target_loc] = alert_data
            view.settings['cbs_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
            
        new_view = CBSAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveCBSAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除預警的區域 (可多選)", options=options, max_values=max(1, len(options)), row=3)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'cbs_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['cbs_alerts']:
                    del settings['cbs_alerts'][loc_to_remove]
            if not settings['cbs_alerts']:
                del settings['cbs_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        target = view.target_loc if view.target_loc not in self.values else None
        new_view = CBSAlertSettingsView(view.guild_id, target)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class CBSAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        
        # 兼容舊版 List 格式
        if 'cbs_alerts' in self.settings and isinstance(self.settings['cbs_alerts'], list):
            old_list = self.settings.pop('cbs_alerts')
            self.settings['cbs_alerts'] = {}
            if old_list:
                self.settings['cbs_alerts']['全台接收'] = {'channel_id': old_list[0]}
            self.all_settings[self.guild_id] = self.settings
            save_settings(self.all_settings)

        alerts = self.settings.get('cbs_alerts', {})
        if alerts:
            loc_options = [discord.SelectOption(label=loc, value=loc) for loc in alerts.keys()][:25]
            self.add_item(TargetLocationSelectForCBS(loc_options, target_loc))
            self.add_item(TargetChannelSelectForCBS(disabled=(target_loc is None)))
            self.add_item(OptionsSelectForCBS(target_loc, alerts, disabled=(target_loc is None)))
            remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
            self.add_item(RemoveCBSAlertSelect(remove_options))
            
        if getattr(self, 'target_loc', None) is None:

            
            self.add_item(SpecificMentionRoleSelect("cbs_mention_role_id", row=3))

            
        back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=4)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`⚠️` 災防告警 (CBS) 設定",
            description="管理當前伺服器的災防告警頻道與狀態。\n當有大雷雨、地震等災防告警發布，且符合您設定的區域時，將會自動推播。",
            color=0x41809b
        )
        alerts = self.settings.get('cbs_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            for loc, data in alerts.items():
                if isinstance(data, dict):
                    ch_id = data.get('channel_id')
                    test_str = "`🟢` 開" if data.get('receive_test') else "`🔴` 關"
                    mtn_str = "`🟢` 開" if data.get('receive_mountain') else "`🔴` 關"
                    
                    type_mapping = {
                        "thunderstorm": "大雷雨", "earthquakeew": "地震", "hurricfrcwnd": "颱風", 
                        "flood": "淹水", "roadclose": "公路", "debrisflow": "土石流", 
                        "reservoirdis": "水庫", "barrierlake": "堰塞湖",
                        "airraidalert": "防空", "tsunami": "海嘯", "largesurf": "巨浪", "nuclear": "核災", "emergalert": "緊急"
                    }
                    allowed = data.get('allowed_types', [])
                    types_str = "全部接收" if not allowed else ", ".join([type_mapping.get(t, t) for t in allowed])
                    
                    embed.add_field(name=f"📍 {loc}", value=f"頻道：<#{ch_id}>\n測試：{test_str}\n山區：{mtn_str}\n類型：`{types_str}`", inline=True)
                else:
                    embed.add_field(name=f"📍 {loc}", value=f"頻道：<#{data}>\n測試：`🔴` 關\n山區：`🔴` 關\n類型：`全部接收`", inline=True)
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
