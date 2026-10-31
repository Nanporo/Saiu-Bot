"""災防告警加入流程的種類與測試訊息篩選。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView


class CBSTypesSetupView(discord.ui.View):
    """在設定災防告警地點後，引導用戶選擇要推送的警報種類 (Step 1)"""
    def __init__(self, guild_id: str, loc_val: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.loc_val = loc_val

        options = [
            discord.SelectOption(label="大雷雨即時訊息", value="thunderstorm", emoji="🌩️"),
            discord.SelectOption(label="地震速報", value="earthquakeew", emoji="🏚️"),
            discord.SelectOption(label="颱風強風告警", value="hurricfrcwnd", emoji="🌀"),
            discord.SelectOption(label="淹水警戒", value="flood", emoji="🌊"),
            discord.SelectOption(label="暴潮告警", value="stormsurge", emoji="🌊"),
            discord.SelectOption(label="公路警戒訊息", value="roadclose", emoji="⛔"),
            discord.SelectOption(label="土石流警戒", value="debrisflow", emoji="⛰️"),
            discord.SelectOption(label="水庫放水警戒", value="reservoirdis", emoji="🚰"),
            discord.SelectOption(label="堰塞湖警戒", value="barrierlake", emoji="🏞️"),
            discord.SelectOption(label="疏散避難", value="evacuation", emoji="🏃"),
            discord.SelectOption(label="通訊中斷 / 降速演練", value="commdisrupt", emoji="📶"),
            discord.SelectOption(label="防空警報 (飛彈/空襲)", value="airraidalert", emoji="🚀"),
            discord.SelectOption(label="海嘯警報", value="tsunami", emoji="🌊"),
            discord.SelectOption(label="巨浪告警", value="largesurf", emoji="🌊"),
            discord.SelectOption(label="核子事故", value="nuclear", emoji="☢️"),
            discord.SelectOption(label="緊急警報", value="emergalert", emoji="🚨"),
            discord.SelectOption(label="空氣品質", value="airquality", emoji="😷"),
            discord.SelectOption(label="電力中斷", value="electric", emoji="⚡"),
            discord.SelectOption(label="森林火災", value="forestfire", emoji="🔥"),
            discord.SelectOption(label="演習預告", value="drillnews", description="若為「全台接收」，則需開啟「接收測試與演練」才能收到此類訊息", emoji="📋")
        ]

        self.select = discord.ui.Select(
            placeholder="請選擇要推送的警報種類 (可多選)...",
            options=options,
            min_values=0,
            max_values=len(options),
            row=0
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

        skip_btn = discord.ui.Button(label="全部接收 (使用預設)", style=discord.ButtonStyle.secondary, row=1)
        skip_btn.callback = self.skip_callback
        self.add_item(skip_btn)

    async def proceed_to_next(self, interaction: discord.Interaction, selected_types: list):
        settings = get_all_settings()
        if self.guild_id in settings and 'cbs_alerts' in settings[self.guild_id]:
            alerts = settings[self.guild_id]['cbs_alerts']
            if self.loc_val in alerts:
                if isinstance(alerts[self.loc_val], dict):
                    alerts[self.loc_val]['allowed_types'] = selected_types
                else:
                    alerts[self.loc_val] = {
                        'channel_id': alerts[self.loc_val],
                        'allowed_types': selected_types
                    }
                save_all_settings(settings)

        if not selected_types:
            types_text = "全部接收"
        else:
            type_mapping = {
                "thunderstorm": "大雷雨", "earthquakeew": "地震", "hurricfrcwnd": "颱風",
                "flood": "淹水", "stormsurge": "暴潮", "roadclose": "公路", "debrisflow": "土石流",
                "reservoirdis": "水庫", "barrierlake": "堰塞湖", "evacuation": "疏散避難",
                "commdisrupt": "通訊中斷", "airraidalert": "防空", "tsunami": "海嘯",
                "largesurf": "巨浪", "nuclear": "核災", "emergalert": "緊急",
                "airquality": "空品", "electric": "電力", "forestfire": "森林火災",
                "drillnews": "演習預告"
            }
            types_text = ", ".join([type_mapping.get(t, t) for t in selected_types])

        msg = interaction.message.content + f"\n\n✅ 警報種類已設定為：**{types_text}**"
        msg += "\n\n📢 **請選擇是否要接收「測試訊息」與「山區告警」：**\n請在下方選單設定（若不勾選則皆預設關閉）："
        view = CBSFilterSetupView(self.guild_id, self.loc_val)

        await interaction.response.edit_message(content=msg, view=view)

    async def select_callback(self, interaction: discord.Interaction):
        await self.proceed_to_next(interaction, self.select.values)

    async def skip_callback(self, interaction: discord.Interaction):
        await self.proceed_to_next(interaction, [])


class CBSFilterSetupView(discord.ui.View):
    """在設定災防告警種類後，引導用戶選擇是否接收測試訊息與山區告警 (Step 2)"""
    def __init__(self, guild_id: str, loc_val: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.loc_val = loc_val

        options = [
            discord.SelectOption(label="接收測試與演練訊息", value="test", description="開啟後將會收到系統測試與演習告警", emoji="📢"),
            discord.SelectOption(label="接收山區告警訊息", value="mountain", description="開啟後將會收到如山區暴雨溪水暴漲等警示", emoji="⛰️"),
        ]
        self.select = discord.ui.Select(
            placeholder="請選擇是否開啟山區與測試訊息 (可多選)...",
            options=options,
            min_values=0,
            max_values=2,
            row=0
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

        skip_btn = discord.ui.Button(label="使用預設 (測試與山區皆關閉)", style=discord.ButtonStyle.secondary, row=1)
        skip_btn.callback = self.skip_callback
        self.add_item(skip_btn)

    async def proceed_to_next(self, interaction: discord.Interaction, selected_values: list):
        settings = get_all_settings()
        if self.guild_id in settings and 'cbs_alerts' in settings[self.guild_id]:
            alerts = settings[self.guild_id]['cbs_alerts']
            if self.loc_val in alerts:
                if not isinstance(alerts[self.loc_val], dict):
                    alerts[self.loc_val] = {'channel_id': alerts[self.loc_val]}
                alerts[self.loc_val]['receive_test'] = 'test' in selected_values
                alerts[self.loc_val]['receive_mountain'] = 'mountain' in selected_values
                save_all_settings(settings)

        test_str = "已開啟" if 'test' in selected_values else "已關閉"
        mtn_str = "已開啟" if 'mountain' in selected_values else "已關閉"

        msg = interaction.message.content + f"\n\n✅ 測試訊息：**{test_str}** | 山區告警：**{mtn_str}**"
        view = RoleSetupView("cbs")
        msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)

    async def select_callback(self, interaction: discord.Interaction):
        await self.proceed_to_next(interaction, self.select.values)

    async def skip_callback(self, interaction: discord.Interaction):
        await self.proceed_to_next(interaction, [])


async def setup(bot):
    pass
