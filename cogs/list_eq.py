import discord
from discord.ext import commands
from discord import app_commands
import json
import re
from datetime import datetime, timezone, timedelta

def get_eq_color(mag, intensity_val):
    if mag <= 4.5: mag_level = 0
    elif mag <= 5.9: mag_level = 1
    elif mag <= 6.6: mag_level = 2
    else: mag_level = 3
        
    if intensity_val <= 3: int_level = 0
    elif intensity_val <= 5.0: int_level = 1
    elif intensity_val <= 6.0: int_level = 2
    else: int_level = 3
        
    level = max(mag_level, int_level)
    colors = [0x2ecc71, 0xf1c40f, 0xe74c3c, 0x9b59b6]
    return colors[level]

def format_intensity(val):
    if val == 5.0: return "5弱"
    if val == 5.5: return "5強"
    if val == 6.0: return "6弱"
    if val == 6.5: return "6強"
    return str(int(val))

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
    clock_emoji = "🕓"
    try:
        try:
            dt = datetime.fromisoformat(origin_time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            dt = datetime.strptime(origin_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
        
        hour = dt.hour % 12
        
        if dt.minute >= 45:
            hour = (hour + 1) % 12
            use_half = False
        elif dt.minute >= 15:
            use_half = True
        else:
            use_half = False
            
        if hour == 0:
            hour = 12
            
        if use_half:
            clock_emojis = {1: "🕜", 2: "🕝", 3: "🕞", 4: "🕟", 5: "🕠", 6: "🕡", 7: "🕢", 8: "🕣", 9: "🕤", 10: "🕥", 11: "🕦", 12: "🕧"}
        else:
            clock_emojis = {1: "🕐", 2: "🕑", 3: "🕒", 4: "🕓", 5: "🕔", 6: "🕕", 7: "🕖", 8: "🕗", 9: "🕘", 10: "🕙", 11: "🕚", 12: "🕛"}
            
        clock_emoji = clock_emojis.get(hour, "🕓")
        
        origin_time_display = f"<t:{int(dt.timestamp())}:f>"
    except ValueError:
        origin_time_display = f"`{origin_time_str}`"

    epicenter = info.get("Epicenter", {}).get("Location", "未知地點")
    epicenter = re.sub(r'[ \n]*[\(（](.*?)[\)）]', r'\n\1', epicenter)
    depth = info.get("FocalDepth", "未知")
    mag_val = info.get("EarthquakeMagnitude", {}).get("MagnitudeValue", "未知")
    try:
        m = float(mag_val)
        mag_display = f"{m:.1f}"
        if m <= 0:
            mag_emoji = "❔"
        elif m < 4.0:
            mag_emoji = "⚪"
        elif m < 5.0:
            mag_emoji = "🟢"
        elif m < 5.6:
            mag_emoji = "🟡"
        elif m < 6.3:
            mag_emoji = "🟠"
        elif m < 6.6:
            mag_emoji = "🔴"
        elif m < 7.5:
            mag_emoji = "🟣"
        else:
            mag_emoji = "🛑"
    except (ValueError, TypeError):
        mag_emoji = "❔"
        m = 0.0
        mag_display = str(mag_val)
    img_url = eq.get("ReportImageURI") or ""

    report_content = eq.get("ReportContent", "中央氣象署最新發布之地震報告")
    
    # 提取各地最大震度
    intensity_data = eq.get("Intensity", {}).get("ShakingArea", [])
    max_intensity_val = 0.0
    
    intensity_map = {}
    for area in intensity_data:
        area_desc = area.get("AreaDesc", "")
        county = area.get("CountyName", "")
        intensity = area.get("AreaIntensity", "")
        
        # 解析最大震度數值供顏色判定
        if intensity:
            match = re.search(r'(\d+)(強|弱)?', str(intensity))
            if match:
                base_val = float(match.group(1))
                if match.group(2) == "強":
                    val = base_val + 0.5
                else:
                    val = base_val
                if val > max_intensity_val:
                    max_intensity_val = val
        
        # 排除 API 內建的「最大震度X級地區」等彙整欄位，自己從各縣市提取以確保一致性
        if "最大震度" in area_desc or not county or not intensity:
            continue
            
        if intensity not in intensity_map:
            intensity_map[intensity] = []
        if county not in intensity_map[intensity]:
            intensity_map[intensity].append(county)
            
    embed_color = get_eq_color(m, max_intensity_val)
    
    embed = discord.Embed(
        title=f"{eq_title}",
        description="",
        color=embed_color
    )
    
    embed.add_field(name="📃 編號", value=f"{eq_no_display}", inline=True)
    embed.add_field(name=f"{mag_emoji} 規模", value=mag_display, inline=True)
    embed.add_field(name="⤵️ 深度", value=f"{depth} 公里", inline=True)
    embed.add_field(name=f"{clock_emoji} 發生時間", value=origin_time_display, inline=True)
    embed.add_field(name="📍 相對位置", value=f"{epicenter}", inline=True)

    def sort_intensity(k):
        if not k: return 0
        num = int(k[0]) if k[0].isdigit() else 0
        weight = num * 10
        if "強" in k: weight += 2
        elif "弱" in k: weight += 1
        return weight

    COUNTY_ORDER = {
        '基隆市': 1, '臺北市': 2, '台北市': 2, '新北市': 3, '桃園市': 4, 
        '新竹縣': 5, '新竹市': 6, '苗栗縣': 7, '臺中市': 8, '台中市': 8,
        '彰化縣': 9, '南投縣': 10, '雲林縣': 11, '嘉義縣': 12, '嘉義市': 13, 
        '臺南市': 14, '台南市': 14, '高雄市': 15, '屏東縣': 16, 
        '宜蘭縣': 17, '花蓮縣': 18, '臺東縣': 19, '台東縣': 19, 
        '澎湖縣': 20, '金門縣': 21, '連江縣': 22, '馬祖': 22
    }
    
    intensity_text = ""
    sorted_keys = sorted(intensity_map.keys(), key=sort_intensity, reverse=True)
    for k in sorted_keys:
        k_full = k.translate(str.maketrans('0123456789', '０１２３４５６７８９'))
        county_list = sorted(intensity_map[k], key=lambda x: COUNTY_ORDER.get(x, 99))
        
        chunks = [county_list[i:i+4] for i in range(0, len(county_list), 4)]
        for i, chunk in enumerate(chunks):
            chunk_str = "、".join(chunk)
            if i == 0:
                if len(chunks) > 1:
                    chunk_str += "、"
                intensity_text += f"**{k_full}** {chunk_str}\n"
            else:
                if i < len(chunks) - 1:
                    chunk_str += "、"
                intensity_text += f"　　 {chunk_str}\n"
        
    if intensity_text:
        embed.add_field(name="各地最大震度", value=intensity_text.strip(), inline=False)

    embed.add_field(name="", value=f"```text\n{report_content}\n```", inline=False)

    if img_url:
        embed.set_image(url=img_url)

    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
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
            try:
                mag_display = f"{float(mag):.1f}"
            except (ValueError, TypeError):
                mag_display = str(mag)
            
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

            # 格式化下拉選單顯示的文字
            label = f"{epicenter_short} | 規模 {mag_display}"
            if len(label) > 100: 
                label = label[:97] + "..."
                
            options.append(discord.SelectOption(
                label=label,
                description=f"{time_short} | {eq_no_display}",
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
    def __init__(self, eqs, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.add_item(EarthquakeSelect(eqs))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

class EarthquakeListCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="地震列表", description="🏚️ 手動查詢最新 10 筆地震報告 Earthquakes")
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
        
        view = EarthquakeListView(latest_10_eqs, interaction.user.id)
        embed = build_eq_embed(latest_10_eqs[0])
        
        await interaction.followup.send(content="🏚️ **地震報告列表**", embed=embed, view=view)

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        alert_cog = self.bot.get_cog("EarthquakeAlertCog")
        if not alert_cog:
            await interaction.followup.send("❌ 錯誤：地震警報模組尚未載入，無法抓取資料。", ephemeral=True)
            return
        eqs = await alert_cog.fetch_earthquakes()
        if not eqs:
            await interaction.followup.send("⚠️ 目前無法獲取地震資料。", ephemeral=True)
            return
        eqs.sort(key=lambda x: x.get("EarthquakeInfo", {}).get("OriginTime", ""), reverse=True)
        latest_10_eqs = eqs[:10]
        
        view = EarthquakeListView(latest_10_eqs, interaction.user.id)
        
        selected_val = None
        for row in message.components:
            for child in row.children:
                if getattr(child, "type", None) == discord.ComponentType.select:
                    for opt in child.options:
                        if opt.default:
                            selected_val = opt.value
        
        selected_eq = latest_10_eqs[0]
        if selected_val:
            for eq in latest_10_eqs:
                if eq.get("EarthquakeNo") == selected_val:
                    selected_eq = eq
                    break
                    
        for child in view.children:
            if getattr(child, "type", None) == discord.ComponentType.select:
                for opt in child.options:
                    opt.default = (opt.value == selected_eq.get("EarthquakeNo"))
        
        embed = build_eq_embed(selected_eq)
        await message.edit(embed=embed, view=view)
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)

async def setup(bot):
    await bot.add_cog(EarthquakeListCog(bot))
