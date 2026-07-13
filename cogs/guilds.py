import discord
from discord.ext import commands
from discord import app_commands
import json
from modules.database import get_all_settings
from modules.ownercheck import is_owner
from typing import Optional

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    OWNER_SERVER_ID = int(config.get('OWNER_SERVER_ID', 0))
except Exception:
    OWNER_SERVER_ID = 0

OWNER_GUILDS = [discord.Object(id=OWNER_SERVER_ID)] if OWNER_SERVER_ID else []

class ServerSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="查看伺服器詳細資訊", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        guild_id = int(self.values[0])
        await self.view.show_detail(interaction, guild_id)

class GuildsView(discord.ui.View):
    def __init__(self, bot, guilds, guild_settings, author_id: int, show_stats: bool, stats_data: dict = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.guilds = guilds
        self.guild_settings = guild_settings
        self.author_id = author_id
        self.show_stats = show_stats
        self.stats_data = stats_data
        
        self.per_page = 10
        self.max_list_pages = max(1, (len(self.guilds) + self.per_page - 1) // self.per_page)
        self.total_pages = self.max_list_pages + (1 if self.show_stats else 0)
        self.current_page = 0
        self.is_detail_mode = False
        self.current_detail_guild_id = None
        self.detail_page = 0
        
        self.prev_button = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=0)
        self.prev_button.callback = self.prev_page
        self.page_indicator = discord.ui.Button(label="第 1 頁", style=discord.ButtonStyle.secondary, disabled=True, row=0)
        self.next_button = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=0)
        self.next_button.callback = self.next_page
        
        self.back_button = discord.ui.Button(label="返回", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
        self.back_button.callback = self.back_to_list
        
        self.toggle_eew_button = discord.ui.Button(label="切換地震預警許可", emoji="🚨", style=discord.ButtonStyle.danger, row=1)
        self.toggle_eew_button.callback = self.toggle_eew_permission
        
        self.back_to_overview_btn = discord.ui.Button(label="回概覽", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
        self.back_to_overview_btn.callback = self.back_to_overview
        
        self.select_menu = None
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def get_guild_marks(self, guild_id_str):
        g_settings = self.guild_settings.get(guild_id_str, {})
        marks = ""
        if g_settings.get("auto_push", False): marks += "📢 "
        if "rain_alerts" in g_settings: marks += "🌧️"
        if "temp_alerts" in g_settings: marks += "🌡️"
        if "eq_alerts" in g_settings: marks += "🏚️"
        if "typhoon_alerts" in g_settings: marks += "🌀"
        if "suspension_alerts" in g_settings: marks += "🎒"
        if g_settings.get("cbs_alerts", False): marks += "⚠️"
        if "eew_alerts" in g_settings: marks += "🚨"
        if "aqi_alerts" in g_settings: marks += "😷"
        return marks

    def build_stats_embed(self):
        desc = (
            f"🌐 **伺服器數** {self.stats_data['total_guilds']}\n"
            f"👥 **總使用者** {self.stats_data['total_members']}\n"
            f"🔔 **開啟推送** {self.stats_data['push']}\n"
            f"📢 **接收廣播** {self.stats_data['broadcast']}\n"
            f"⚠️ **災防告警** {self.stats_data['cbs']}\n"
            f"🚨 **強震警報** {self.stats_data['eew']}\n"
            f"🌧️ **降雨預警** {self.stats_data['rain']}\n"
            f"🌡️ **氣溫預警** {self.stats_data['temp']}\n"
            f"🏚️ **地震通知** {self.stats_data['eq']}\n"
            f"🌀 **颱風機率** {self.stats_data['typhoon']}\n"
            f"🎒 **停班停課** {self.stats_data['suspension']}\n"
            f"😷 **空氣品質** {self.stats_data['aqi']}"
        )
        embed = discord.Embed(title="機器人狀態與統計", description=desc, color=0x41809b)
        return embed

    def build_list_embed(self, list_page_index):
        start_idx = list_page_index * self.per_page
        end_idx = start_idx + self.per_page
        page_guilds = self.guilds[start_idx:end_idx]
        
        embed = discord.Embed(title="伺服器列表", color=0x2ecc71)
        for i, guild in enumerate(page_guilds):
            marks = self.get_guild_marks(str(guild.id))
            if guild.owner_id:
                owner_name = f"<@{guild.owner_id}>"
            else:
                owner_name = "未知"
            embed.add_field(
                name=f"{start_idx + i + 1} : {guild.name} {marks}".strip(),
                value=f"ID: `{guild.id}`\n擁有者: {owner_name}\n人數: `{guild.member_count}` 人",
                inline=False
            )
        return embed, page_guilds

    async def build_detail_embed(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return discord.Embed(title="❌ 伺服器不存在", description="機器人可能已經退出該伺服器。", color=discord.Color.red())
            
        embed = discord.Embed(title=f"伺服器詳細資訊：{guild.name}", color=0x3498db)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
            
        if guild.owner_id:
            owner_name = f"<@{guild.owner_id}>"
        else:
            owner_name = "未知"
            
        joined_time = f"<t:{int(guild.me.joined_at.timestamp())}:f>" if guild.me and guild.me.joined_at else "未知"
        created_time = f"<t:{int(guild.created_at.timestamp())}:f>" if guild.created_at else "未知"
        
        g_settings = self.guild_settings.get(str(guild.id), {})
        
        def format_channel(cid):
            channel = guild.get_channel(int(cid))
            if channel:
                return f"<#{cid}> ({channel.name})"
            else:
                return f"<#{cid}> (未知頻道)"
        
        if getattr(self, "detail_page", 0) == 0:
            # 尋找已開啟推送的頻道
            push_channels = set()
            if g_settings.get("target_channel_ids"):
                for cid in g_settings["target_channel_ids"]:
                    push_channels.add(format_channel(cid))
                
            alert_keys = ["rain_alerts", "temp_alerts", "eq_alerts", "typhoon_alerts", "suspension_alerts", "cbs_alerts", "aqi_alerts"]
            for key in alert_keys:
                alerts = g_settings.get(key, {})
                if isinstance(alerts, dict):
                    for loc, data in alerts.items():
                        if isinstance(data, dict) and "channel_id" in data:
                            push_channels.add(format_channel(data['channel_id']))
                        elif isinstance(data, int):
                            push_channels.add(format_channel(data))
                            
            push_channel_str = ", ".join(list(push_channels)) if push_channels else "無"
            marks = self.get_guild_marks(str(guild.id))
            
            eew_auth = g_settings.get("eew_authorized", False)
            eew_status = "`🟢` 已許可" if eew_auth else "`🔴` 未許可"
            
            embed.add_field(name="基本資訊", value=f"ID: `{guild.id}`\n擁有者: {owner_name}\n人數: `{guild.member_count}` 人\n建立時間: {created_time}\n加入時間: {joined_time}", inline=False)
            embed.add_field(name="設定狀態", value=f"啟用功能: {marks if marks else '無'}\n推送頻道: {push_channel_str}", inline=False)
            embed.add_field(name="強震即時警報許可", value=eew_status, inline=False)
        else:
            settings_desc = ""
            
            push_channels = []
            if g_settings.get("target_channel_ids"):
                push_channels = [format_channel(cid) for cid in g_settings["target_channel_ids"]]
            
            if push_channels:
                settings_desc += f"**廣播接收頻道**: {', '.join(push_channels)}\n"
                
            if g_settings.get("auto_push"):
                settings_desc += "**自動推送廣播**: `開啟`\n"

            detailed_items = []
            alert_mapping = {
                "rain_alerts": "🌧️ 降雨",
                "temp_alerts": "🌡️ 氣溫",
                "eq_alerts": "🏚️ 地震",
                "typhoon_alerts": "🌀 颱風",
                "suspension_alerts": "🎒 停班課",
                "eew_alerts": "🚨 強震",
                "aqi_alerts": "😷 空品",
                "cbs_alerts": "⚠️ 災防告警"
            }
            
            processed_names = set()
            for key, name in alert_mapping.items():
                alerts = g_settings.get(key)
                if alerts:
                    if name not in processed_names:
                        settings_desc += f"**{name}**: `開啟`\n"
                        processed_names.add(name)
                        
                    if isinstance(alerts, dict):
                        loc_details = []
                        for loc, data in alerts.items():
                            if isinstance(data, dict) and "channel_id" in data:
                                loc_details.append(f"{loc} {format_channel(data['channel_id'])}")
                            elif isinstance(data, int):
                                loc_details.append(f"{loc} {format_channel(data)}")
                            else:
                                loc_details.append(str(loc))
                        if loc_details:
                            detailed_items.append((name, "\n".join(loc_details)))
                            

            if not settings_desc:
                settings_desc = "無任何詳細設定。"

            embed.add_field(name="詳細設定與狀態", value=settings_desc[:1024], inline=False)
            
            if detailed_items:
                for name, details in detailed_items:
                    val = details if len(details) <= 1024 else details[:1020] + "..."
                    embed.add_field(name=f"{name} 📍 地區與頻道", value=val, inline=False)

        return embed

    def update_components(self):
        self.clear_items()
        
        if self.is_detail_mode:
            self.prev_button.disabled = self.detail_page == 0
            self.next_button.disabled = self.detail_page == 1
            self.page_indicator.label = f"第 {self.detail_page + 1} / 2 頁"
            
            self.add_item(self.prev_button)
            self.add_item(self.page_indicator)
            self.add_item(self.next_button)
            self.add_item(self.back_button)
            self.add_item(self.toggle_eew_button)
            return

        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == self.total_pages - 1
        self.page_indicator.label = f"第 {self.current_page + 1} / {self.total_pages} 頁"
        
        if self.total_pages > 1:
            self.add_item(self.prev_button)
            self.add_item(self.page_indicator)
            self.add_item(self.next_button)

        if self.current_page > 0 or not self.show_stats:
            self.add_item(self.back_to_overview_btn)

        # 決定當前頁面要顯示哪些選項到 Select
        if self.show_stats and self.current_page == 0:
            pass
        else:
            list_page_index = self.current_page - (1 if self.show_stats else 0)
            start_idx = list_page_index * self.per_page
            end_idx = start_idx + self.per_page
            page_guilds = self.guilds[start_idx:end_idx]
            
            if page_guilds:
                options = []
                for guild in page_guilds:
                    options.append(discord.SelectOption(label=guild.name[:100], value=str(guild.id), description=f"ID: {guild.id}"))
                self.select_menu = ServerSelect(options)
                self.add_item(self.select_menu)

    async def get_current_embed(self):
        if self.is_detail_mode:
            return await self.build_detail_embed(self.current_detail_guild_id)
            
        if self.show_stats and self.current_page == 0:
            return self.build_stats_embed()
        else:
            list_page_index = self.current_page - (1 if self.show_stats else 0)
            embed, _ = self.build_list_embed(list_page_index)
            return embed

    async def prev_page(self, interaction: discord.Interaction):
        if self.is_detail_mode:
            self.detail_page -= 1
        else:
            self.current_page -= 1
        self.update_components()
        embed = await self.get_current_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def next_page(self, interaction: discord.Interaction):
        if self.is_detail_mode:
            self.detail_page += 1
        else:
            self.current_page += 1
        self.update_components()
        embed = await self.get_current_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def show_detail(self, interaction: discord.Interaction, guild_id: int):
        self.is_detail_mode = True
        self.current_detail_guild_id = guild_id
        self.detail_page = 0
        self.update_components()
        embed = await self.build_detail_embed(guild_id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def toggle_eew_permission(self, interaction: discord.Interaction):
        if not self.current_detail_guild_id:
            return
        gid_str = str(self.current_detail_guild_id)
        if gid_str not in self.guild_settings:
            self.guild_settings[gid_str] = {}
        
        current_status = self.guild_settings[gid_str].get("eew_authorized", False)
        self.guild_settings[gid_str]["eew_authorized"] = not current_status
        
        # We need to save settings here, import save_all_settings dynamically or add to top
        from modules.database import save_all_settings
        save_all_settings(self.guild_settings)
        
        embed = await self.build_detail_embed(self.current_detail_guild_id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def back_to_list(self, interaction: discord.Interaction):
        self.is_detail_mode = False
        self.update_components()
        embed = await self.get_current_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def back_to_overview(self, interaction: discord.Interaction):
        if not self.show_stats:
            self.show_stats = True
            self.total_pages = self.max_list_pages + 1
        self.current_page = 0
        self.update_components()
        embed = await self.get_current_embed()
        await interaction.response.edit_message(embed=embed, view=self)


class GuildsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="伺服器列表", description="（限擁有者）顯示機器人加入的伺服器列表與狀態 Server Guilds")
    @app_commands.rename(sort_by="排序方式", search="搜尋關鍵字", feature_filter="功能篩選", guild_id="伺服器id")
    @app_commands.describe(sort_by="選擇列表排序方式", search="輸入伺服器名稱或 ID", feature_filter="篩選啟用特定功能的伺服器", guild_id="直接輸入伺服器 ID 查看詳情")
    @app_commands.choices(
        sort_by=[
            app_commands.Choice(name="人數最多", value="members_desc"),
            app_commands.Choice(name="人數最少", value="members_asc"),
            app_commands.Choice(name="最新加入", value="joined_desc"),
            app_commands.Choice(name="最早加入", value="joined_asc")
        ],
        feature_filter=[
            app_commands.Choice(name="所有", value="all"),
            app_commands.Choice(name="有開啟推送", value="push"),
            app_commands.Choice(name="接收廣播", value="auto_push"),
            app_commands.Choice(name="降雨預警", value="rain"),
            app_commands.Choice(name="氣溫預警", value="temp"),
            app_commands.Choice(name="地震通知", value="eq"),
            app_commands.Choice(name="颱風機率", value="typhoon"),
            app_commands.Choice(name="停班課通知", value="suspension"),
            app_commands.Choice(name="災防告警", value="cbs"),
            app_commands.Choice(name="強震即時警報", value="eew"),
            app_commands.Choice(name="空氣品質預警", value="aqi")
        ]
    )
    @app_commands.guilds(*OWNER_GUILDS)
    async def guilds_command(self, interaction: discord.Interaction, 
                             sort_by: app_commands.Choice[str] = None, 
                             search: str = None, 
                             feature_filter: app_commands.Choice[str] = None, 
                             guild_id: str = None):
        # 權限檢查
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            guild_settings = get_all_settings()
        except Exception:
            guild_settings = {}

        if guild_id:
            try:
                gid = int(guild_id)
                dummy_view = GuildsView(self.bot, [], guild_settings, interaction.user.id, False)
                dummy_view.is_detail_mode = True
                dummy_view.update_components()
                embed = await dummy_view.build_detail_embed(gid)
                await interaction.followup.send(embed=embed)
            except ValueError:
                await interaction.followup.send("❌ 錯誤的伺服器 ID 格式。")
            return

        # 篩選
        filtered_guilds = []
        for guild in self.bot.guilds:
            if search:
                if search.lower() not in guild.name.lower() and search != str(guild.id):
                    continue
            
            if feature_filter and feature_filter.value != "all":
                g_settings = guild_settings.get(str(guild.id), {})
                val = feature_filter.value
                if val == "push" and not g_settings.get("target_channel_ids"):
                    continue
                elif val == "auto_push" and not g_settings.get("auto_push", False):
                    continue
                elif val == "rain" and not "rain_alerts" in g_settings:
                    continue
                elif val == "temp" and not "temp_alerts" in g_settings:
                    continue
                elif val == "eq" and not "eq_alerts" in g_settings:
                    continue
                elif val == "typhoon" and not "typhoon_alerts" in g_settings:
                    continue
                elif val == "suspension" and not "suspension_alerts" in g_settings:
                    continue
                elif val == "cbs" and not g_settings.get("cbs_alerts", False):
                    continue
                elif val == "eew" and not "eew_alerts" in g_settings:
                    continue
                elif val == "aqi" and not "aqi_alerts" in g_settings:
                    continue
                    
            filtered_guilds.append(guild)

        # 排序
        sort_val = sort_by.value if sort_by else "members_desc"
        if sort_val == "members_desc":
            filtered_guilds.sort(key=lambda g: g.member_count, reverse=True)
        elif sort_val == "members_asc":
            filtered_guilds.sort(key=lambda g: g.member_count, reverse=False)
        elif sort_val == "joined_desc":
            filtered_guilds.sort(key=lambda g: g.me.joined_at if g.me and g.me.joined_at else discord.utils.utcnow(), reverse=True)
        elif sort_val == "joined_asc":
            filtered_guilds.sort(key=lambda g: g.me.joined_at if g.me and g.me.joined_at else discord.utils.utcnow(), reverse=False)

        if not filtered_guilds:
            await interaction.followup.send("❌ 找不到符合條件的伺服器。")
            return

        # 統計
        total_members = sum(g.member_count for g in self.bot.guilds)
        stats_data = {
            "total_guilds": len(self.bot.guilds),
            "total_members": total_members,
            "push": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and guild_settings[str(g.id)].get("target_channel_ids")),
            "broadcast": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and guild_settings[str(g.id)].get("auto_push", False)),
            "rain": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and "rain_alerts" in guild_settings[str(g.id)]),
            "temp": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and "temp_alerts" in guild_settings[str(g.id)]),
            "eq": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and "eq_alerts" in guild_settings[str(g.id)]),
            "typhoon": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and "typhoon_alerts" in guild_settings[str(g.id)]),
            "suspension": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and "suspension_alerts" in guild_settings[str(g.id)]),
            "cbs": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and guild_settings[str(g.id)].get("cbs_alerts", False)),
            "eew": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and "eew_alerts" in guild_settings[str(g.id)]),
            "aqi": sum(1 for g in self.bot.guilds if str(g.id) in guild_settings and "aqi_alerts" in guild_settings[str(g.id)]),
        }

        # 如果沒有進行任何篩選，則顯示首頁統計
        show_stats = (search is None and (feature_filter is None or feature_filter.value == "all"))
        
        view = GuildsView(self.bot, filtered_guilds, guild_settings, interaction.user.id, show_stats, stats_data)
        embed = await view.get_current_embed()
        
        message_content = "🤖 機器人伺服器狀態"
        await interaction.followup.send(content=message_content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(GuildsCog(bot))