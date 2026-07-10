import discord
from discord.ext import commands
from discord import app_commands
import re
from datetime import datetime, timezone, timedelta
import logging
from modules.http_client import fetch_json

logger = logging.getLogger(__name__)

class TaipowerView(discord.ui.View):
    def __init__(self, data_dict, total_power, curr_load_str, embed_title, update_time, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.data_dict = data_dict
        self.total_power = total_power
        self.curr_load_str = curr_load_str
        self.embed_title = embed_title
        self.update_time = update_time
        self.show_details = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def build_embed(self):
        desc_text = f"總發電量：**{self.total_power:,.1f} MW**\n"
        if self.curr_load_str:
            desc_text += f"{self.curr_load_str}\n"

        sorted_data = sorted(self.data_dict.items(), key=lambda x: x[1], reverse=True)
        desc_text += "\n"

        for i, (name, power) in enumerate(sorted_data):
            ratio = (power / self.total_power) * 100
            
            bar_length = 7
            filled = int(round((ratio / 75) * bar_length))
            filled = max(1, min(bar_length, filled))
            empty = bar_length - filled
            
            block = "⬜"
            if "核" in name: block = "🟥"
            elif "煤" in name: block = "🟫"
            elif "氣" in name or "風" in name: block = "🟩"
            elif "汽" in name: block = "🟨"
            elif "太陽" in name: block = "🟧"
            elif "水" in name or "抽蓄" in name: block = "🟦"

            progress_bar = (block * filled) + ("⬛" * empty)
            if self.show_details:
                desc_text += f"{progress_bar} `{ratio:>4.1f}%` **{name}**\n> ({power:,.1f} MW)\n"
            else:
                desc_text += f"{progress_bar} `{ratio:>4.1f}%` **{name}**\n"

        embed = discord.Embed(
            title=self.embed_title,
            description=desc_text.strip(),
            color=0xf1c40f
        )

        if self.update_time:
            footer_text = f"台灣電力公司 • 查詢時間 {self.update_time}"
        else:
            current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
            footer_text = f"台灣電力公司 • 查詢時間 {current_time}"

        embed.set_footer(text=footer_text, icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/tpc_logo.png")
        return embed

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary)
    async def toggle_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.show_details = not self.show_details
        if self.show_details:
            button.label = "隱藏詳細資訊"
            button.style = discord.ButtonStyle.secondary
        else:
            button.label = "顯示詳細資訊"
            button.style = discord.ButtonStyle.primary
            
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

class TaipowerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    async def _fetch_taipower_data(self):
        display_url_power = "https://www.taipower.com.tw/2289/2363/2367/2368/10266/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        data_dict = {}
        total_power = 0.0
        update_time = ""
        
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
            data = await fetch_json(url_power_data, headers=headers)
            update_time = data.get("", "")
            
            for row in data.get("aaData", []):
                if len(row) >= 5:
                    if "小計" in str(row[2]):
                        continue
                    match = re.search(r"NAME=['\"]([^'\"]+)['\"]", str(row[0]), re.IGNORECASE)
                    if match:
                        en_type = match.group(1).upper()
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
            logger.error(f"genary_eng.json 抓取失敗: {e}")

        url_para_data = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/loadpara.json"
        embed_title = ""
        curr_load_str = ""
        try:
            para_data = await fetch_json(url_para_data, headers=headers)
            for record in para_data.get("records", []):
                if "curr_load" in record:
                    curr_load = record.get("curr_load", "0")
                    try:
                        cl_mw = float(curr_load) * 10
                        curr_load_str = f"目前用電量：**{cl_mw:,.1f} MW**"
                    except ValueError:
                        curr_load_str = f"目前用電量：**{curr_load} 萬瓩**"
                        
                if "fore_peak_resv_indicator" in record:
                    indicator = record.get("fore_peak_resv_indicator", "G")
                    
                    indicator_text = "`🟢` 供電充裕"
                    if indicator == "Y": indicator_text = "`🟡` 供電吃緊"
                    elif indicator == "O": indicator_text = "`🟠` 供電警戒"
                    elif indicator == "R": indicator_text = "`🔴` 限電警戒"
                    elif indicator == "B": indicator_text = "`⚫` 限電準備"
                    
                    embed_title = indicator_text
        except Exception as e:
            logger.error(f"loadpara.json 抓取失敗: {e}")

        if not data_dict:
            return None, "⚠️ 無法取得台電即時發電量資料，網站可能維護中。"

        total_power = sum(data_dict.values())
        if total_power == 0: total_power = 1.0

        return {
            "data_dict": data_dict,
            "total_power": total_power,
            "curr_load_str": curr_load_str,
            "embed_title": embed_title,
            "update_time": update_time
        }, None

    @app_commands.command(name="台電發電", description="💡 查詢各能源別即時發電量小計 Taipower")
    async def taipower_command(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            result, error = await self._fetch_taipower_data()
            if error:
                await interaction.followup.send(error)
                return
                
            content = "💡 台灣即時發電量"
            view = TaipowerView(
                result["data_dict"], 
                result["total_power"], 
                result["curr_load_str"], 
                result["embed_title"], 
                result["update_time"], 
                interaction.user.id
            )
            embed = view.build_embed()
            
            await interaction.followup.send(content=content, embed=embed, view=view)
        except Exception as e:
            await interaction.followup.send(f"❌ 發生錯誤：{e}")
            logger.error(f"❌ 台電爬蟲發生錯誤：{e}")

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        
        try:
            result, error = await self._fetch_taipower_data()
            if error:
                await interaction.followup.send(error, ephemeral=True)
                return
                
            show_details = False
            for row in message.components:
                for child in row.children:
                    if getattr(child, "type", None) == discord.ComponentType.button:
                        if child.label == "隱藏詳細資訊":
                            show_details = True
                            
            view = TaipowerView(
                result["data_dict"], 
                result["total_power"], 
                result["curr_load_str"], 
                result["embed_title"], 
                result["update_time"], 
                interaction.user.id
            )
            view.show_details = show_details
            if show_details:
                for child in view.children:
                    if getattr(child, "type", None) == discord.ComponentType.button:
                        child.label = "隱藏詳細資訊"
                        child.style = discord.ButtonStyle.secondary
            
            embed = view.build_embed()
            await message.edit(content="💡 台灣即時發電量", embed=embed, view=view)
            await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 發生錯誤：{e}", ephemeral=True)
            logger.error(f"❌ refresh_message (TaipowerCog) 發生錯誤：{e}")

async def setup(bot):
    await bot.add_cog(TaipowerCog(bot))