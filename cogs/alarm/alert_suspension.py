import discord
from discord.ext import commands, tasks
from discord import app_commands
from html import unescape
import json
import re
from datetime import datetime, timezone, timedelta
from modules.database import get_all_settings
from modules.cache_manager import load_cache
from modules.discord_utils import safe_channel_send
import logging

logger = logging.getLogger(__name__)

COUNTIES = [
    "基隆市", "臺北市", "新北市", 
    "桃園市", "新竹市", "新竹縣", "苗栗縣",
    "臺中市", "彰化縣", "南投縣", 
    "雲林縣", "嘉義市", "嘉義縣", "臺南市",
    "高雄市", "屏東縣", 
    "宜蘭縣", "花蓮縣", "臺東縣", 
    "澎湖縣", "金門縣", "連江縣"
]

NO_ANNOUNCEMENT_STATUSES = {
    "",
    "無停班停課訊息",
    "尚未宣布",
    "尚未宣布消息",
    "尚無宣布消息",
    "目前尚未宣布消息",
}

STATUS_EMOJIS = {
    "suspended": "🚸",
    "normal": "✅",
    "partial": "🟡",
    "unknown": "⚠️",
}

DGPA_LOGO_URL = "https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/dgpa_logo.png"


def parse_source_update_time(raw_html):
    """從人事行政總處頁面取得資料更新時間。"""
    plain_text = unescape(re.sub(r'<[^>]+>', ' ', raw_html or ''))
    match = re.search(
        r'更新時間\s*[:：]\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s+'
        r'(\d{1,2}):(\d{2})(?::\d{2})?',
        plain_text,
    )
    if not match:
        return None

    try:
        source_time = datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return source_time.strftime("%Y-%m-%d %H:%M")


def build_footer_text(action, action_time, source_update_time):
    """組合來源更新時間與本機器的查詢／推播時間。"""
    update_time = source_update_time or "無法取得"
    return (
        f"行政院人事行政總處 • 更新時間 {update_time}\n"
        f"{action} {action_time.strftime('%Y-%m-%d %H:%M')}"
    )


def normalize_status_text(info):
    """將人事行政總處的狀態文字正規化，避免排版差異被誤判為新公告。"""
    if not info:
        return ""
    text = re.sub(r'\s+', '', str(info))
    text = text.replace('今天', '').replace('明日', '')
    return text.rstrip('。．.!！')


def is_no_announcement_status(info):
    """是否為空值或「尚未宣布／無訊息」類的無內容占位狀態。"""
    return normalize_status_text(info) in NO_ANNOUNCEMENT_STATUSES


def get_status_kind(info):
    """將公告分類為全縣市停班課、正常、局部／混合狀態或無法判斷。

    同時有今天與明天的內容時，只以明天（明日）起的內容判斷。
    """
    if is_no_announcement_status(info):
        return "unknown"

    text = re.sub(r'\s+', '', str(info))

    # 公告可能先寫今天、再寫明天；展示燈號應以較新的天數為準。
    tomorrow_match = re.search(r'明(?:天|日)', text)
    if tomorrow_match:
        text = text[tomorrow_match.start():]

    # 有明確鄉鎮市區、村里或學校前綴的公告，不是整個縣市的單一狀態。
    local_target_pattern = (
        r'(?:鄉|鎮|市|區|村|里|小學|國小|中學|國中|高中|高職|農工|'
        r'高商|大學|專科|學校|幼兒園|分校|托兒所|進修部)[:：]'
    )
    if re.search(local_target_pattern, text) or re.search(r'(?:部分地區|下列地區|除.+外)', text):
        return "partial"

    # 「未達停止標準」含有「停止上班」字樣，必須先於停班課判斷。
    normal_patterns = (
        r'未達停止上班及上課標準',
        r'未達停止上班(?:及|、|與)上課標準',
        r'(?:正常|照常)上班(?:、|及|與)(?:正常|照常)?上課',
    )
    if any(re.search(pattern, text) for pattern in normal_patterns):
        return "normal"

    stopped_work = bool(re.search(r'停止上班', text))
    stopped_school = bool(re.search(r'停止上課', text))
    reaches_stop_standard = bool(re.search(r'已達停止上班(?:及|、|與)上課標準', text))

    if reaches_stop_standard or (stopped_work and stopped_school):
        return "suspended"

    # 只停班或只停課，都屬於混合／特殊狀態。
    if stopped_work or stopped_school:
        return "partial"

    return "unknown"


def get_status_emoji(info):
    """取得縣市公告前的燈號 emoji，無法辨識時以 ⚠️ 保底。"""
    return STATUS_EMOJIS.get(get_status_kind(info), STATUS_EMOJIS["unknown"])


def has_meaningful_status_change(previous, current):
    """忽略占位狀態間的轉換，但保留實際停班課公告與解除公告。"""
    if is_no_announcement_status(previous) and is_no_announcement_status(current):
        return False
    return normalize_status_text(previous) != normalize_status_text(current)

class SuspensionAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.last_status = cache.get("suspension_status", {})
        self.last_http_status = None
        self.source_update_time = None
        self.check_suspension_loop.start()

    def save_state(self):
        return {"suspension_status": self.last_status}

    def cog_unload(self):
        self.check_suspension_loop.cancel()

    def strip_html(self, text):
        """去除 HTML 標籤並將換行標籤轉為真正的換行"""
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&nbsp;', ' ').replace('　', ' ')
        return text.strip()

    def is_normal_status(self, info):
        """判斷該文字是否代表全縣市皆為正常上班上課"""
        return get_status_kind(info) == "normal" or is_no_announcement_status(info)

    async def fetch_data(self):
        """爬取人事行政總處的停班停課資料"""
        # 加入時間戳以避免 CDN 或是 Proxy 快取舊資料
        timestamp = int(datetime.now().timestamp())
        url = f"https://www.dgpa.gov.tw/typh/daily/nds.html?t={timestamp}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            async with self.bot.session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    if self.last_http_status not in (None, 200):
                        logger.info("✅ [停班停課] 資料來源已恢復正常")
                    self.last_http_status = 200
                    html = await resp.text()
                    self.source_update_time = parse_source_update_time(html)
                    results = {}
                    
                    # 透過正則表達式逐行抓取 table 內的 row
                    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
                    for row in rows:
                        city_match = re.search(r'<td[^>]*headers="city_Name[^"]*"[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                        
                        if len(tds) < 2:
                            continue
                            
                        if city_match:
                            city_raw = city_match.group(1)
                        else:
                            # 備用解析：若 HTML 結構改變遺失 headers 屬性
                            city_raw = tds[1] if len(tds) > 2 else tds[0]
                            
                        info_raw = tds[-1]
                                
                        city = self.strip_html(city_raw)
                        info = self.strip_html(info_raw)
                        
                        # 整理字串（去除多餘空白，保留換行）
                        city = " ".join(city.split())
                        lines = [line.strip() for line in info.split('\n') if line.strip()]
                        
                        # 將項目依層級排序：縣市層級(1) -> 鄉鎮市區(2) -> 村里(3) -> 學校(4)
                        def get_weight(line):
                            if ":" not in line and "：" not in line: return 1
                            target = line.split(":")[0].split("：")[0]
                            school_kw = ["小學", "中學", "高中", "高職", "農工", "高商", "大學", "專科", "學校", "幼兒園", "分校", "托兒所", "進修部"]
                            if any(k in target for k in school_kw): return 4
                            if any(k in target for k in ["村", "里"]): return 3
                            if any(k in target for k in ["鄉", "鎮", "市", "區"]): return 2
                            return 1
                            
                        lines.sort(key=lambda x: (get_weight(x), x))
                        info = "\n".join(lines)
                        
                        if city and info and city != "縣市名稱" and any(k in city for k in ["市", "縣"]):
                            results[city] = info
                            
                    # 若無颱風或災害時，網頁可能不會顯示表格
                    # 若解析後結果為空，預設全台皆為正常狀態以避免誤判為抓取失敗
                    if not results:
                        for c in COUNTIES:
                            results[c] = "無停班停課訊息。"
                            
                    return results
                else:
                    if self.last_http_status != resp.status:
                        logger.warning("⚠️ [停班停課] 資料來源回應異常：HTTP %s", resp.status)
                        self.last_http_status = resp.status
                    return None
        except Exception as e:
            err_str = f"EXC_{type(e).__name__}"
            if self.last_http_status != err_str:
                logger.warning("⚠️ [停班停課] 資料取得失敗：%r", e)
                self.last_http_status = err_str
        return None

    @tasks.loop(minutes=5.0)
    async def check_suspension_loop(self):
        data = await self.fetch_data()
        if data is None: return
            
        if not self.last_status:
            self.last_status = data
            return
            
        is_all_clear = len(data) >= len(COUNTIES) and all(
            is_no_announcement_status(info) for info in data.values()
        )

        changes = {
            city: info
            for city, info in data.items()
            if has_meaningful_status_change(self.last_status.get(city), info)
        }
        self.last_status = data
        
        if not changes: return
        
        if is_all_clear: return
            
        try:
            settings = get_all_settings()
        except Exception: return
            
        sent_cnt = 0
        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            alerts = d.get('suspension_alerts', {})
            if not isinstance(alerts, dict):
                continue
                
            channel_updates = {}
            for city, info in changes.items():
                for alert_city, data in alerts.items():
                    if alert_city == "全台接收" or alert_city.replace("臺", "台") in city.replace("臺", "台") or city.replace("臺", "台") in alert_city.replace("臺", "台"):
                        ch_id = data.get('channel_id') if isinstance(data, dict) else data
                        if not ch_id or isinstance(ch_id, bool): continue
                        ch_id_str = str(ch_id)
                        if ch_id_str not in channel_updates:
                            channel_updates[ch_id_str] = {}
                        channel_updates[ch_id_str][city] = info
                        
            for ch_id_str, city_infos in channel_updates.items():
                try:
                    channel = self.bot.get_channel(int(ch_id_str))
                except (TypeError, ValueError):
                    channel = None
                    
                if not channel: continue
                
                if len(city_infos) == 1:
                    city = list(city_infos.keys())[0]
                    info = city_infos[city]
                    is_normal = self.is_normal_status(info)
                    color = discord.Color.green() if is_normal else discord.Color.red()
                    title_icon = get_status_emoji(info)
                    embed = discord.Embed(title="", description=f"`{title_icon}` **{city}** 最新宣布：\n\n{info}", color=color)
                    current_time = datetime.now(timezone(timedelta(hours=8)))
                    embed.set_footer(
                        text=build_footer_text("推播時間", current_time, self.source_update_time),
                        icon_url=DGPA_LOGO_URL,
                    )
                    try: 
                        content = f"{title_icon} 停班停課狀態更新"
                        mention_role_id = d.get('suspension_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            await safe_channel_send(channel, context="停班停課", content=content, embed=embed, silent=global_silent)
                            sent_cnt += 1
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        status_str = "正常" if is_normal else "停班課"
                        logger.debug(f"📢 [停班停課] 已發送狀態更新至 {guild_name} ({channel.name}) - {city} ({status_str})")
                    except Exception as e: pass
                else:
                    all_normal = all(self.is_normal_status(info) for info in city_infos.values())
                    color = discord.Color.green() if all_normal else discord.Color.red()
                    title_icon = "✅" if all_normal else "⚠️"
                    
                    embed = discord.Embed(title="", color=color)
                    for city, info in city_infos.items():
                        display_info = info if len(info) <= 1024 else info[:1021] + "..."
                        status_emoji = get_status_emoji(info)
                        embed.add_field(name=f"`{status_emoji}` **{city}** 最新宣布：", value=display_info, inline=False)
                        
                    current_time = datetime.now(timezone(timedelta(hours=8)))
                    embed.set_footer(
                        text=build_footer_text("推播時間", current_time, self.source_update_time),
                        icon_url=DGPA_LOGO_URL,
                    )
                    try:
                        content = f"{title_icon} 停班停課狀態更新"
                        mention_role_id = d.get('suspension_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            await safe_channel_send(channel, context="停班停課", content=content, embed=embed, silent=global_silent)
                            sent_cnt += 1
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        cities_str = "、".join(city_infos.keys())
                        status_str = "全正常" if all_normal else "含停班課"
                        logger.debug(f"📢 [停班停課] 已發送狀態更新至 {guild_name} ({channel.name}) - 多縣市合併 ({cities_str}) ({status_str})")
                    except Exception as e: pass

        if sent_cnt > 0:
            logger.info(f"📢 [停班停課] 廣播完成 | 共發送 {sent_cnt} 個頻道")

    @check_suspension_loop.before_loop
    async def before_check_suspension(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(SuspensionAlertCog(bot))
