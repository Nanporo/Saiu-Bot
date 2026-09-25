import discord
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect, ClearMentionRoleButton

TRAFFIC_MODES = [
    {"code": "thsr", "label": "台灣高鐵"},
    {"code": "tra",  "label": "台灣鐵路"},
    {"code": "trtc", "label": "臺北捷運"},
    {"code": "tymc", "label": "桃園機場捷運"},
    {"code": "tmrt", "label": "臺中捷運"},
    {"code": "krtc", "label": "高雄捷運"},
]
DEFAULT_TRAFFIC_MODES = [m["code"] for m in TRAFFIC_MODES]
MODE_NAMES = {m["code"]: m["label"] for m in TRAFFIC_MODES}
MODE_MAP = {m["code"]: m for m in TRAFFIC_MODES}

NO_TRAIN_COUNTIES = {"澎湖縣", "金門縣", "連江縣"}

THSR_COUNTIES = {
    "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣",
    "臺中市", "彰化縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市"
}

TRTC_COUNTIES = {"臺北市", "新北市"}
TYMC_COUNTIES = {"臺北市", "新北市", "桃園市"}
TMRT_COUNTIES = {"臺中市"}
KRTC_COUNTIES = {"高雄市"}

def get_available_traffic_modes(county: str) -> list[dict]:
    """根據縣市回傳該地區實際支援的軌道交通方式"""
    if not county or county == "全台接收":
        return list(TRAFFIC_MODES)

    norm_county = county.replace("台", "臺").strip()
    if not norm_county.endswith(("市", "縣")):
        for full in ["臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市", "嘉義市"]:
            if norm_county in full:
                norm_county = full
                break

    available_codes = []

    # 1. 檢查高鐵
    if norm_county in THSR_COUNTIES:
        available_codes.append("thsr")

    # 2. 檢查台鐵 (本島皆有，排除澎湖、金門、連江)
    if norm_county not in NO_TRAIN_COUNTIES:
        available_codes.append("tra")

    # 3. 檢查臺北捷運
    if norm_county in TRTC_COUNTIES:
        available_codes.append("trtc")

    # 4. 檢查桃園捷運
    if norm_county in TYMC_COUNTIES:
        available_codes.append("tymc")

    # 5. 檢查臺中捷運
    if norm_county in TMRT_COUNTIES:
        available_codes.append("tmrt")

    # 6. 檢查高雄捷運
    if norm_county in KRTC_COUNTIES:
        available_codes.append("krtc")

    if not available_codes and norm_county not in NO_TRAIN_COUNTIES:
        available_codes.append("tra")

    return [MODE_MAP[code] for code in available_codes if code in MODE_MAP]

