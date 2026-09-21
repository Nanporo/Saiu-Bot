import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import logging
import math
from modules.database import (
    get_all_settings,
    create_safety_checkin,
    record_safety_message,
    get_safety_checkin,
    get_active_safety_checkins,
    update_safety_checkin_response,
    close_safety_checkin,
    get_safety_messages
)

logger = logging.getLogger(__name__)

TAIWAN_TZ = timezone(timedelta(hours=8))

def parse_dt(val) -> datetime:
    if isinstance(val, datetime):
        return val.astimezone(TAIWAN_TZ) if val.tzinfo else val.replace(tzinfo=TAIWAN_TZ)
    val_str = str(val).strip()
    try:
        dt = datetime.fromisoformat(val_str)
        return dt.astimezone(TAIWAN_TZ) if dt.tzinfo else dt.replace(tzinfo=TAIWAN_TZ)
    except Exception:
        try:
            dt = datetime.strptime(val_str, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=TAIWAN_TZ)
        except Exception:
            return datetime.now(TAIWAN_TZ)

def count_human_members(guild: discord.Guild) -> int:
    """計算伺服器中的真實人類成員數（排除機器人）"""
    if not guild:
        return 1
    bot_count = sum(1 for m in guild.members if m.bot)
    total_members = guild.member_count or len(guild.members)
    return max(1, total_members - bot_count)

def build_safety_embed(checkin: dict, guild: discord.Guild) -> discord.Embed:
    """建構發送給指定伺服器的平安通報 Embed"""
    title = checkin.get("title", "平安通報")
    desc_base = checkin.get("description", "")
    status = checkin.get("status", "active")
    responses = checkin.get("responses", {})

    # 僅計算該伺服器成員的回報
    guild_id_str = str(guild.id) if guild else ""
    guild_responses = {
        uid: data for uid, data in responses.items()
        if str(data.get("guild_id")) == guild_id_str
    }

    safe_count = sum(1 for d in guild_responses.values() if d.get("status") == "safe")
    affected_count = sum(1 for d in guild_responses.values() if d.get("status") == "affected")
    help_count = sum(1 for d in guild_responses.values() if d.get("status") == "help")
    reported_count = safe_count + affected_count + help_count

    human_members = count_human_members(guild)

    desc_clean = str(desc_base).strip() if desc_base else ""
    desc_part = f"{desc_clean}\n\n" if desc_clean else ""
    full_desc = (
        f"{desc_part}"
        f"請注意自身安全，並留意最新消息。\n\n"
        f"`🟢` 平安人數：`{safe_count}`\n"
        f"`🟡` 稍受影響：`{affected_count}`\n"
        f"`🔴` 需要協助：`{help_count}`\n\n"
        f"伺服器目前已有 `{reported_count}` / `{human_members}` 人回報。\n\n"
        f"-# ⚠️ 求助表單只會保存在小裁雨機器人本地，不會自動連線給消防救援單位。\n-# 若有緊急危險，請務必立即撥打 **119** 或 **110** 求助！"
    )

    embed = discord.Embed(
        title=title,
        description=full_desc,
        color=0x4cd4af
    )

    exp_dt = parse_dt(checkin.get("expires_at"))
    exp_str = exp_dt.strftime("%Y-%m-%d %H:%M")
    if status == "closed":
        embed.set_footer(text=f"截止時間：{exp_str} • 已結束統計")
    else:
        embed.set_footer(text=f"截止時間：{exp_str}")

    return embed

