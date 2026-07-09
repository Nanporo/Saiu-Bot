import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import feedparser
import re
import logging
import time
from datetime import datetime, timezone, timedelta
from modules.http_client import fetch_text

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
        for i, news in enumerate(self.news_list[:6]):
            title = news.get("title", "")
            embed.add_field(name=title, value=news.get("link", ""), inline=False)
        now_time = datetime.now(timezone(timedelta(hours=8)))
        current_time = now_time.strftime("%m-%d %H:%M")
        embed.set_footer(text=f"公視、中央社、央廣 RSS • 查詢時間 {current_time}")
        return embed

    def _build_detail_embed(self, index: int):
        news = self.news_list[index]
        title = news.get("title", "未知標題")
        link = news.get("link", "")
        summary = news.get("summary", "")
        # 去除 HTML 標籤
        summary_clean = re.sub(r'<[^>]+>', '', summary).strip()
        pubDate = news.get("pubDate") or news.get("published") or ""
        parsed_time = news.get('published_parsed') or news.get('updated_parsed')
        if parsed_time:
            dt = datetime(*parsed_time[:6], tzinfo=timezone.utc)
            pubDate = dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        
        embed = discord.Embed(
            title=title,
            url=link,
            description=f"{summary_clean}\n\n[閱讀全文]({link})",
            color=0x2ecc71
        )
        source = news.get("news_source", "新聞網")
        if pubDate:
            embed.set_footer(text=f"{source} • 發佈時間 {pubDate}")
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

    @app_commands.command(name="氣象新聞", description="📰 獲取最新的氣象、天災相關新聞 Weather News")
    async def weather_news(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        
        urls = [
            'https://news.pts.org.tw/xml/newsfeed.xml',
            'https://feeds.feedburner.com/rsscna/local',
            'https://feeds.feedburner.com/rsscna/lifehealth',
            'https://www.rti.org.tw/rss'
        ]
        keywords = ['下雨', '大雨', '豪雨', '暴雨', '雷雨', '氣象署', 
                    '地震', '震度', '強震', '餘震', '防震', '全台有感', 
                    '颱風', '防颱', '水庫', 
                    '天氣', '氣象', '淹水', '土石', '打雷', '閃電', 
                    '防汛', '寒流', '高溫', '坍方', '落石', '停班', '停課']
        blacklist = ['人事', '政治', '選戰', '立委', '藍綠', '藍白', '法院', '判決', '貪污', '弊案', '黨工', '立院', '國會', '質詢', '口水戰', '選舉', '候選人', '議員', '黨團']
        news_by_title = {}
        
        try:
            for url in urls:
                text = await fetch_text(url)
                feed = feedparser.parse(text)
                
                source_name = "綜合新聞網"
                if "pts.org.tw" in url:
                    source_name = "公視新聞網"
                elif "rsscna" in url:
                    source_name = "中央通訊社"
                elif "rti.org.tw" in url:
                    source_name = "中央廣播電臺"
                    
                for entry in feed.entries:
                    title = entry.get('title', '').strip()
                    if not title:
                        continue
                    # 排除黑名單（政治、法院、弊案等與純天氣災防無關的詞）
                    if any(b in title for b in blacklist):
                        continue
                    if any(k in title for k in keywords):
                        if title in news_by_title:
                            if source_name not in news_by_title[title]['news_source']:
                                news_by_title[title]['news_source'] += f"、{source_name}"
                        else:
                            entry['news_source'] = source_name
                            news_by_title[title] = entry
                            
            results = list(news_by_title.values())
            
            # 根據發布時間進行排序（由新到舊）
            def get_entry_time(entry):
                if entry.get('published_parsed'):
                    return time.mktime(entry.published_parsed)
                elif entry.get('updated_parsed'):
                    return time.mktime(entry.updated_parsed)
                return 0
                
            results.sort(key=get_entry_time, reverse=True)
            results = results[:24]
            
        except Exception as e:
            logger.error(f"獲取氣象新聞失敗: {e}")
            await interaction.followup.send("❌ 獲取新聞時發生錯誤，請稍後再試。")
            return
            
        if not results:
            await interaction.followup.send("ℹ️ 目前沒有與氣象或災防相關的最新新聞。")
            return
            
        view = NewsView(results, interaction.user.id)
        embed = view._build_overview_embed()
        await interaction.followup.send(content="📰 最新氣象與災防新聞", embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(NewsCog(bot))
