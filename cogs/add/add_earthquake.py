"""地震報告與強震即時警報的加入表單。"""

import discord
from modules.database import get_all_settings, save_all_settings
import math
from modules.location_matcher import match_location
from cogs.add.add_roles import RoleSetupView


class EqModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="設定地震通知地點與震度")

    location = discord.ui.TextInput(
        label="請輸入縣市與鄉鎮市區",
        placeholder="例如：臺北市信義區...",
        required=True,
        max_length=20
    )

    min_intensity = discord.ui.TextInput(
        label="最低觸發震度 (請輸入數字 1~6)",
        placeholder="例如：3",
        default="3",
        required=True,
        max_length=1
    )

    min_magnitude = discord.ui.TextInput(
        label="最低觸發規模 (1.0~6.5)",
        placeholder="例如：5.5",
        default="5.5",
        required=True,
        max_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        if self.location.value == "全台接收":
            loc_val = "全台接收"
        else:
            loc_val, error_msg = match_location(self.location.value)
            if error_msg:
                await interaction.response.send_message(content=error_msg, ephemeral=True)
                return

        try:
            min_int = int(self.min_intensity.value)
            if min_int < 1 or min_int > 6:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(content="❌ 最低觸發震度請輸入 1 到 6 之間的數字。", ephemeral=True)
            return

        try:
            min_mag = float(self.min_magnitude.value)
            if math.isnan(min_mag) or min_mag < 1.0 or min_mag > 6.5:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(content="❌ 最低觸發規模請輸入 1.0 到 6.5 之間的數字。", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id

        settings = get_all_settings()

        if guild_id not in settings:
            settings[guild_id] = {}

        alerts = settings[guild_id].setdefault('eq_alerts', {})
        if len(alerts) >= 20 and loc_val not in alerts:
            await interaction.response.send_message(content="❌ 本伺服器已達到最多 20 個地震通知地點的上限！", ephemeral=True)
            return

        previous = alerts.get(loc_val, {})
        detailed = previous.get('detailed_format', False) if isinstance(previous, dict) else False
        alerts[loc_val] = {
            'channel_id': channel_id,
            'min_magnitude': min_mag,
            'min_intensity': min_int,
            'detailed_format': loc_val == "全台接收" or detailed
        }

        save_all_settings(settings)

        msg = f"✅ 已成功設定！當 **{loc_val}** 發生規模達 **{float(min_mag):.1f}** 且最大震度達 **{min_int}級** 以上的地震時，將會自動通知此頻道。"
        if loc_val != "全台接收":
            msg += "\n\n🖼️ **請選擇地震通知格式：**\n簡易：地震規模與當地震度摘要。\n詳細：完整地震報告與圖表。"
            await interaction.response.edit_message(
                content=msg, view=EqFormatJoinView(guild_id, loc_val, interaction.user.id))
            return
        msg += "\n全台接收固定使用詳細格式。"
        view = RoleSetupView("earthquake")
        msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)


class EqFormatJoinView(discord.ui.View):
    """Choose the earthquake report format before configuring a mention role."""
    def __init__(self, guild_id, target_loc, author_id):
        super().__init__(timeout=180)
        self.guild_id = str(guild_id)
        self.target_loc = target_loc
        self.author_id = author_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 此選單只能由原指令使用者操作。", ephemeral=True)
            return False
        return True

    async def set_format(self, interaction, detailed):
        settings = get_all_settings()
        sub = settings.get(self.guild_id, {}).get('eq_alerts', {}).get(self.target_loc)
        if not isinstance(sub, dict):
            await interaction.response.send_message("❌ 此地點的通知已解除，請重新使用 `/加入` 設定。", ephemeral=True)
            return
        sub['detailed_format'] = self.target_loc == "全台接收" or detailed
        save_all_settings(settings)
        fmt = "詳細" if sub['detailed_format'] else "簡易"
        await interaction.response.edit_message(
            content=f"✅ 已將 **{self.target_loc}** 的地震通知設定為 **{fmt}** 格式。\n可使用 `/設定` 調整格式。\n可繼續設定通知標記身分組：",
            view=RoleSetupView("earthquake"))

    @discord.ui.button(label="簡易通知", style=discord.ButtonStyle.secondary)
    async def simple(self, interaction, button):
        await self.set_format(interaction, False)

    @discord.ui.button(label="詳細通知", style=discord.ButtonStyle.secondary)
    async def detailed(self, interaction, button):
        await self.set_format(interaction, True)


class EewModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="設定強震即時警報")

    location = discord.ui.TextInput(
        label="請輸入縣市與鄉鎮市區 (可填寫: 全台接收)",
        placeholder="例如：臺北市信義區、全台接收...",
        required=True,
        max_length=20
    )

    min_intensity = discord.ui.TextInput(
        label="最低觸發預估震度 (數字 1~5)",
        placeholder="例如：3",
        default="3",
        required=False,
        max_length=1
    )

    min_magnitude = discord.ui.TextInput(
        label="最低觸發規模 (4.5~7.0 之間)",
        placeholder="例如：4.5",
        default="4.5",
        required=False,
        max_length=4
    )

    async def on_submit(self, interaction: discord.Interaction):
        if self.location.value == "全台接收":
            loc_val = "全台接收"
            error_msg = None
        else:
            loc_val, error_msg = match_location(self.location.value)

        if error_msg:
            await interaction.response.send_message(content=error_msg, ephemeral=True)
            return

        try:
            min_int = int(self.min_intensity.value)
            if min_int < 1 or min_int > 5:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(content="❌ 最低觸發震度請輸入 1 到 5 之間的數字。", ephemeral=True)
            return

        try:
            min_mag = float(self.min_magnitude.value) if self.min_magnitude.value else 4.5
            if math.isnan(min_mag) or min_mag < 4.5 or min_mag > 7.0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(content="❌ 規模請輸入 4.5 到 7.0 之間的有效數字。", ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id

        settings = get_all_settings()
        if guild_id not in settings:
            settings[guild_id] = {}

        if not settings[guild_id].get("eew_authorized", False):
            await interaction.response.send_message(content="❌ 本伺服器尚未獲得強震即時警報推播許可。\n您可以透過 `/問題回報` 指令的表單申請推播。", ephemeral=True)
            return

        alerts = settings[guild_id].setdefault('eew_alerts', {})
        if len(alerts) >= 20 and loc_val not in alerts:
            await interaction.response.send_message(content="❌ 本伺服器已達到最多 20 個 EEW 通知地點的上限！", ephemeral=True)
            return

        alerts[loc_val] = {
            'channel_id': channel_id,
            'min_magnitude': min_mag,
            'min_intensity': min_int
        }

        save_all_settings(settings)

        msg = f"✅ 已成功設定！當 **{loc_val}** 預估震度達 **{min_int}級** 且規模達 **{float(min_mag):.1f}** 時，將會自動通知此頻道。"
        msg += "\n🖼️ 🏠 💡 **提示**：您後續可在 `/設定` 指令的 `強震即時警報` 選單中，選擇是否要開啟地圖圖片發送與震度速報功能。"
        view = RoleSetupView("eew")
        msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)


class EqSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="點此輸入地點與觸發條件（可填寫：全台接收）", style=discord.ButtonStyle.primary, emoji="✍️")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EqModal())


async def setup(bot):
    pass