class SafetyHelpModal(discord.ui.Modal):
    def __init__(self, checkin_id: int):
        super().__init__(title="回報需要協助")
        self.checkin_id = checkin_id

    location = discord.ui.TextInput(
        label="目前所在位置",
        placeholder="例如：花蓮市中正路、受困大樓、避難所等",
        required=True,
        max_length=100
    )

    situation = discord.ui.TextInput(
        label="遭遇狀況與所需協助",
        placeholder="例如：房屋受損、停電、受困、需要飲用水或醫療協助等。",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=300
    )

    contact = discord.ui.TextInput(
        label="緊急聯絡方式或備註 (選填)",
        placeholder="例如：手機號碼、同行人數等",
        required=False,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        checkin = get_safety_checkin(self.checkin_id)
        if not checkin or checkin.get("status") == "closed":
            await interaction.response.send_message("⚠️ 此平安通報已結束統計或已關閉。", ephemeral=True)
            return

        info = {
            "location": str(self.location.value).strip(),
            "situation": str(self.situation.value).strip(),
            "contact": str(self.contact.value).strip() if self.contact.value else ""
        }

        update_safety_checkin_response(
            self.checkin_id,
            interaction.user.id,
            interaction.guild_id,
            "help",
            info
        )

        await interaction.response.send_message(
            "🔴 已記錄您的協助需求回報，請盡量保持通訊暢通並留意自身安全！\n"
            "⚠️ **注意**：求助表單只會保存在小裁雨機器人本地，無法同時連線給消防救援單位。若有緊急危險，請務必立即撥打 **119** 或 **110** 求助！",
            ephemeral=True
        )

        # 動態即時更新推播訊息上的統計人數
        try:
            if interaction.message and interaction.guild:
                updated_checkin = get_safety_checkin(self.checkin_id)
                new_embed = build_safety_embed(updated_checkin, interaction.guild)
                await interaction.message.edit(embed=new_embed)
        except Exception as e:
            logger.warning(f"⚠️ 更新平安通報面板失敗: {e}")

class SafetyCheckinView(discord.ui.View):
    def __init__(self, checkin_id: int, bot=None, is_closed: bool = False):
        super().__init__(timeout=None)
        self.checkin_id = checkin_id
        self.bot = bot
        self.is_closed = is_closed

        # 第一排：三種回報狀態（結束統計後不可選）
        safe_btn = discord.ui.Button(
            label="平安無事",
            emoji="🟢",
            style=discord.ButtonStyle.success,
            custom_id=f"safety_checkin:safe:{checkin_id}",
            disabled=is_closed,
            row=0
        )
        safe_btn.callback = self.safe_callback
        self.add_item(safe_btn)

        affected_btn = discord.ui.Button(
            label="稍受影響 / 安全",
            emoji="🟡",
            style=discord.ButtonStyle.secondary,
            custom_id=f"safety_checkin:affected:{checkin_id}",
            disabled=is_closed,
            row=0
        )
        affected_btn.callback = self.affected_callback
        self.add_item(affected_btn)

        help_btn = discord.ui.Button(
            label="需要協助",
            emoji="⚠️",
            style=discord.ButtonStyle.danger,
            custom_id=f"safety_checkin:help:{checkin_id}",
            disabled=is_closed,
            row=0
        )
        help_btn.callback = self.help_callback
        self.add_item(help_btn)

        # 第二排：查看名單（結束統計後依然可以查看）
        list_btn = discord.ui.Button(
            label="查看目前名單",
            style=discord.ButtonStyle.primary,
            custom_id=f"safety_checkin:list:{checkin_id}",
            row=1
        )
        list_btn.callback = self.list_callback
        self.add_item(list_btn)

    async def safe_callback(self, interaction: discord.Interaction):
        checkin = get_safety_checkin(self.checkin_id)
        if not checkin or checkin.get("status") == "closed":
            await interaction.response.send_message("⚠️ 此平安通報已結束統計或已關閉。", ephemeral=True)
            try:
                if interaction.message:
                    closed_view = SafetyCheckinView(self.checkin_id, self.bot, is_closed=True)
                    if checkin and interaction.guild:
                        new_embed = build_safety_embed(checkin, interaction.guild)
                        await interaction.message.edit(embed=new_embed, view=closed_view)
                    else:
                        await interaction.message.edit(view=closed_view)
            except Exception:
                pass
            return

        update_safety_checkin_response(
            self.checkin_id,
            interaction.user.id,
            interaction.guild_id,
            "safe"
        )

        await interaction.response.send_message(
            "✅ 已記錄您的平安回報，請注意自身安全並留意最新訊息！",
            ephemeral=True
        )

        try:
            if interaction.message and interaction.guild:
                updated_checkin = get_safety_checkin(self.checkin_id)
                new_embed = build_safety_embed(updated_checkin, interaction.guild)
                await interaction.message.edit(embed=new_embed)
        except Exception as e:
            logger.warning(f"⚠️ 更新平安通報面板失敗: {e}")

    async def affected_callback(self, interaction: discord.Interaction):
        checkin = get_safety_checkin(self.checkin_id)
        if not checkin or checkin.get("status") == "closed":
            await interaction.response.send_message("⚠️ 此平安通報已結束統計或已關閉。", ephemeral=True)
            try:
                if interaction.message:
                    closed_view = SafetyCheckinView(self.checkin_id, self.bot, is_closed=True)
                    if checkin and interaction.guild:
                        new_embed = build_safety_embed(checkin, interaction.guild)
                        await interaction.message.edit(embed=new_embed, view=closed_view)
                    else:
                        await interaction.message.edit(view=closed_view)
            except Exception:
                pass
            return

        update_safety_checkin_response(
            self.checkin_id,
            interaction.user.id,
            interaction.guild_id,
            "affected"
        )

        await interaction.response.send_message(
            "🟡 已記錄您的平安回報（稍受影響 / 安全），請注意自身安全並留意最新訊息！",
            ephemeral=True
        )

        try:
            if interaction.message and interaction.guild:
                updated_checkin = get_safety_checkin(self.checkin_id)
                new_embed = build_safety_embed(updated_checkin, interaction.guild)
                await interaction.message.edit(embed=new_embed)
        except Exception as e:
            logger.warning(f"⚠️ 更新平安通報面板失敗: {e}")

    async def help_callback(self, interaction: discord.Interaction):
        checkin = get_safety_checkin(self.checkin_id)
        if not checkin or checkin.get("status") == "closed":
            await interaction.response.send_message("⚠️ 此平安通報已結束統計或已關閉。", ephemeral=True)
            try:
                if interaction.message:
                    closed_view = SafetyCheckinView(self.checkin_id, self.bot, is_closed=True)
                    if checkin and interaction.guild:
                        new_embed = build_safety_embed(checkin, interaction.guild)
                        await interaction.message.edit(embed=new_embed, view=closed_view)
                    else:
                        await interaction.message.edit(view=closed_view)
            except Exception:
                pass
            return

        modal = SafetyHelpModal(self.checkin_id)
        await interaction.response.send_modal(modal)

    async def list_callback(self, interaction: discord.Interaction):
        checkin = get_safety_checkin(self.checkin_id)
        if not checkin:
            await interaction.response.send_message("❌ 找不到此平安通報記錄。", ephemeral=True)
            return

        # 根據伺服器人數來動態調整顯示方式：若大於50人，採用類似 /平安通報記錄 的詳細分頁模式；小於等於50人，保持現在的顯示方式
        human_members = count_human_members(interaction.guild)
        if human_members > 50:
            from cogs.safe import CheckinDetailView
            view = CheckinDetailView(checkin, interaction.guild_id, interaction.user.id, parent_view=None)
            embed = view.build_page_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        responses = checkin.get("responses", {})
        guild_id_str = str(interaction.guild_id)
        guild_responses = {
            uid: d for uid, d in responses.items()
            if str(d.get("guild_id")) == guild_id_str
        }

        help_users = []
        affected_users = []
        safe_users = []

        for uid, d in guild_responses.items():
            st = d.get("status")
            if st == "help":
                info = d.get("info", {})
                loc = info.get("location", "未提供地點")
                sit = info.get("situation", "需要協助")
                contact = info.get("contact")
                contact_str = f" | 聯絡：{contact}" if contact else ""
                help_users.append(f"• <@{uid}>: {sit} (地點：{loc}{contact_str})")
            elif st == "affected":
                affected_users.append(f"<@{uid}>")
            elif st == "safe":
                safe_users.append(f"<@{uid}>")

        embed = discord.Embed(
            title=f"本伺服器回報名單 - {checkin.get('title')}",
            color=0x4cd4af
        )

        help_text = "\n".join(help_users) if help_users else "（無）"
        affected_text = "、".join(affected_users) if affected_users else "（無）"
        safe_text = "、".join(safe_users) if safe_users else "（無）"

        # 避免 Discord 欄位超過 1024 字元
        if len(help_text) > 1024:
            help_text = help_text[:1020] + "..."
        if len(affected_text) > 1024:
            affected_text = affected_text[:1020] + "..."
        if len(safe_text) > 1024:
            safe_text = safe_text[:1020] + "..."

        embed.add_field(name=f"🔴 需要協助 ({len(help_users)} 人)", value=help_text, inline=False)
        embed.add_field(name=f"🟡 稍受影響 ({len(affected_users)} 人)", value=affected_text, inline=False)
        embed.add_field(name=f"🟢 平安無事 ({len(safe_users)} 人)", value=safe_text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def broadcast_safety_checkin(bot, title: str, description: str, hours: int = 48, created_by: str = None) -> int:
    """全域推播平安通報至所有開啟該功能的伺服器"""
    try:
        if hours is None or math.isnan(hours) or math.isinf(hours):
            hours = 48
        hours = max(1, min(720, int(hours)))
    except Exception:
        hours = 48
    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    checkin_id = create_safety_checkin(title, description, expires_at, created_by)

    # 註冊 persistent view
    view = SafetyCheckinView(checkin_id, bot)
    bot.add_view(view)

    settings = get_all_settings()
    sent_count = 0

    for guild_id_str, s in settings.items():
        if not s.get("safety_alerts"):
            continue

        alerts = s.get("safety_alerts")
        ch_id = alerts.get("channel_id") if isinstance(alerts, dict) else alerts
        if not ch_id:
            continue

        try:
            channel = bot.get_channel(int(ch_id))
            if not channel:
                try:
                    channel = await bot.fetch_channel(int(ch_id))
                except Exception:
                    channel = None

            if not channel:
                continue

            guild = channel.guild
            checkin_data = get_safety_checkin(checkin_id)
            embed = build_safety_embed(checkin_data, guild)

            content = "🏡 平安通報系統"
            role_id = s.get("safety_mention_role_id")
            if role_id:
                content += f" <@&{role_id}>"

            msg = await channel.send(content=content, embed=embed, view=view)
            record_safety_message(checkin_id, guild.id, channel.id, msg.id)
            sent_count += 1
        except Exception as e:
            logger.warning(f"⚠️ 平安通報推播至伺服器 {guild_id_str} 失敗: {e}")

    logger.info(f"📢 [平安通報] 已成功推播「{title}」至 {sent_count} 個伺服器。")
    return sent_count

async def check_and_backfill_safety_checkin(bot, guild_id: int, channel_id: int, role_id: int = None) -> int:
    """檢查是否有進行中且未過期的平安通報，若指定伺服器尚未發送過，自動補發至該伺服器頻道"""
    active_checkins = get_active_safety_checkins()
    if not active_checkins:
        return 0

    now = datetime.now(timezone.utc)
    sent_count = 0
    for c in active_checkins:
        exp_dt = parse_dt(c.get("expires_at"))
        if now >= exp_dt or c.get("status") == "closed":
            continue

        cid = c["id"]
        existing_msgs = get_safety_messages(cid)
        # 若該伺服器在此次通報中已有發送記錄，則不重複補發
        if any(str(m["guild_id"]) == str(guild_id) for m in existing_msgs):
            continue

        try:
            channel = bot.get_channel(int(channel_id))
            if not channel:
                try:
                    channel = await bot.fetch_channel(int(channel_id))
                except Exception:
                    channel = None

            if not channel:
                continue

            guild = channel.guild
            checkin_data = get_safety_checkin(cid)
            embed = build_safety_embed(checkin_data, guild)

            content = "🏡 平安通報系統"
            if role_id:
                content += f" <@&{role_id}>"

            view = SafetyCheckinView(cid, bot)
            msg = await channel.send(content=content, embed=embed, view=view)
            record_safety_message(cid, guild.id, channel.id, msg.id)
            sent_count += 1
            logger.info(f"📢 [平安通報] 伺服器 {guild.id} 設定開啟平安通報，已自動補發進行中的通報「{c.get('title')}」(ID: {cid}) 至頻道 {channel.id}")
        except Exception as e:
            logger.warning(f"⚠️ 補發平安通報至伺服器 {guild_id} 失敗: {e}")

    return sent_count

class AlertSafeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.auto_close_expired_task.start()

    def cog_unload(self):
        self.auto_close_expired_task.cancel()

    async def cog_load(self):
        # 啟動時為所有進行中的平安通報重新掛載 persistent views
        active_checkins = get_active_safety_checkins()
        for c in active_checkins:
            self.bot.add_view(SafetyCheckinView(c["id"], self.bot))
        logger.info(f"🔄 [平安通報] 已為 {len(active_checkins)} 個進行中的平安通報註冊 Persistent Views。")

    @tasks.loop(minutes=1.0)
    async def auto_close_expired_task(self):
        """定期檢查並自動結束過期的平安通報"""
        now = datetime.now(timezone.utc)
        active_checkins = get_active_safety_checkins()
        for c in active_checkins:
            exp_dt = parse_dt(c.get("expires_at"))
            if now >= exp_dt:
                checkin_id = c["id"]
                close_safety_checkin(checkin_id)
                logger.info(f"⏰ [平安通報] 事件「{c.get('title')}」(ID: {checkin_id}) 已到達截止時間，已自動結束統計。")

                # 更新推播訊息（將按鈕改為不可選）
                msgs = get_safety_messages(checkin_id)
                updated_checkin = get_safety_checkin(checkin_id)
                closed_view = SafetyCheckinView(checkin_id, self.bot, is_closed=True)
                for item in msgs:
                    try:
                        channel = self.bot.get_channel(item["channel_id"])
                        if channel:
                            msg = await channel.fetch_message(item["message_id"])
                            if msg and msg.embeds:
                                new_embed = build_safety_embed(updated_checkin, channel.guild)
                                await msg.edit(embed=new_embed, view=closed_view)
                    except Exception as e:
                        logger.debug(f"⚠️ 更新過期通報訊息失敗: {e}")

    @auto_close_expired_task.before_loop
    async def before_auto_close(self):
        await self.bot.wait_until_ready()
        # 啟動後檢查是否有已開啟平安通報但尚未收到進行中通報的伺服器，自動進行補發
        try:
            settings = get_all_settings()
            active_checkins = get_active_safety_checkins()
            if active_checkins:
                for guild_id_str, s in settings.items():
                    alerts = s.get("safety_alerts")
                    if not alerts:
                        continue
                    ch_id = alerts.get("channel_id") if isinstance(alerts, dict) else alerts
                    if ch_id:
                        role_id = s.get("safety_mention_role_id")
                        await check_and_backfill_safety_checkin(self.bot, int(guild_id_str), int(ch_id), role_id)
        except Exception as e:
            logger.warning(f"⚠️ 啟動時檢查補發平安通報發生錯誤: {e}")

async def setup(bot):
    await bot.add_cog(AlertSafeCog(bot))
