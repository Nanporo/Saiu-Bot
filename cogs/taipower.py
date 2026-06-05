import discord
from discord.ext import commands
from discord import app_commands
import re
from datetime import datetime, timezone, timedelta

class TaipowerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="台電發電", description="查詢各能源別即時發電量小計")
    async def taipower_command(self, interaction: discord.Interaction):
        await interaction.response.defer()

        display_url_power = "https://www.taipower.com.tw/2289/2363/2367/2368/10266/"
        
        try:
            # 增加 User-Agent 模擬一般瀏覽器，避免被阻擋
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            data_dict = {}
            total_power = 0.0
            update_time = ""
            
            # 能源類別中英對照表
            energy_map = {
                "NUCLEAR": "核能",
                "COAL": "燃煤",
                "IPPCOAL": "民營燃煤",
                "LNG": "燃氣",
                "IPPLNG": "民營燃氣",
                "COGEN": "汽電共生",
                "FUELOIL": "燃油",
                "SOLAR": "太陽能",
                "WIND": "風力",
                "HYDRO": "水力",
                "ENERGYSTORAGESYSTEM": "抽蓄與儲能",
                "OTHERRENEWABLEENERGY": "其他再生能源"
            }
            
            url_power_data = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/genary.json"
            try:
                async with self.bot.session.get(url_power_data, headers=headers) as response:
                    if response.status in [200, 202]:
                        data = await response.json(content_type=None)
                        update_time = data.get("", "")
                        
                        for row in data.get("aaData", []):
                            if len(row) >= 5:
                                # 忽略「小計」
                                if "小計" in str(row[2]):
                                    continue
                                    
                                # 從 <A NAME='...'> 標籤中提取能源代號
                                match = re.search(r"NAME=['\"]([^'\"]+)['\"]", str(row[0]), re.IGNORECASE)
                                if match:
                                    en_type = match.group(1).upper()
                                    # 忽略儲能負載
                                    if en_type == "ENERGYSTORAGESYSTEMLOAD":
                                        continue
                                        
                                    tw_name = energy_map.get(en_type, en_type)
                                    
                                    try:
                                        power_val = float(str(row[4]).replace(',', ''))
                                        if power_val > 0:
                                            data_dict[tw_name] = data_dict.get(tw_name, 0.0) + power_val
                                    except ValueError:
                                        pass
            except Exception as e:
                print(f"genary_eng.json 抓取失敗: {e}")

            if not data_dict:
                await interaction.followup.send("⚠️ 無法取得台電即時發電量資料，網站可能維護中。")
                return

            # 計算總發電量
            total_power = sum(data_dict.values())
            if total_power == 0:
                total_power = 1.0 # 避免除以零報錯

            embed = discord.Embed(
                title="⚡ 台灣即時發電量",
                description=f"總發電量：**{total_power:,.1f} MW**\n資料來源：[發電量小計]({display_url_power})",
                color=0xf1c40f
            )

            # 依照發電量由大到小排序
            sorted_data = sorted(data_dict.items(), key=lambda x: x[1], reverse=True)

            for name, power in sorted_data:
                ratio = (power / total_power) * 100
                
                # 製作圖形化進度條 (長度固定 15 格)
                bar_length = 15
                filled = int(round((ratio / 100) * bar_length))
                empty = bar_length - filled
                
                # 依照不同能源選擇圖示顏色
                block = "⬜"
                if "核" in name: block = "🟥"
                elif "煤" in name: block = "🟧"
                elif "氣" in name or "風" in name: block = "🟩"
                elif "汽" in name: block = "🟨"
                elif "太陽" in name: block = "❇️"
                elif "水" in name or "抽蓄" in name: block = "🟦"

                progress_bar = (block * filled) + ("⬛" * empty)
                embed.add_field(name=f"{name}", value=f"{progress_bar} `{ratio:>5.1f}%`\n└ **{power:,.1f} MW**", inline=False)

            if update_time:
                footer_text = f"台灣電力公司 • 查詢時間 {update_time}"
            else:
                current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                footer_text = f"台灣電力公司 • 查詢時間 {current_time}"

            embed.set_footer(text=footer_text, icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/tpc_logo.png")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"❌ 發生錯誤：{e}")
            print(f"❌ 台電爬蟲發生錯誤：{e}")

async def setup(bot):
    await bot.add_cog(TaipowerCog(bot))