import discord
from discord.ext import commands
from discord import app_commands
import json
import re
from datetime import datetime, timezone, timedelta

def build_eq_embed(eq):
    info = eq.get("EarthquakeInfo", {})
    eq_no_raw = str(eq.get("EarthquakeNo", ""))
    
    # 判斷如果是編號太短、為空，或末三碼為特殊代號
    if not eq_no_raw or len(eq_no_raw) < 3 or eq_no_raw.endswith("000"):
        eq_title = "小區域地震"
        eq_no_display = "小區域"
    elif eq_no_raw.endswith("999"):
        eq_title = "遠地有感地震"
        eq_no_display = "遠地有感地震"
    else:
        eq_title = "顯著有感地震"
        eq_no_display = eq_no_raw

    origin_time_str = info.get("OriginTime", "未知時間")
    try:
        try:
            dt = datetime.fromisoformat(origin_time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            dt = datetime.strptime(origin_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
        origin_time_display = f"<t:{int(dt.timestamp())}:f>"
    except ValueError:
        origin_time_display = f"`{origin_time_str}`"

    epicenter = info.get("Epicenter", {}).get("Location", "未知地點")
    depth = info.get("FocalDepth", "未知")
    mag = info.get("EarthquakeMagnitude", {}).get("MagnitudeValue", "未知")
    img_url = eq.get("ReportImageURI", "")

    embed = discord.Embed(
        title=f"🏚️ {eq_title}",
        description=eq.get("ReportContent", "中央氣象署最新發布之地震報告"),
        color=0xff3846
    )
    
    embed.add_field(name="發生時間", value=origin_time_display, inline=False)
    embed.add_field(name="相對位置", value=f"{epicenter}", inline=False)
    embed.add_field(name="編號", value=f"`{eq_no_display}`", inline=True)
    embed.add_field(name="地震規模", value=f"M{mag}", inline=True)
    embed.add_field(name="震源深度", value=f"{depth} 公里", inline=True)

    # 提取各地最大震度
    intensity_data = eq.get("Intensity", {}).get("ShakingArea", [])
    intensity_map = {}
    for area in intensity_data:
        area_desc = area.get("AreaDesc", "")
        county = area.get("CountyName", "")
        intensity = area.get("AreaIntensity", "")
        
        # 排除 API 內建的「最大震度X級地區」等彙整欄位，自己從各縣市提取以確保一致性
        if "最大震度" in area_desc or not county or not intensity:
            continue
            
        if intensity not in intensity_map:
            intensity_map[intensity] = []
        if county not in intensity_map[intensity]:
            intensity_map[intensity].append(county)

    def sort_intensity(k):
        if not k: return 0
        num = int(k[0]) if k[0].isdigit() else 0
        weight = num * 10
        if "強" in k: weight += 2
        elif "弱" in k: weight += 1
        return weight

    intensity_text = ""
    sorted_keys = sorted(intensity_map.keys(), key=sort_intensity, reverse=True)
    for k in sorted_keys:
        counties = "、".join(intensity_map[k])
        k_full = k.translate(str.maketrans('0123456789', '０１２３４５６７８９'))
        intensity_text += f"**{k_full}** {counties}\n"
        
    if intensity_text:
        embed.add_field(name="各地最大震度", value=intensity_text.strip(), inline=False)

    if img_url:
        embed.set_image(url=img_url)

    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/TWERG-Bot/main/photos/cwa_logo.png")
    return embed

class EarthquakeSelect(discord.ui.Select):
    def __init__(self, eqs):
        # 將地震資料以 "時間_編號" 建立成字典方便查找
        self.eq_dict = {}
        options = []
        
        for i, eq in enumerate(eqs):
            info = eq.get("EarthquakeInfo", {})
            origin_time = info.get("OriginTime", "未知時間")
            eq_no_raw = str(eq.get("EarthquakeNo", "小區域"))
            epicenter = info.get("Epicenter", {}).get("Location", "未知地點")
            mag = info.get("EarthquakeMagnitude", {}).get("MagnitudeValue", "?")
            
            dict_key = f"{origin_time}_{eq_no_raw}"
            self.eq_dict[dict_key] = eq
            
            # 判斷顯示編號
            if not eq_no_raw or len(eq_no_raw) < 3 or eq_no_raw.endswith("000"):
                eq_no_display = "小區域"
            elif eq_no_raw.endswith("999"):
                eq_no_display = "遠地有感地震"
            else:
                eq_no_display = eq_no_raw
                
            # 簡化震央位置 (提取括號內的文字)
            epi_match = re.search(r'位於(.*?)\)', epicenter)
            epicenter_short = epi_match.group(1).strip() if epi_match else epicenter[:15]
            
            try:
                try:
                    dt = datetime.fromisoformat(origin_time)
                except ValueError:
                    dt = datetime.strptime(origin_time, "%Y-%m-%d %H:%M:%S")
                time_short = dt.strftime("%m-%d %H:%M")
            except ValueError:
                time_short = origin_time[5:16] if len(origin_time) >= 16 else origin_time

            # 格式化下拉選單顯示的文字 (時間與規模對調)
            label = f"規模: {mag} | {epicenter_short}"
            if len(label) > 100: 
                label = label[:97] + "..."
                
            options.append(discord.SelectOption(
                label=label,
                description=f"時間 {time_short} | 編號 {eq_no_display}",
                value=dict_key,
                default=(i == 0)
            ))
            
        super().__init__(placeholder="選擇要查看的地震報告", options=options)
        
    async def callback(self, interaction: discord.Interaction):
        # 更新下拉選項的預設打勾狀態
        for opt in self.options:
            opt.default = (opt.value == self.values[0])
            
        selected_eq = self.eq_dict.get(self.values[0])
        embed = build_eq_embed(selected_eq)
        await interaction.response.edit_message(embed=embed, view=self.view)

class EarthquakeListView(discord.ui.View):
    def __init__(self, eqs):
        super().__init__(timeout=300)
        self.add_item(EarthquakeSelect(eqs))

class EarthquakeListCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="地震列表", description="手動查詢最新 10 筆地震報告")
    async def earthquake_list_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # 呼叫已經在 EarthquakeAlertCog 寫好的抓取函式，節省重複程式碼
        alert_cog = self.bot.get_cog("EarthquakeAlertCog")
        if not alert_cog:
            await interaction.followup.send("❌ 錯誤：地震警報模組尚未載入，無法抓取資料。")
            return
            
        eqs = await alert_cog.fetch_earthquakes()
        if not eqs:
            await interaction.followup.send("⚠️ 目前無法從氣象署獲取地震資料，或尚未設定 API Key。")
            return
            
        # 依照發生時間 (OriginTime) 降冪排序，確保最晚發生的在最前面
        eqs.sort(key=lambda x: x.get("EarthquakeInfo", {}).get("OriginTime", ""), reverse=True)
        latest_10_eqs = eqs[:10]
        
        view = EarthquakeListView(latest_10_eqs)
        embed = build_eq_embed(latest_10_eqs[0])
        
        await interaction.followup.send(content="🏚️ **地震報告列表**", embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(EarthquakeListCog(bot))
