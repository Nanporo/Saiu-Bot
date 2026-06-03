import discord
from discord.ext import commands
from discord import app_commands
import json
from datetime import datetime, timezone, timedelta

class RainfallView(discord.ui.View):
    def __init__(self, bot, api_key, results, show_image=False):
        super().__init__(timeout=300)
        self.bot = bot
        self.api_key = api_key
        self.results = results
        self.is_large_interval = False
        self.show_image = show_image
        self.show_details = False

        self.color_button = None
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "切換毫米顏色":
                    self.color_button = child
                elif child.label == "顯示雨量圖":
                    if self.show_image:
                        child.label = "隱藏雨量圖"

        # 若預設不顯示雨量圖，則先將切換顏色按鈕從視圖中移除
        if not self.show_image and self.color_button:
            self.remove_item(self.color_button)

    def build_embed(self):
        message_content = "☔ 今日累積雨量測站排行"
        embed = discord.Embed(color=0x3498db)
        
        lines = []
        for i, r in enumerate(self.results[:10]):
            # 決定降雨量特報燈號
            precip_val = r['precip']
            icon = "💧"
            if precip_val >= 500.0:
                icon = "🟣"
            elif precip_val >= 350.0:
                icon = "🔴"
            elif precip_val >= 200.0:
                icon = "🟠"
            elif precip_val >= 80.0:
                icon = "🟡"

            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'][i]
            if i < 3:
                rank_str = ['`🥇`', '`🥈`', '`🥉`'][i]
                line = f"{num_emoji} `{icon} {precip_val} mm` **{r['county']}{r['town']}** {rank_str}"
            else:
                line = f"{num_emoji} `{icon} {precip_val} mm` **{r['county']}{r['town']}**"

            if self.show_details:
                line += f"\n> {r['station']}"
            lines.append(line)
        
        embed.description = "\n".join(lines)
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")

        if self.show_image:
            data_id = "O-A0040-001" if self.is_large_interval else "O-A0040-002"
            timestamp = (int(datetime.now().timestamp()) // 300) * 300
            product_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/{data_id}.jpg?t={timestamp}"
            embed.set_image(url=product_url)
            
        return message_content, embed

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary, row=0)
    async def toggle_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.show_details = not self.show_details
        if self.show_details:
            button.label = "隱藏詳細資訊"
            button.style = discord.ButtonStyle.secondary
        else:
            button.label = "顯示詳細資訊"
            button.style = discord.ButtonStyle.primary
            
        content, embed = self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    @discord.ui.button(label="顯示雨量圖", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_display(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.show_image = not self.show_image
        
        if self.show_image:
            button.label = "隱藏雨量圖"
            if self.color_button and self.color_button not in self.children:
                self.add_item(self.color_button)
        else:
            button.label = "顯示雨量圖"
            if self.color_button and self.color_button in self.children:
                self.remove_item(self.color_button)
            
        content, embed = self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    @discord.ui.button(label="切換毫米顏色", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.is_large_interval = not self.is_large_interval
        content, embed = self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

class RainfallCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @app_commands.command(name="今日雨量", description="查詢今日台灣各測站的累積雨量排行")
    @app_commands.describe(
        雨量圖="是否顯示今日累積雨量圖"
    )
    @app_commands.choices(雨量圖=[
        app_commands.Choice(name="顯示", value="yes"),
        app_commands.Choice(name="不顯示", value="no")
    ])
    async def rainfall_command(self, interaction: discord.Interaction, 雨量圖: app_commands.Choice[str] = None):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        # 避免 API 回應過慢導致超時報錯
        await interaction.response.defer()

        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001?Authorization={self.api_key}&RainfallElement=Now"
        show_image = 雨量圖 and 雨量圖.value == "yes"

        try:
            async with self.bot.session.get(url) as response:
                if response.status != 200:
                    await interaction.followup.send(f"⚠️ API 請求失敗，狀態碼：{response.status}")
                    return
                
                data = await response.json()
                stations = data.get('records', {}).get('Station', [])

                results = []
                for st in stations:
                    station_name = st.get('StationName', '未知')
                    geo_info = st.get('GeoInfo', {})
                    county = geo_info.get('CountyName', '')
                    town = geo_info.get('TownName', '')
                    
                    precip_info = st.get('RainfallElement', {}).get('Now', {})
                    precip_str = precip_info.get('Precipitation', '-99')

                    try:
                        precip_val = float(precip_str)
                    except ValueError:
                        continue

                    # 排除氣象署資料的無效值（例如 -99.0 或 -998.0）與無雨量的測站 (0.0)
                    if precip_val <= 0.0:
                        continue
                        
                    results.append({
                        "station": station_name,
                        "county": county,
                        "town": town,
                        "precip": precip_val
                    })

                if not results:
                    await interaction.followup.send("⚠️ 目前尚無大於 0.0 mm 的雨量資料。")
                    return

                results.sort(key=lambda x: x['precip'], reverse=True)
                
                view = RainfallView(self.bot, self.api_key, results, show_image)
                content, embed = view.build_embed()
                await interaction.followup.send(content=content, embed=embed, view=view)

        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            print(f"❌ /今日雨量 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(RainfallCog(bot))
