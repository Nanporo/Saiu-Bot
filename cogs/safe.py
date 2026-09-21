import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Optional
from modules.ownercheck import is_owner
from modules.config import get_config
from modules.database import (
    get_safety_checkin,
    get_active_safety_checkins,
    get_recent_safety_checkins,
    close_safety_checkin,
    get_safety_messages
)
from cogs.alarm.alert_safe import broadcast_safety_checkin, build_safety_embed, parse_dt, SafetyCheckinView

logger = logging.getLogger(__name__)

try:
    config = get_config()
    OWNER_SERVER_ID = config.OWNER_SERVER_ID
except Exception:
    OWNER_SERVER_ID = 0

OWNER_GUILDS = [discord.Object(id=OWNER_SERVER_ID)] if OWNER_SERVER_ID else []

class ConfirmSendView(discord.ui.View):
    def __init__(self, bot, title: str, description: str, hours: int, author_id: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.title = title
        self.description = description
        self.hours = hours
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 只有指令發起者可以操作此確認按鈕！", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="確認發送", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(view=self)

        cnt = await broadcast_safety_checkin(
            self.bot,
            self.title,
            self.description,
            self.hours,
            created_by=interaction.user.name
        )
        await interaction.followup.send(f"✅ 已成功發布平安通報「**{self.title}**」並推播至 `{cnt}` 個伺服器！", ephemeral=True)

    @discord.ui.button(label="取消發送", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ 已取消發送平安通報。", embed=None, view=self)

class SafetyCreateModal(discord.ui.Modal):
    def __init__(self, bot, author_id: int):
        super().__init__(title="發起平安通報")
        self.bot = bot
        self.author_id = author_id

    event_title = discord.ui.TextInput(
        label="事件標題",
        placeholder="例如：2024年花蓮地震",
        required=True,
        max_length=100
    )

    event_description = discord.ui.TextInput(
        label="說明文字 (支援 Enter 換行或 \\n)",
        placeholder="例如：花蓮地區發生規模7.2地震\n請大家回報自身狀況並注意安全",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1500
    )

    event_hours = discord.ui.TextInput(
        label="有效時間 (小時，限定 1~720)",
        placeholder="例如：72",
        default="72",
        required=True,
        max_length=4
    )

    async def on_submit(self, interaction: discord.Interaction):
        title = str(self.event_title.value).strip().replace("\\n", " ")
        raw_desc = str(self.event_description.value).strip()
        # 支援 Enter 換行及手動輸入的 \n 字元
        description = raw_desc.replace("\r\n", "\n").replace("\r", "\n").replace("\\n", "\n")

        hours_val = str(self.event_hours.value).strip()
        try:
            hours = int(hours_val)
            if hours < 1 or hours > 720:
                await interaction.response.send_message(
                    "❌ 有效時間必須介於 1 小時至 720 小時（30 天）之間！",
                    ephemeral=True
                )
                return
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "❌ 有效時間格式錯誤，請輸入介於 1 至 720 之間的整數！",
                ephemeral=True
            )
            return

        # 二次確認預覽 Embed，防止誤發
        confirm_embed = discord.Embed(
            title="請確認平安通報事件內容",
            description=f"**【{title}】**\n\n{description}",
            color=0x4cd4af
        )
        confirm_embed.set_footer(text=f"預計有效時間：{hours} 小時")

        view = ConfirmSendView(self.bot, title, description, hours, interaction.user.id)
        await interaction.response.send_message(
            content="🏡 小裁雨平安通報系統",
            embed=confirm_embed,
            view=view,
            ephemeral=True
        )

# ================= /平安通報記錄 歷史查詢介面 =================

class CheckinSelect(discord.ui.Select):
    def __init__(self, checkins: list, guild_id: int, start_idx: int = 0):
        self.guild_id = guild_id
        self.checkins_map = {str(c["id"]): c for c in checkins}
        options = []
        for i, c in enumerate(checkins):
            c_dt = parse_dt(c.get("created_at"))
            date_str = c_dt.strftime("%Y-%m-%d")
            title = c.get("title", "平安通報")
            options.append(discord.SelectOption(
                label=f"{start_idx + i + 1}. {title}"[:100],
                description=f"{date_str} | ID: {c['id']}",
                value=str(c["id"])
            ))
        super().__init__(placeholder="請選擇要查詢的通報記錄...", options=options, min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        checkin = self.checkins_map.get(selected_id)
        if not checkin:
            await interaction.response.send_message("❌ 找不到該筆通報資料。", ephemeral=True)
            return

        detail_view = CheckinDetailView(checkin, self.guild_id, interaction.user.id, self.view)
        embed = detail_view.build_page_embed()
        await interaction.response.edit_message(content="🏡 平安通報記錄", embed=embed, view=detail_view)

class CheckinHistoryOverviewView(discord.ui.View):
    def __init__(self, checkins: list, guild: discord.Guild, author_id: int):
        super().__init__(timeout=300)
        self.checkins = checkins
        self.guild = guild
        self.author_id = author_id
        self.current_page = 0
        self.per_page = 5

        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=1)
        self.prev_btn.callback = self.prev_page

        self.page_indicator = discord.ui.Button(label="", style=discord.ButtonStyle.secondary, disabled=True, row=1)

        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=1)
        self.next_btn.callback = self.next_page

        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 只有指令呼叫者可以操作此選單與按鈕！", ephemeral=True)
            return False
        return True

    def update_components(self):
        self.clear_items()
        total_pages = max(1, (len(self.checkins) + self.per_page - 1) // self.per_page)
        start_idx = self.current_page * self.per_page
        page_checkins = self.checkins[start_idx : start_idx + self.per_page]

        # Row 0: 下拉選單（顯示當前頁面的通報）
        self.add_item(CheckinSelect(page_checkins, self.guild.id, start_idx=start_idx))

        # Row 1: 翻頁與關閉按鈕（若不足或等於5個事件則翻頁按鈕不可用）
        if len(self.checkins) <= self.per_page:
            self.prev_btn.disabled = True
            self.next_btn.disabled = True
        else:
            self.prev_btn.disabled = (self.current_page == 0)
            self.next_btn.disabled = (self.current_page >= total_pages - 1)

        self.page_indicator.label = f"第 {self.current_page + 1} / {total_pages} 頁"

        self.add_item(self.prev_btn)
        self.add_item(self.page_indicator)
        self.add_item(self.next_btn)
        self.add_item(self.close_btn)

    async def prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_components()
            embed = self.build_overview_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    async def next_page(self, interaction: discord.Interaction):
        total_pages = max(1, (len(self.checkins) + self.per_page - 1) // self.per_page)
        if self.current_page < total_pages - 1:
            self.current_page += 1
            self.update_components()
            embed = self.build_overview_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    async def close_callback(self, interaction: discord.Interaction):
        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.delete_original_response()
            except Exception:
                try:
                    await interaction.response.edit_message(content="❌ 已關閉名單。", embed=None, view=None)
                except Exception:
                    for child in self.children:
                        child.disabled = True
                    try:
                        await interaction.response.edit_message(view=self)
                    except Exception:
                        pass
        self.stop()

    def build_overview_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="",
            description="請在下拉選單內選擇要查詢的通報記錄。",
            color=0x4cd4af
        )

        guild_id_str = str(self.guild.id)
        start_idx = self.current_page * self.per_page
        page_checkins = self.checkins[start_idx : start_idx + self.per_page]

        for i, c in enumerate(page_checkins):
            c_dt = parse_dt(c.get("created_at"))
            ts = int(c_dt.timestamp())
            title = c.get("title", "平安通報")
            responses = c.get("responses", {})
            guild_responses = {
                uid: d for uid, d in responses.items()
                if str(d.get("guild_id")) == guild_id_str
            }

            safe_count = sum(1 for d in guild_responses.values() if d.get("status") == "safe")
            affected_count = sum(1 for d in guild_responses.values() if d.get("status") == "affected")
            help_count = sum(1 for d in guild_responses.values() if d.get("status") == "help")

            field_title = f"{start_idx + i + 1}. {title} (<t:{ts}:D>)"
            field_val = f"`🟢` 平安人數：`{safe_count}`\n`🟡` 稍受影響：`{affected_count}`\n`🔴` 需要協助：`{help_count}`"
            embed.add_field(name=field_title, value=field_val, inline=False)

        return embed

class CheckinCategorySelect(discord.ui.Select):
    def __init__(self, current_cat: str, help_len: int, affected_len: int, safe_len: int):
        options = [
            discord.SelectOption(
                label=f"需要協助名單 ({help_len}人)",
                value="help",
                emoji="🔴",
                default=(current_cat == "help")
            ),
            discord.SelectOption(
                label=f"稍受影響名單 ({affected_len}人)",
                value="affected",
                emoji="🟡",
                default=(current_cat == "affected")
            ),
            discord.SelectOption(
                label=f"平安無事名單 ({safe_len}人)",
                value="safe",
                emoji="🟢",
                default=(current_cat == "safe")
            ),
        ]
        super().__init__(placeholder="選擇查看的類別...", options=options, min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.view.current_category = self.values[0]
        self.view.current_member_page = 0
        self.view.update_components()
        await interaction.response.edit_message(embed=self.view.build_page_embed(), view=self.view)

class CheckinDetailView(discord.ui.View):
    def __init__(self, checkin: dict, guild_id: int, author_id: int, parent_view: Optional[CheckinHistoryOverviewView] = None):
        super().__init__(timeout=300)
        self.checkin = checkin
        self.guild_id = guild_id
        self.author_id = author_id
        self.parent_view = parent_view
        self.current_category = "help"
        self.current_member_page = 0
        self.per_page = 20

        self._load_members()

        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=1)
        self.prev_btn.callback = self.prev_page

        self.page_indicator = discord.ui.Button(label="", style=discord.ButtonStyle.secondary, disabled=True, row=1)

        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=1)
        self.next_btn.callback = self.next_page

        self.back_btn = discord.ui.Button(label="返回記錄列表", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
        self.back_btn.callback = self.back_callback

        self.close_btn = discord.ui.Button(label="關閉", emoji="❌", style=discord.ButtonStyle.secondary, row=2)
        self.close_btn.callback = self.close_callback

        self.update_components()

    def _load_members(self):
        responses = self.checkin.get("responses", {})
        guild_id_str = str(self.guild_id)
        guild_responses = {
            uid: d for uid, d in responses.items()
            if str(d.get("guild_id")) == guild_id_str
        }

        self.help_items = []
        self.affected_items = []
        self.safe_items = []

        for uid, d in guild_responses.items():
            st = d.get("status")
            dt_str = ""
            if d.get("timestamp"):
                r_dt = parse_dt(d.get("timestamp"))
                dt_str = f" (<t:{int(r_dt.timestamp())}:R>)"

            if st == "help":
                info = d.get("info", {})
                loc = info.get("location", "未提供地點")
                sit = info.get("situation", "需要協助")
                contact = info.get("contact")
                contact_str = f" | 聯絡：{contact}" if contact else ""
                self.help_items.append(f"• <@{uid}>{dt_str}\n  狀況：{sit}\n  地點：{loc}{contact_str}")
            elif st == "affected":
                self.affected_items.append(f"• <@{uid}>{dt_str}")
            elif st == "safe":
                self.safe_items.append(f"• <@{uid}>{dt_str}")

    def get_current_list(self) -> list:
        if self.current_category == "help":
            return self.help_items
        elif self.current_category == "affected":
            return self.affected_items
        else:
            return self.safe_items

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 只有指令呼叫者可以操作此選單與按鈕！", ephemeral=True)
            return False
        return True

    def update_components(self):
        self.clear_items()

        # Row 0: 類別切換下拉選單
        self.add_item(CheckinCategorySelect(
            self.current_category,
            len(self.help_items),
            len(self.affected_items),
            len(self.safe_items)
        ))

        # Row 1: 人員名單翻頁按鈕（每頁20人）
        current_list = self.get_current_list()
        total_pages = max(1, (len(current_list) + self.per_page - 1) // self.per_page)

        if len(current_list) <= self.per_page:
            self.prev_btn.disabled = True
            self.next_btn.disabled = True
        else:
            self.prev_btn.disabled = (self.current_member_page == 0)
            self.next_btn.disabled = (self.current_member_page >= total_pages - 1)

        self.page_indicator.label = f"第 {self.current_member_page + 1} / {total_pages} 頁"

        self.add_item(self.prev_btn)
        self.add_item(self.page_indicator)
        self.add_item(self.next_btn)

        # Row 2: 返回按鈕（若有上一層總覽頁顯示）與關閉按鈕（放置在同一排右方）
        if self.parent_view:
            self.add_item(self.back_btn)
        self.add_item(self.close_btn)

    async def prev_page(self, interaction: discord.Interaction):
        if self.current_member_page > 0:
            self.current_member_page -= 1
            self.update_components()
            await interaction.response.edit_message(embed=self.build_page_embed(), view=self)

    async def next_page(self, interaction: discord.Interaction):
        current_list = self.get_current_list()
        total_pages = max(1, (len(current_list) + self.per_page - 1) // self.per_page)
        if self.current_member_page < total_pages - 1:
            self.current_member_page += 1
            self.update_components()
            await interaction.response.edit_message(embed=self.build_page_embed(), view=self)

    async def back_callback(self, interaction: discord.Interaction):
        if not self.parent_view:
            return
        self.parent_view.update_components()
        embed = self.parent_view.build_overview_embed()
        await interaction.response.edit_message(content="🏡 平安通報記錄", embed=embed, view=self.parent_view)

    async def close_callback(self, interaction: discord.Interaction):
        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.delete_original_response()
            except Exception:
                try:
                    await interaction.response.edit_message(content="❌ 已關閉名單。", embed=None, view=None)
                except Exception:
                    for child in self.children:
                        child.disabled = True
                    try:
                        await interaction.response.edit_message(view=self)
                    except Exception:
                        pass
        self.stop()

    def build_page_embed(self) -> discord.Embed:
        title = self.checkin.get("title", "平安通報")
        current_list = self.get_current_list()
        total_count = len(current_list)
        total_pages = max(1, (total_count + self.per_page - 1) // self.per_page)
        start_idx = self.current_member_page * self.per_page
        page_items = current_list[start_idx : start_idx + self.per_page]

        if self.current_category == "help":
            cat_name = "需要協助名單"
            cat_color = 0xe74c3c
            desc_header = f"此分類共 `{total_count}` 人回報需緊急協助（第 {self.current_member_page + 1}/{total_pages} 頁）：\n\n"
            empty_msg = "（本伺服器目前無需要協助人員）"
        elif self.current_category == "affected":
            cat_name = "稍受影響名單"
            cat_color = 0xf1c40f
            desc_header = f"此分類共 `{total_count}` 人回報稍受影響但人身安全（第 {self.current_member_page + 1}/{total_pages} 頁）：\n\n"
            empty_msg = "（本伺服器目前無稍受影響人員）"
        else:
            cat_name = "平安無事名單"
            cat_color = 0x2ecc71
            desc_header = f"此分類共 `{total_count}` 人回報平安（第 {self.current_member_page + 1}/{total_pages} 頁）：\n\n"
            empty_msg = "（本伺服器目前尚無回報）"

        body = "\n\n".join(page_items) if page_items else empty_msg
        embed = discord.Embed(
            title=f"{cat_name} - {title}",
            description=desc_header + body,
            color=cat_color
        )

        c_dt = parse_dt(self.checkin.get("created_at"))
        embed.set_footer(text=f"事件時間：{c_dt.strftime('%Y-%m-%d %H:%M')}")
        return embed

# ================= Cog 主體 =================

class SafeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ---------- 擁有者限定指令：/平安通報 ----------
    @app_commands.command(name="平安通報", description="（限擁有者）發起、結束或管理平安通報 Roll Call")
    @app_commands.rename(
        action="動作",
        checkin_id="通報id"
    )
    @app_commands.describe(
        action="選擇要執行的動作",
        checkin_id="（結束時必填）要結束的平安通報 ID"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="發起平安通報", value="start"),
        app_commands.Choice(name="進行中的通報列表", value="list"),
        app_commands.Choice(name="結束平安通報", value="close")
    ])
    @app_commands.guilds(*OWNER_GUILDS)
    async def safety_command(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        checkin_id: Optional[int] = None
    ):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        act = action.value

        if act == "start":
            # 使用彈出式表單（Modal）輸入，支援換行與原生必填防呆
            modal = SafetyCreateModal(self.bot, interaction.user.id)
            await interaction.response.send_modal(modal)

        elif act == "list":
            active_checkins = get_active_safety_checkins()
            if not active_checkins:
                await interaction.response.send_message("目前沒有進行中的平安通報。", ephemeral=True)
                return

            embed = discord.Embed(title="🏡 進行中的平安通報列表", color=0x4cd4af)
            for c in active_checkins:
                c_dt = parse_dt(c.get("created_at"))
                e_dt = parse_dt(c.get("expires_at"))
                resp_cnt = len(c.get("responses", {}))
                embed.add_field(
                    name=f"ID: `{c['id']}` - {c['title']}",
                    value=f"事件時間：<t:{int(c_dt.timestamp())}:f>\n截止時間：<t:{int(e_dt.timestamp())}:f>\n已回報總人數：`{resp_cnt}` 人",
                    inline=False
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        elif act == "close":
            if checkin_id is None:
                await interaction.response.send_message(
                    "❌ 結束平安通報時，必須填寫「通報ID」！\n您可使用 `/平安通報 動作:進行中的通報列表` 查詢進行中的通報 ID。",
                    ephemeral=True
                )
                return

            try:
                cid = int(checkin_id)
            except (ValueError, TypeError):
                await interaction.response.send_message("❌ 請輸入有效的整數通報 ID！", ephemeral=True)
                return

            checkin = get_safety_checkin(cid)
            if not checkin:
                await interaction.response.send_message(f"❌ 找不到 ID 為 `{cid}` 的平安通報。", ephemeral=True)
                return

            if checkin.get("status") == "closed":
                await interaction.response.send_message(
                    f"ℹ️ ID 為 `{cid}` 的平安通報「**{checkin.get('title')}**」已經結束統計，無須重複結束。",
                    ephemeral=True
                )
                return

            close_safety_checkin(cid)
            updated_checkin = get_safety_checkin(cid)
            msgs = get_safety_messages(cid)
            closed_view = SafetyCheckinView(cid, self.bot, is_closed=True)
            for item in msgs:
                try:
                    channel = self.bot.get_channel(item["channel_id"])
                    if channel:
                        msg = await channel.fetch_message(item["message_id"])
                        if msg:
                            new_embed = build_safety_embed(updated_checkin, channel.guild)
                            await msg.edit(embed=new_embed, view=closed_view)
                except Exception:
                    pass

            await interaction.response.send_message(
                f"✅ 已成功結束 ID: `{cid}`「**{checkin.get('title')}**」的平安通報統計！",
                ephemeral=True
            )

    @safety_command.autocomplete("checkin_id")
    async def safety_id_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[int]]:
        active_checkins = get_active_safety_checkins()
        choices = []
        for c in active_checkins:
            label = f"ID: {c['id']} - {c['title']}"[:100]
            if not current or current in str(c['id']) or current.lower() in c['title'].lower():
                choices.append(app_commands.Choice(name=label, value=c['id']))
        return choices[:25]

    # ---------- 公開指令：/平安通報記錄 ----------
    @app_commands.command(name="平安通報記錄", description="📋 查詢過去 1 年內的平安通報與回報名單 Safety Report History")
    async def safety_history_command(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用。", ephemeral=True)
            return

        checkins = get_recent_safety_checkins(days=365)
        if not checkins:
            await interaction.response.send_message("目前沒有過去 1 年內的平安通報記錄。", ephemeral=True)
            return

        view = CheckinHistoryOverviewView(checkins, interaction.guild, interaction.user.id)
        embed = view.build_overview_embed()
        await interaction.response.send_message(content="🏡 平安通報記錄", embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(SafeCog(bot))
