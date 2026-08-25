import discord
from discord.ext import commands, tasks
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import logging
import asyncio
import re
from modules.database import get_all_settings
from modules.cache_manager import load_cache
from modules.http_client import fetch_text
from modules.location_matcher import town_mapping_cache

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
    return f"`{time_str}`"

def is_location_affected(alert_loc: str, thsrc_data: dict, trc_data: dict) -> bool:
    """判斷指定地點/縣市是否受高鐵或台鐵當前異動影響"""
    if alert_loc == "全台接收":
        return True

    norm_loc = alert_loc.replace("台", "臺").strip()
    short_loc = norm_loc.rstrip("縣市")

    # 1. 檢查高鐵異動
    thsrc_status = thsrc_data.get('status_text', '')
    if "正常" not in thsrc_status:
        thsrc_full = (
            thsrc_status + " " +
            thsrc_data.get('event_title', '') + " " +
            thsrc_data.get('desc', '') + " " +
            " ".join(thsrc_data.get('remarks', []))
        ).replace("台", "臺")

        if "全線" in thsrc_full:
            return True

        if norm_loc in thsrc_full or short_loc in thsrc_full:
            return True

        thsrc_map = {
            "南港": ["臺北市"], "台北": ["臺北市"], "臺北": ["臺北市"],
            "板橋": ["新北市"], "桃園": ["桃園市"],
            "新竹": ["新竹縣", "新竹市"], "苗栗": ["苗栗縣"],
            "台中": ["臺中市"], "臺中": ["臺中市"],
            "彰化": ["彰化縣"], "雲林": ["雲林縣"],
            "嘉義": ["嘉義縣", "嘉義市"],
            "台南": ["臺南市"], "臺南": ["臺南市"],
            "左營": ["高雄市"], "高雄": ["高雄市"]
        }

        m_sec = re.search(r'影響路段[:：]\s*(.+)', " ".join(thsrc_data.get('remarks', [])))
        if m_sec:
            sec_text = m_sec.group(1).replace("台", "臺")
            st_order = ["南港", "臺北", "板橋", "桃園", "新竹", "苗栗", "臺中", "彰化", "雲林", "嘉義", "臺南", "左營"]
            st_in_sec = [s for s in st_order if s in sec_text]
            if len(st_in_sec) >= 2:
                idx1 = st_order.index(st_in_sec[0])
                idx2 = st_order.index(st_in_sec[-1])
                affected_sts = st_order[idx1:idx2+1]
                for st in affected_sts:
                    for c in thsrc_map.get(st, []):
                        if norm_loc in c or short_loc in c:
                            return True
            else:
                for st, counties in thsrc_map.items():
                    if st in sec_text:
                        for c in counties:
                            if norm_loc in c or short_loc in c:
                                return True
        else:
            for st, counties in thsrc_map.items():
                if st in thsrc_full:
                    for c in counties:
                        if norm_loc in c or short_loc in c:
                            return True

    # 2. 檢查台鐵異動
    trc_items = trc_data.get('items', [])
    if trc_items:
        for item in trc_items:
            trc_full = " ".join(item).replace("台", "臺")

            if "全線" in trc_full:
                return True

            if norm_loc in trc_full or short_loc in trc_full:
                return True

            # 抓取可能的地名關鍵字，比對 town_mapping_cache 判斷所屬縣市
            words = re.findall(r'[\u4e00-\u9fa5]{2,4}', trc_full)
            for w in words:
                if w in town_mapping_cache:
                    matches = town_mapping_cache[w]
                    for fullname, _, _, _ in matches:
                        if norm_loc in fullname or short_loc in fullname:
                            return True

    return False

class TrafficAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.last_status = cache.get("traffic_status", {})
        self.alerted_channels = set(cache.get("traffic_alerted_channels", []))
        self.last_thsrc_err = False
        self.last_trc_err = False
        self.check_traffic_loop.start()

    def save_state(self):
        return {
            "traffic_status": self.last_status,
            "traffic_alerted_channels": list(self.alerted_channels)
        }

    def cog_unload(self):
        self.check_traffic_loop.cancel()

    async def _fetch_thsrc_data(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            html = await fetch_text(THSRC_URL, headers=headers, cache_ttl=30)
            if self.last_thsrc_err:
                logger.info("✅ [交通狀況] 高鐵資料抓取已恢復正常")
                self.last_thsrc_err = False
            soup = BeautifulSoup(html, 'html.parser')

            status_text = "全線正常營運"
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
            if not self.last_thsrc_err:
                logger.warning(f"⚠️ [交通狀況] 高鐵營運狀況爬取失敗: {e!r}")
                self.last_thsrc_err = True
            return None

    async def _fetch_trc_data(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            html = await fetch_text(TRC_URL, headers=headers, cache_ttl=30)
            if self.last_trc_err:
                logger.info("✅ [交通狀況] 台鐵資料抓取已恢復正常")
                self.last_trc_err = False
            soup = BeautifulSoup(html, 'html.parser')

            items = []
            tables = soup.find_all('table')
            for table in tables:
                for tr in table.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) >= 3:
                        cols = [td.get_text(strip=True) for td in tds]
                        if cols[0] not in ['Mar', 'APR', 'May', '2023', '2024', '2025', '2026'] and len(cols[0]) >= 5:
                            items.append(cols)

            return {
                'items': items
            }
        except Exception as e:
            if not self.last_trc_err:
                logger.warning(f"⚠️ [交通狀況] 台鐵營運狀況爬取失敗: {e!r}")
                self.last_trc_err = True
            return None

    def build_traffic_embed(self, thsrc_data, trc_data):
        # 1. 處理高鐵狀態
        thsrc_status = thsrc_data.get('status_text', '正常營運')
        has_thsrc_issue = "正常" not in thsrc_status or bool(thsrc_data.get('event_title') or thsrc_data.get('remarks') or thsrc_data.get('desc'))
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
        has_trc_issue = bool(trc_items or trc_err)
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

        # 決定顏色 (根據有狀況運具的最高異常等級)
        max_level = max(thsrc_level if has_thsrc_issue else 0, trc_level if has_trc_issue else 0)
        if max_level == 0:
            embed_color = 0x2ecc71
        elif max_level == 1:
            embed_color = 0xf1c40f
        else:
            embed_color = 0xe74c3c

        desc_lines = []
        # 若只有台鐵有狀況只顯示台鐵、高鐵亦然；若兩者皆有狀況或兩者皆正常，則兩者皆顯示
        show_thsrc = has_thsrc_issue or (not has_thsrc_issue and not has_trc_issue)
        show_trc = has_trc_issue or (not has_thsrc_issue and not has_trc_issue)

        if show_thsrc:
            desc_lines.append(f"<:thsrc_logo:1529810134526853260> **台灣高鐵** {thsrc_icon} {thsrc_status}")
        if show_trc:
            desc_lines.append(f"<:trc_logo:1529810132785959054> **台灣鐵路** {trc_icon} {trc_status}")

        embed = discord.Embed(
            title="",
            description="\n".join(desc_lines),
            color=embed_color
        )

        detail_blocks = []

        # ---------------- 整理高鐵異動詳情 ----------------
        if show_thsrc and (thsrc_data.get('event_title') or thsrc_data.get('remarks') or thsrc_data.get('desc')):
            thsrc_block_lines = []

            m_time = re.search(r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}', thsrc_data.get('update_time', ''))
            update_time = m_time.group(0) if m_time else thsrc_data.get('update_time', '')

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

        # ---------------- 整理台鐵異動詳情 ----------------
        if show_trc and trc_items:
            trc_block_lines = []
            for item in trc_items[:3]:
                time_str = item[0] if len(item) > 0 else ''
                section = item[1] if len(item) > 1 else ''
                content_str = item[2] if len(item) > 2 else ''
                recover_raw = item[3] if len(item) > 3 else ''

                content_str = re.sub(r'\s+', ' ', content_str).strip()

                m_rec = re.search(r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}', recover_raw)
                recover_time = m_rec.group(0) if m_rec else recover_raw

                sec_label = f" ({section})" if section else ""
                trc_block_lines.append(f"**台鐵{sec_label}**：{content_str}")
                if time_str:
                    trc_block_lines.append(f"* 通報時間：{format_discord_timestamp(time_str)}")
                if recover_time:
                    trc_block_lines.append(f"* 預計恢復：{format_discord_timestamp(recover_time)}")

            detail_blocks.append("\n".join(trc_block_lines))

        if detail_blocks:
            embed.add_field(name="\u200b", value="\n──────────────────\n".join(detail_blocks).strip(), inline=False)

        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        footer_text = f"交通狀況異動推播 • {current_time}"
        embed.set_footer(text=footer_text)

        return embed

    @tasks.loop(minutes=5.0)
    async def check_traffic_loop(self):
        thsrc_data, trc_data = await asyncio.gather(
            self._fetch_thsrc_data(),
            self._fetch_trc_data()
        )

        if thsrc_data is None or trc_data is None:
            return

        current_status = {
            'thsrc_status': thsrc_data.get('status_text', ''),
            'thsrc_title': thsrc_data.get('event_title', ''),
            'thsrc_desc': thsrc_data.get('desc', ''),
            'trc_items': trc_data.get('items', [])
        }

        # 首次啟動：僅記錄當前狀態，不發送通知
        if not self.last_status:
            self.last_status = current_status
            return

        thsrc_changed = (
            current_status.get('thsrc_status') != self.last_status.get('thsrc_status') or
            current_status.get('thsrc_title') != self.last_status.get('thsrc_title') or
            current_status.get('thsrc_desc') != self.last_status.get('thsrc_desc')
        )

        # 比對台鐵異動的核心內容 (發布時間、影響路段、異動內容)，忽略僅有預計恢復時間變動
        current_trc_core = [tuple(item[:3]) for item in current_status.get('trc_items', [])]
        last_trc_core = [tuple(item[:3]) for item in self.last_status.get('trc_items', [])]

        trc_changed = (current_trc_core != last_trc_core)

        has_changed = thsrc_changed or trc_changed

        # 記錄上一狀態是否為真實異動異常
        last_thsrc_status = self.last_status.get('thsrc_status', '')
        was_thsrc_abnormal = ("正常" not in last_thsrc_status and last_thsrc_status != "無法取得狀態" and bool(last_thsrc_status))
        was_trc_abnormal = bool(self.last_status.get('trc_items', []))
        was_abnormal = was_thsrc_abnormal or was_trc_abnormal

        self.last_status = current_status

        if not has_changed:
            return

        try:
            settings = get_all_settings()
        except Exception:
            return

        # 判斷是否為恢復正常
        thsrc_normal = "正常" in current_status.get('thsrc_status', '')
        trc_normal = not current_status.get('trc_items', [])
        is_all_clear = thsrc_normal and trc_normal

        # 若當前為全線正常，但上一狀態並非真實異常且無待恢復頻道，則不發送恢復正常訊息
        if is_all_clear and not was_abnormal and not self.alerted_channels:
            return

        sent_cnt = 0
        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            traffic_alerts = d.get('traffic_alerts', {})
            if not traffic_alerts:
                continue

            channels_to_send = set()
            if isinstance(traffic_alerts, dict):
                for loc, data in traffic_alerts.items():
                    ch_id = data.get('channel_id') if isinstance(data, dict) else data
                    if not ch_id or isinstance(ch_id, bool):
                        continue
                    ch_id_str = str(ch_id)
                    if is_all_clear:
                        if ch_id_str in self.alerted_channels:
                            channels_to_send.add(ch_id_str)
                    elif is_location_affected(loc, thsrc_data, trc_data):
                        channels_to_send.add(ch_id_str)
                        self.alerted_channels.add(ch_id_str)
            elif isinstance(traffic_alerts, (int, str)) and not isinstance(traffic_alerts, bool):
                # 舊單一頻道格式，預設視為全台接收
                ch_id_str = str(traffic_alerts)
                if is_all_clear:
                    if ch_id_str in self.alerted_channels:
                        channels_to_send.add(ch_id_str)
                else:
                    channels_to_send.add(ch_id_str)
                    self.alerted_channels.add(ch_id_str)

            if not channels_to_send:
                continue

            embed = self.build_traffic_embed(thsrc_data, trc_data)

            if is_all_clear:
                title_icon = "✅"
                status_msg = "交通營運恢復正常"
            else:
                title_icon = "⚠️"
                status_msg = "交通營運狀況異動通知"

            content = f"{title_icon} **{status_msg}**"
            mention_role_id = d.get('traffic_mention_role_id')
            if mention_role_id:
                content += f" <@&{mention_role_id}>"

            for ch_id_str in channels_to_send:
                try:
                    channel = self.bot.get_channel(int(ch_id_str))
                except (TypeError, ValueError):
                    channel = None

                if not channel:
                    continue

                try:
                    if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                        logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                    else:
                        await channel.send(content=content, embed=embed, silent=global_silent)
                        sent_cnt += 1
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.debug(f"📢 [交通狀況] 已發送狀態更新至 {guild_name} ({channel.name})")
                except Exception as e:
                    logger.error(f"❌ 發送交通狀況異動通知至 {channel.name} 失敗: {e!r}")

        if sent_cnt > 0:
            logger.info(f"📢 [交通狀況] 廣播完成 | 共發送 {sent_cnt} 個頻道")

        # 若本次為全線恢復，發送完畢後清空已通報頻道記錄
        if is_all_clear:
            self.alerted_channels.clear()

    @check_traffic_loop.before_loop
    async def before_check_traffic(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TrafficAlertCog(bot))
