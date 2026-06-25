import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import feedparser
import re
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

class NewsView(discord.ui.View):
    def __init__(self, news_list, author_id: int):
        super().__init__(timeout=300)
        self.news_list = news_list
        self.author_id = author_id
        
        # 建立下拉選單
        options = [discord.SelectOption(label="📰 新聞概覽", value="overview", default=True)]
        for i, news in enumerate(self.news_list):
            title = news.get("title", "未知標題")
            if len(title) > 40:
                title = title[:37] + "..."
            options.append(discord.SelectOption(label=title, value=str(i)))
            
        self.select = discord.ui.Select(
            placeholder="選擇要查看的新聞...",
            options=options,
            row=0
        )
        self.select.callback = self.select_callback
        
        self.back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=1, disabled=True)
        self.back_btn.callback = self.back_action
        
        self.close_btn = discord.ui.Button(label="關閉", style=discord.ButtonStyle.secondary, emoji="❌", row=1)
        self.close_btn.callback = self.close_action
        
        self.add_item(self.select)
        self.add_item(self.back_btn)
        self.add_item(self.close_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def _build_overview_embed(self):
        embed = discord.Embed(
            title="",
            color=0x3498db,
            description="請從下拉選單選擇閱讀詳細內容。"
        )
        for i, news in enumerate(self.news_list[:5]):
            title = news.get("title", "")
            embed.add_field(name=title, value=news.get("link", ""), inline=False)
        now_time = datetime.now(timezone(timedelta(hours=8)))
        current_time = now_time.strftime("%m-%d %H:%M")
        embed.set_footer(text=f"公視新聞網 RSS • 查詢時間 {current_time}")
        return embed

    def _build_detail_embed(self, index: int):
        news = self.news_list[index]
        title = news.get("title", "未知標題")
        link = news.get("link", "")
        summary = news.get("summary", "")
        # 去除 HTML 標籤
        summary_clean = re.sub(r'<[^>]+>', '', summary).strip()
        pubDate = news.get("pubDate") or news.get("published") or ""
        
        embed = discord.Embed(
            title=title,
            url=link,
            description=f"{summary_clean}\n\n[閱讀全文]({link})",
            color=0x2ecc71
        )
        if pubDate:
            embed.set_footer(text=f"發布時間：{pubDate}")
        return embed

    async def select_callback(self, interaction: discord.Interaction):
        value = self.select.values[0]
        
        # 更新選單的 default 狀態
        for opt in self.select.options:
            opt.default = (opt.value == value)
            
        if value == "overview":
            self.back_btn.disabled = True
            embed = self._build_overview_embed()
        else:
            self.back_btn.disabled = False
            embed = self._build_detail_embed(int(value))
            
        await interaction.response.edit_message(content="🌦️ 最新氣象與災防新聞", embed=embed, view=self)

    async def back_action(self, interaction: discord.Interaction):
        for opt in self.select.options:
            opt.default = (opt.value == "overview")
        self.back_btn.disabled = True
        embed = self._build_overview_embed()
        await interaction.response.edit_message(content="🌦️ 最新氣象與災防新聞", embed=embed, view=self)
        
    async def close_action(self, interaction: discord.Interaction):
        try:
            await interaction.message.delete()
        except:
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)


class NewsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="氣象新聞", description="📰 獲取公視最新的氣象、天災、水情相關新聞")
    async def weather_news(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        
        url = 'https://news.pts.org.tw/xml/newsfeed.xml'
        keywords = ['雨', '地震', '颱風', '水庫', '天氣', '氣象', '淹水', '土石', '雷', '防汛', '寒流', '高溫', '坍方', '落石', '停班', '停課', '災情']
        results = []
        
        try:
            # 使用 ssl=False 以避免某些環境下憑證錯誤
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        await interaction.followup.send("❌ 無法取得新聞資料，來源伺服器可能發生錯誤。")
                        return
                    text = await resp.text()
                    
            feed = feedparser.parse(text)
            for entry in feed.entries:
                content = (entry.get('title', '') + ' ' + entry.get('summary', '')).strip()
                if any(k in content for k in keywords):
                    results.append(entry)
                    if len(results) >= 10:
                        break
        except Exception as e:
            logger.error(f"獲取氣象新聞失敗: {e}")
            await interaction.followup.send("❌ 獲取新聞時發生錯誤，請稍後再試。")
            return
            
        if not results:
            await interaction.followup.send("ℹ️ 目前沒有與氣象或災防相關的最新新聞。")
            return
            
        view = NewsView(results, interaction.user.id)
        embed = view._build_overview_embed()
        await interaction.followup.send(content="🌦️ 最新氣象與災防新聞", embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(NewsCog(bot))
