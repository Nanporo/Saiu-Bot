import discord
from discord.ext import commands
from discord import app_commands
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import logging
import asyncio
import re
from modules.http_client import fetch_text

logger = logging.getLogger(__name__)

THSRC_URL = "https://www.thsrc.com.tw/ArticleContent/3ec1c04f-d3de-45b1-becc-cba412d55123"
TRC_URL = "https://www.railway.gov.tw/tra-tip-web/tip/tip007/tip711/blockList"

def format_discord_timestamp(time_str: str) -> str:
    if not time_str:
        return ""
    tz = timezone(timedelta(hours=8))
    m = re.search(r'(?:(\d{4})[/-])?(\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?', time_str)
    if m:
        year = int(m.group(1)) if m.group(1) else datetime.now(tz).year
        month = int(m.group(2))
        day = int(m.group(3))
        hour = int(m.group(4))
        minute = int(m.group(5))
        second = int(m.group(6)) if m.group(6) else 0
        try:
            dt = datetime(year, month, day, hour, minute, second, tzinfo=tz)
            return f"<t:{int(dt.timestamp())}:f>"
        except ValueError:
            pass
    return time_str

class TrafficCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _fetch_thsrc_data(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            html = await fetch_text(THSRC_URL, headers=headers, cache_ttl=60)
            soup = BeautifulSoup(html, 'html.parser')

            status_text = "未知狀態"
            ds = soup.find(class_='status-ds')
            if ds:
                text_div = ds.find(class_='text')
                if text_div:
                    status_text = text_div.get_text(strip=True)

            event_title = ""
            update_time = ""
            remarks = []
            desc = ""

            detail_div = soup.find(class_='status-detail')
            if detail_div:
                t_div = detail_div.find(class_=lambda c: c and 'font-11r' in c)
                if t_div:
                    event_title = t_div.get_text(strip=True)

                u_span = detail_div.find(class_=lambda c: c and 'light-gray' in c)
                if u_span:
                    update_time = u_span.get_text(strip=True)

                r_ul = detail_div.find(class_='status-remark')
                if r_ul:
                    for li in r_ul.find_all('li'):
                        r_text = li.get_text(strip=True)
                        if r_text:
                            remarks.append(r_text)

                p_desc = detail_div.find('p', class_=lambda c: c and 'darkgray' in c)
                if p_desc:
                    desc = p_desc.get_text(strip=True)

            return {
                'status_text': status_text,
                'event_title': event_title,
                'update_time': update_time,
                'remarks': remarks,
                'desc': desc
            }
        except Exception as e:
            logger.error(f"❌ 高鐵營運狀況爬取失敗: {e!r}")
            return {
                'status_text': "無法取得狀態",
                'event_title': "",
                'update_time': "",
                'remarks': [],
                'desc': f"高鐵連線失敗：{e!r}"
            }

    async def _fetch_trc_data(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            html = await fetch_text(TRC_URL, headers=headers, cache_ttl=60)
            soup = BeautifulSoup(html, 'html.parser')

            items = []
            tables = soup.find_all('table')
            for table in tables:
                dl = table.find_previous('dl', class_='now_status')
                line_name = dl.find('dt').get_text(strip=True) if (dl and dl.find('dt')) else ''
                for tr in table.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) >= 3:
                        cols = [td.get_text(strip=True) for td in tds]
                        if cols[0] not in ['Mar', 'APR', 'May', '2023', '2024', '2025', '2026'] and len(cols[0]) >= 5:
                            cols.append(line_name)
                            items.append(cols)

            return {
                'items': items
            }
        except Exception as e:
            logger.error(f"❌ 台鐵營運狀況爬取失敗: {e!r}")
            return {
                'items': [],
                'error': f"台鐵連線失敗：{e!r}"
            }

    def build_traffic_embed(self, thsrc_data, trc_data):
        # 1. 處理高鐵狀態
        thsrc_status = thsrc_data['status_text']
        if "正常" in thsrc_status:
            thsrc_icon = "`🟢`"
            thsrc_level = 0
        elif "延誤" in thsrc_status or "調整" in thsrc_status:
            thsrc_icon = "`🟡`"
            thsrc_level = 1
        elif "暫停" in thsrc_status or "中斷" in thsrc_status or "停駛" in thsrc_status:
            thsrc_icon = "`🔴`"
            thsrc_level = 2
        else:
            thsrc_icon = "`⚪`"
            thsrc_level = 0

        # 2. 處理台鐵狀態
        trc_items = trc_data.get('items', [])
        trc_err = trc_data.get('error')
        if trc_err:
            trc_status = "無法取得狀態"
            trc_icon = "`⚪`"
            trc_level = 0
        elif not trc_items:
            trc_status = "全線正常營運"
            trc_icon = "`🟢`"
            trc_level = 0
        else:
            trc_status = "部份路段受阻"
            trc_icon = "`🟡`"
            trc_level = 1

        # 決定整體 Title 與顏色
        max_level = max(thsrc_level, trc_level)
        if max_level == 0:
            overall_title = "`🟢` 正常營運"
            embed_color = 0x2ecc71
        elif max_level == 1:
            overall_title = "`🟡` 營運調整"
            embed_color = 0xf1c40f
        else:
            overall_title = "`🔴` 營運中斷"
            embed_color = 0xe74c3c

        desc = f"<:thsrc_logo:1529810134526853260> **台灣高鐵** {thsrc_icon} {thsrc_status}\n<:trc_logo:1529810132785959054> **台灣鐵路** {trc_icon} {trc_status}"

        embed = discord.Embed(
            title=overall_title,
            description=desc,
            color=embed_color
        )

        detail_blocks = []

        # ---------------- 整理高鐵異動詳情 (使用 Regex 整理) ----------------
        if thsrc_data['event_title'] or thsrc_data['remarks'] or thsrc_data['desc']:
            thsrc_block_lines = []

            # 用 Regex 提取更新時間 YYYY/MM/DD HH:MM
            m_time = re.search(r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}', thsrc_data.get('update_time', ''))
            update_time = m_time.group(0) if m_time else thsrc_data.get('update_time', '')

            # 用 Regex 從 remarks 擷取路段與原因
            section = ''
            cause = ''
            for r in thsrc_data.get('remarks', []):
                m_sec = re.search(r'影響路段[:：]\s*(.+)', r)
                if m_sec:
                    section = m_sec.group(1).strip()
                m_cause = re.search(r'發生原因[:：]\s*(.+)', r)
                if m_cause:
                    cause = m_cause.group(1).strip()

            title = thsrc_data.get('event_title', '')
            clean_title = re.sub(r'[\s\-–—]*已派員.*$', '', title).strip()

            sec_label = f" ({section})" if section else ""
            thsrc_block_lines.append(f"**高鐵{sec_label}**：{clean_title if clean_title else thsrc_status}")
            if update_time:
                thsrc_block_lines.append(f"* 通報時間：{format_discord_timestamp(update_time)}")
            if cause:
                thsrc_block_lines.append(f"* 發生原因：{cause}")
            if thsrc_data.get('desc'):
                clean_desc = re.sub(r'\s+', ' ', thsrc_data.get('desc')).strip()
                thsrc_block_lines.append(f"```{clean_desc}```")

            detail_blocks.append("\n".join(thsrc_block_lines))

        # ---------------- 整理台鐵異動詳情 (使用 Regex 整理) ----------------
        if trc_items:
            trc_block_lines = []
            for item in trc_items[:3]:
                time_str = item[0] if len(item) > 0 else ''
                section = item[1] if len(item) > 1 else ''
                content_str = item[2] if len(item) > 2 else ''
                recover_raw = item[3] if len(item) > 3 else ''
                line_name = item[4] if len(item) > 4 else ''

                content_str = re.sub(r'\s+', ' ', content_str).strip()

                clean_recover = re.sub(r'^(?:預計)?恢復(?:時間)?[:：]?\s*', '', recover_raw).strip()
                m_rec = re.search(r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}', clean_recover)
                recover_time = m_rec.group(0) if m_rec else clean_recover

                prefix = line_name if line_name else "台鐵"
                sec_label = f" ({section})" if section else ""
                trc_block_lines.append(f"**{prefix}{sec_label}**：{content_str}")
                if time_str:
                    trc_block_lines.append(f"* 通報時間：{format_discord_timestamp(time_str)}")
                if recover_time:
                    trc_block_lines.append(f"* 預計恢復：{format_discord_timestamp(recover_time)}")

            detail_blocks.append("\n".join(trc_block_lines))

        if detail_blocks:
            embed.add_field(name="\u200b", value="\n──────────────────\n".join(detail_blocks).strip(), inline=False)

        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        footer_text = f"查詢時間 {current_time}"
        embed.set_footer(text=footer_text)

        return embed

    @app_commands.command(name="交通狀況", description="🚄 查詢全台軌道交通即時營運狀況與異動通報 Traffic")
    async def traffic_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            thsrc_data, trc_data = await asyncio.gather(
                self._fetch_thsrc_data(),
                self._fetch_trc_data()
            )
            embed = self.build_traffic_embed(thsrc_data, trc_data)
            await interaction.followup.send(content="🚄 交通狀況", embed=embed)
        except Exception as e:
            logger.error(f"❌ 指令 /交通狀況 發生錯誤：{e!r}")
            await interaction.followup.send(f"❌ 發生錯誤：{e!r}")

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        try:
            thsrc_data, trc_data = await asyncio.gather(
                self._fetch_thsrc_data(),
                self._fetch_trc_data()
            )
            embed = self.build_traffic_embed(thsrc_data, trc_data)
            await message.edit(content="🚄 交通狀況", embed=embed)
            await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
        except Exception as e:
            logger.error(f"❌ refresh_message (TrafficCog) 發生錯誤：{e!r}")
            await interaction.followup.send(f"❌ 發生錯誤：{e!r}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TrafficCog(bot))
