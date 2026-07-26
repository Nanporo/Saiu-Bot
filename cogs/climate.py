import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import io
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

async def fetch_climate_data(session):
    now = datetime.now(timezone(timedelta(hours=8)))
    last_month = now.replace(day=1) - timedelta(days=1)
    api_date = last_month.strftime("%Y-%m")
    
    url = "https://climate.cwa.gov.tw/Home/get_gauge_data"
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    data = {"date": api_date}
    
    try:
        async with session.post(url, headers=headers, data=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("is_success"):
                    return result.get("data", {}), api_date
    except Exception as e:
        logger.warning(f"⚠️ [警告] 獲取氣候資料失敗: {e!r}")
        
    return None, api_date

async def fetch_nino_images(session):
    now = datetime.now(timezone(timedelta(hours=8)))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    check_time = now
    valid_yyyymm = None
    for _ in range(6):
        yyyy = check_time.strftime("%Y")
        mm = check_time.strftime("%m")
        url = f"https://climate.cwa.gov.tw/archive/ENSOOutlook/{yyyy}{mm}_ENSOmonitor_Fig2_2.png"
        
        try:
            async with session.head(url, headers=headers) as resp:
                if resp.status == 200:
                    valid_yyyymm = f"{yyyy}{mm}"
                    break
        except Exception:
            pass
            
        check_time = check_time.replace(day=1) - timedelta(days=1)
        
    images = {}
    if not valid_yyyymm:
        return images, None

    image_urls = {
        "fig2_2": f"https://climate.cwa.gov.tw/archive/ENSOOutlook/{valid_yyyymm}_ENSOmonitor_Fig2_2.png",
        "fig1_1": f"https://climate.cwa.gov.tw/archive/ENSOOutlook/{valid_yyyymm}_ENSOmonitor_Fig1_1.png",
        "fig2_0": f"https://climate.cwa.gov.tw/archive/ENSOOutlook/{valid_yyyymm}_ENSOmonitor_Fig2_0.png",
        "fig2_1": f"https://climate.cwa.gov.tw/archive/ENSOOutlook/{valid_yyyymm}_ENSOmonitor_Fig2_1.png",
        "nino34": f"https://climate.cwa.gov.tw/archive/ENSOOutlook/CWACFSv2_nino34_{valid_yyyymm}.png"
    }
    
    async def fetch_one(key, img_url):
        try:
            async with session.get(img_url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if len(data) > 1000:
                        images[key] = data
        except Exception:
            pass

    await asyncio.gather(*(fetch_one(k, v) for k, v in image_urls.items()))
    return images, valid_yyyymm

async def fetch_enso_data(session):
    url_alert = "https://climate.cwa.gov.tw/ClimateFcst/get_enso_alert_value"
    url_desc = "https://climate.cwa.gov.tw/ClimateFcst/get_enso_description"
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    alert_val = 50
    abstract = []
    
    try:
        async with session.post(url_alert, headers=headers) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("is_success"):
                    alert_val = res.get("data", {}).get("value", 50)
    except Exception as e:
        logger.warning(f"⚠️ 獲取聖嬰警報值失敗: {e}")

    try:
        async with session.post(url_desc, headers=headers) as resp:
            if resp.status == 200:
                res = await resp.json()
                if res.get("is_success"):
                    abstract = res.get("data", {}).get("abstract", [])
    except Exception as e:
        logger.warning(f"⚠️ 獲取聖嬰敘述失敗: {e}")
        
    return alert_val, abstract

def build_climate_embed(gauge_data, date_str, enso_data):
    embed = discord.Embed(title="", color=0x3498db)
    
    try:
        dt = datetime.strptime(date_str, "%Y-%m")
        display_date = f"{dt.year}年{dt.month}月"
    except Exception:
        display_date = date_str
        
    embed.description = f"**{display_date} 氣候監測回顧**\n\n"
    
    metrics = {
        "air_temperature_mean": {"name": "🌡️ 平均氣溫", "unit": "度"},
        "air_temperature_maximum_ge35_days": {"name": "🥵 高溫日數", "unit": "天"},
        "precipitation_accumulation": {"name": "🌧️ 累積雨量", "unit": "毫米"},
        "precipitation_days": {"name": "☔ 降雨日數", "unit": "天"}
    }
    
    if gauge_data and "data" in gauge_data:
        for item in gauge_data["data"]:
            var_name = item.get("var")
            if var_name in metrics:
                info = metrics[var_name]
                obs = item.get('observation')
                desc = item.get('description', '')
                
                parts = desc.split('，')
                if len(parts) > 1:
                    import re
                    short_compare = re.sub(r'\(.*?\)|（.*?）', '', parts[1])
                    desc_formatted = short_compare
                else:
                    desc_formatted = desc
                
                embed.add_field(
                    name=info["name"], 
                    value=f"**{obs} {info['unit']}**\n{desc_formatted}",
                    inline=True
                )
    else:
        embed.description += "\n⚠️ 目前無法取得氣候指標資料"

    alert_val, abstract = enso_data
    enso_mapping = {
        10: ("反聖嬰發展中", "海氣現況已滿足反聖嬰狀態且預期此狀態將會持續。"),
        30: ("反聖嬰預警", "未來6個月內海氣狀態發展將有利於反聖嬰現象的發生。"),
        50: ("(反)聖嬰現象不活躍", "海氣現況未滿足(反)聖嬰狀態，且沒有預期(反)聖嬰現象發展的跡象。"),
        70: ("聖嬰預警", "未來6個月內海氣狀態發展將有利於聖嬰現象的發生。"),
        90: ("聖嬰發展中", "海氣現況已滿足聖嬰狀態且預期此狀態將會持續。")
    }
    status_title, status_desc = enso_mapping.get(alert_val, enso_mapping[50])
    
    embed.add_field(
        name="🌎 聖嬰概況",
        value=f"**{status_title}**\n{status_desc}",
        inline=True
    )
    
    if abstract:
        abs_text = "\n".join(abstract)
        embed.add_field(
            name="",
            value=f"```\n{abs_text}\n```",
            inline=False
        )

    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
    
    return embed

class ClimateView(discord.ui.View):
    def __init__(self, bot, author_id: int, gauge_data, date_str, images, enso_data, image_date):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.gauge_data = gauge_data
        self.date_str = date_str
        self.images = images
        self.enso_data = enso_data
        self.image_date = image_date
        self.current_mode = "overview"
        
        options = [
            discord.SelectOption(label="概覽", value="overview", default=True),
            discord.SelectOption(label="近赤道海洋熱含量與Nino3.4指標", value="fig2_2", default=False),
            discord.SelectOption(label="850百帕風場與外逸長波輻射距平", value="fig1_1", default=False),
            discord.SelectOption(label="5°S~5°N平均海溫距平剖面", value="fig2_0", default=False),
            discord.SelectOption(label="赤道剖面次表層海溫距平", value="fig2_1", default=False),
            discord.SelectOption(label="Niño3.4指標未來預報", value="nino34", default=False)
        ]
        
        self.view_select = discord.ui.Select(placeholder="切換圖表資料", options=options, min_values=1, max_values=1)
        self.view_select.callback = self.view_select_callback
        self.add_item(self.view_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def build_message(self):
        file = None
        if self.current_mode == "overview":
            embed = build_climate_embed(self.gauge_data, self.date_str, self.enso_data)
        else:
            embed = discord.Embed(title="", color=0x3498db)
            img_time_str = ""
            if self.image_date:
                try:
                    dt = datetime.strptime(self.image_date, "%Y%m")
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                    img_time_str = f"\n資料時間：<t:{int(dt.timestamp())}:D>"
                except Exception:
                    pass

            if self.current_mode == "fig2_2":
                embed.title = ""
                embed.description = f"**近赤道上層海洋熱含量與Nino3.4指標**{img_time_str}"
            elif self.current_mode == "fig1_1":
                embed.title = ""
                embed.description = f"**850百帕風場距平與外逸長波輻射距平**{img_time_str}\n色階：冷色代表對流處較強，暖色代表對流處較弱。"
            elif self.current_mode == "fig2_0":
                embed.title = ""
                embed.description = f"**5°S~5°N平均之海溫距平的時間-經度剖面**{img_time_str}\n縱軸為時間，橫軸為經度。"
            elif self.current_mode == "fig2_1":
                embed.title = ""
                embed.description = f"**赤道剖面次表層海溫距平近況**{img_time_str}\n綠色線為斜溫層深度，縱軸為深度，橫軸為經度。"
            elif self.current_mode == "nino34":
                embed.title = ""
                embed.description = f"**Niño3.4指標未來預報**{img_time_str}"
                
            img_bytes = self.images.get(self.current_mode)
            if img_bytes:
                filename = f"{self.current_mode}.png"
                file = discord.File(io.BytesIO(img_bytes), filename=filename)
                embed.set_image(url=f"attachment://{filename}")
            else:
                embed.description = (embed.description + "\n\n⚠️ 無法取得此圖表資料").strip()
                
            current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
            embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
            
        return embed, file

    async def view_select_callback(self, interaction: discord.Interaction):
        self.current_mode = self.view_select.values[0]
        for opt in self.view_select.options:
            opt.default = (opt.value == self.current_mode)
            
        embed, file = self.build_message()
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])

class ClimateCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="氣候監測", description="📊 查詢台灣最新的氣候監測與聖嬰指標 Climate")
    async def climate_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        gauge_task = fetch_climate_data(self.bot.session)
        image_task = fetch_nino_images(self.bot.session)
        enso_task = fetch_enso_data(self.bot.session)
        
        (gauge_data, date_str), (images, image_date), enso_data = await asyncio.gather(gauge_task, image_task, enso_task)
        
        view = ClimateView(self.bot, interaction.user.id, gauge_data, date_str, images, enso_data, image_date)
        embed, file = view.build_message()
        
        if file:
            await interaction.followup.send(content="📊 氣候監測資訊", embed=embed, file=file, view=view)
        else:
            await interaction.followup.send(content="📊 氣候監測資訊", embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(ClimateCog(bot))
