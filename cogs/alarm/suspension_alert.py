import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import re
from datetime import datetime, timezone, timedelta

COUNTIES = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣",
    "臺中市", "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市",
    "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣"
]

class SuspensionAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_status = {}
        self.check_suspension_loop.start()

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
        clean_info = re.sub(r'\s+', '', info).replace('今天', '').replace('明日', '')
        normal_list = [
            '照常上班、照常上課。', 
            '照常上班、照常上課',
            '照常上班及上課。', 
            '照常上班、上課。',
            '正常上班、正常上課。',
            '正常上班、正常上課',
            '無停班停課訊息。',
            '尚未宣布。'
        ]
        return clean_info in normal_list

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
                    html = await resp.text()
                    results = {}
                    
                    # 透過正則表達式逐行抓取 table 內的 row
                    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
                    for row in rows:
                        city_match = re.search(r'<td[^>]*headers="city_Name"[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                        info_match = re.search(r'<td[^>]*headers="StopWorkSchool_Info"[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                        
                        if city_match and info_match:
                            city_raw = city_match.group(1)
                            info_raw = info_match.group(1)
                        else:
                            # 備用解析：若 HTML 結構改變遺失 headers 屬性
                            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
                            if len(tds) >= 2:
                                city_raw = tds[0]
                                info_raw = tds[-1] if len(tds) > 2 else tds[1]
                            else:
                                continue
                                
                        city = self.strip_html(city_raw)
                        info = self.strip_html(info_raw)
                        
                        # 整理字串（去除多餘空白，保留換行）
                        city = " ".join(city.split())
                        info = "\n".join([line.strip() for line in info.split('\n') if line.strip()])
                        
                        if city and info and any(k in city for k in ["市", "縣"]):
                            results[city] = info
                            
                    # 若無颱風或災害時，網頁可能不會顯示表格
                    # 若解析後結果為空，預設全台皆為正常狀態以避免誤判為抓取失敗
                    if not results:
                        for c in COUNTIES:
                            results[c] = "無停班停課訊息。"
                            
                    return results
        except Exception as e:
            print(f"⚠️ [停班停課] 獲取資料失敗: {e}")
        return None

    @tasks.loop(minutes=5.0)
    async def check_suspension_loop(self):
        data = await self.fetch_data()
        if data is None: return
            
        if not self.last_status:
            self.last_status = data
            return
            
        changes = {city: info for city, info in data.items() if self.last_status.get(city) != info}
        self.last_status = data
        
        if not changes: return
            
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception: return
            
        for guild_id, d in settings.items():
            alerts = d.get('suspension_alerts', {})
            
            # 向下相容舊版單一地點設定
            if 'suspension_alert' in d:
                old = d['suspension_alert']
                loc = old.get('location_name', '全台') if isinstance(old, dict) else '全台'
                alerts[loc] = old
                
            for city, info in changes.items():
                for alert_city, data in alerts.items():
                    if alert_city.replace("臺", "台") in city.replace("臺", "台") or city.replace("臺", "台") in alert_city.replace("臺", "台"):
                        ch_id = data.get('channel_id') if isinstance(data, dict) else data
                        try:
                            channel = self.bot.get_channel(int(ch_id))
                        except (TypeError, ValueError):
                            channel = None
                            
                        if channel:
                            is_normal = self.is_normal_status(info)
                            color = discord.Color.green() if is_normal else discord.Color.red()
                            title_icon = "✅" if is_normal else "⚠️"
                            embed = discord.Embed(title=f"{title_icon} 停班停課狀態更新", description=f"**{city}** 最新宣布：\n\n{info}", color=color)
                            current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                            embed.set_footer(text=f"行政院人事行政總處 • 推播時間 {current_time}")
                            try: await channel.send(embed=embed)
                            except Exception: pass

    @check_suspension_loop.before_loop
    async def before_check_suspension(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(SuspensionAlertCog(bot))