class TargetLocationSelectForTraffic(discord.ui.Select):
    def __init__(self, options, current_target=None):
        super().__init__(placeholder="選擇要編輯的區域", options=options, min_values=1, max_values=1)
        if current_target:
            for opt in self.options:
                if opt.value == current_target:
                    opt.default = True
                    
    async def callback(self, interaction: discord.Interaction):
        self.view.target_loc = self.values[0]
        new_view = TrafficAlertSettingsView(self.view.guild_id, self.view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TargetChannelSelectForTraffic(discord.ui.ChannelSelect):
    def __init__(self, disabled=True):
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="選擇新的發送頻道", min_values=1, max_values=1, disabled=disabled)
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('traffic_alerts', {})
        if view.target_loc in alerts:
            avail_modes = get_available_traffic_modes(view.target_loc)
            default_modes = [m["code"] for m in avail_modes]
            if isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc]['channel_id'] = self.values[0].id
            else:
                alerts[view.target_loc] = {'channel_id': self.values[0].id, 'enabled_modes': default_modes}
            view.settings['traffic_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
        
        new_view = TrafficAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TrafficModeSelectForTraffic(discord.ui.Select):
    def __init__(self, current_modes=None, target_loc="全台接收"):
        available_modes = get_available_traffic_modes(target_loc)
        available_codes = [m["code"] for m in available_modes]

        if current_modes is None:
            current_modes = available_codes
            
        options = []
        for m in available_modes:
            options.append(discord.SelectOption(
                label=m["label"],
                value=m["code"],
                default=(m["code"] in current_modes)
            ))
            
        super().__init__(
            placeholder=f"選擇推播的交通方式 ({target_loc})",
            options=options,
            min_values=1,
            max_values=len(available_modes)
        )
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        alerts = view.settings.get('traffic_alerts', {})
        if view.target_loc in alerts:
            if not isinstance(alerts[view.target_loc], dict):
                alerts[view.target_loc] = {'channel_id': alerts[view.target_loc]}
            alerts[view.target_loc]['enabled_modes'] = self.values
            view.settings['traffic_alerts'] = alerts
            view.all_settings[view.guild_id] = view.settings
            save_settings(view.all_settings)
            
        new_view = TrafficAlertSettingsView(view.guild_id, view.target_loc)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveCurrentTrafficAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="解除此地點", emoji="🗑️")
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'traffic_alerts' in settings and view.target_loc in settings['traffic_alerts']:
            del settings['traffic_alerts'][view.target_loc]
            if not settings['traffic_alerts']:
                del settings['traffic_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        new_view = TrafficAlertSettingsView(view.guild_id, None)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class RemoveTrafficAlertSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="選擇要解除推播的區域 (可多選)", options=options, max_values=max(1, len(options)))
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        settings = view.settings
        if 'traffic_alerts' in settings:
            for loc_to_remove in self.values:
                if loc_to_remove in settings['traffic_alerts']:
                    del settings['traffic_alerts'][loc_to_remove]
            if not settings['traffic_alerts']:
                del settings['traffic_alerts']
                
        view.all_settings[view.guild_id] = settings
        save_settings(view.all_settings)
        
        target = view.target_loc if view.target_loc not in self.values else None
        new_view = TrafficAlertSettingsView(view.guild_id, target)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class TrafficAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        
        # 舊格式相容性處理
        if 'traffic_alerts' in self.settings and isinstance(self.settings['traffic_alerts'], (int, str)):
            old_ch = self.settings.pop('traffic_alerts')
            self.settings['traffic_alerts'] = {'全台接收': {'channel_id': int(old_ch), 'enabled_modes': DEFAULT_TRAFFIC_MODES}}
            self.all_settings[self.guild_id] = self.settings
            save_settings(self.all_settings)

        alerts = self.settings.get('traffic_alerts', {})
        
        # 若只有一個地點且未特別指定，預設直接進入該地點進行編輯
        if target_loc is None and len(alerts) == 1:
            target_loc = list(alerts.keys())[0]

        self.target_loc = target_loc
        
        if alerts:
            loc_options = [discord.SelectOption(label=loc, value=loc) for loc in alerts.keys()][:25]
            self.add_item(TargetLocationSelectForTraffic(loc_options, target_loc))
            
            if target_loc is not None:
                self.add_item(TargetChannelSelectForTraffic(disabled=False))
                loc_data = alerts.get(target_loc, {})
                loc_avail_modes = get_available_traffic_modes(target_loc)
                loc_avail_codes = [m["code"] for m in loc_avail_modes]
                current_modes = loc_data.get('enabled_modes', loc_avail_codes) if isinstance(loc_data, dict) else loc_avail_codes
                self.add_item(TrafficModeSelectForTraffic(current_modes=current_modes, target_loc=target_loc))
            else:
                remove_options = [discord.SelectOption(label=loc, value=loc, emoji="🗑️") for loc in alerts.keys()][:25]
                self.add_item(RemoveTrafficAlertSelect(remove_options))
            
        if getattr(self, 'target_loc', None) is None:
            self.add_item(SpecificMentionRoleSelect("traffic_mention_role_id"))

        back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️")
        back_btn.callback = self.back_callback
        self.add_item(back_btn)
        if getattr(self, "target_loc", None) is not None and getattr(self, "target_loc", None) in alerts:
            self.add_item(RemoveCurrentTrafficAlertButton())
        if getattr(self, "target_loc", None) is None and self.settings.get("traffic_mention_role_id"):
            self.add_item(ClearMentionRoleButton("traffic_mention_role_id"))
            
    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`🚄` 交通狀況通知設定", description="管理當前伺服器的交通營運狀況異動通知頻道與推播運具。", color=0x41809b)
        role_id = self.settings.get('traffic_mention_role_id')
        role_status = f"<@&{role_id}>" if role_id else "⚠️ 未設定"
        alerts = self.settings.get('traffic_alerts', {})
        if alerts:
            embed.add_field(name="狀態", value="`🟢` 已啟用", inline=False)
            embed.add_field(name="預警自動標記", value=role_status, inline=False)
            for loc, data in alerts.items():
                ch_id = data.get('channel_id') if isinstance(data, dict) else data
                loc_avail_modes = get_available_traffic_modes(loc)
                loc_avail_codes = [m["code"] for m in loc_avail_modes]
                raw_modes = data.get('enabled_modes', loc_avail_codes) if isinstance(data, dict) else loc_avail_codes
                # 只顯示該縣市實際支援的模式
                enabled_modes = [m for m in raw_modes if m in loc_avail_codes]
                if not enabled_modes:
                    enabled_modes = loc_avail_codes

                modes_str = "、".join([MODE_NAMES.get(m, m) for m in enabled_modes]) if enabled_modes else "無"
                embed.add_field(
                    name=f"📍 {loc}", 
                    value=f"• 發送至：<#{ch_id}>\n• 推播運具：**{modes_str}**", 
                    inline=True
                )
        else:
            embed.add_field(name="狀態", value="`🔴` 未設定", inline=False)
            embed.add_field(name="預警自動標記", value=role_status, inline=False)
            embed.add_field(name="提示", value="請使用 `/加入` 來啟用此功能。", inline=False)
        return embed

    async def back_callback(self, interaction: discord.Interaction):
        alerts = self.settings.get('traffic_alerts', {})
        if getattr(self, 'target_loc', None) is not None and len(alerts) > 1:
            new_view = self.__class__(self.guild_id, None)
            new_view.target_loc = None
            await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)
        else:
            from cogs.settings.settings_main import SettingsView
            view = SettingsView(int(self.guild_id))
            await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass
