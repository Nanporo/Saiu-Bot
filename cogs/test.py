import discord
from discord.ext import commands
from discord import app_commands
import json
import re
from datetime import datetime
import asyncio
import os
from modules.ownercheck import is_owner

class TestEewMapView(discord.ui.View):
    def __init__(self, latest_alert=None):
        super().__init__(timeout=300)
        self.latest_alert = latest_alert
        btn = discord.ui.Button(label="測試 EEW 地圖生成", style=discord.ButtonStyle.primary, emoji="🗺️")
        btn.callback = self.test_map
        if not latest_alert:
            btn.disabled = True
            btn.label = "無 EEW 資料可供測試"
        self.add_item(btn)

    async def test_map(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not self.latest_alert:
            await interaction.followup.send("沒有最新的 EEW 資料可以進行測試。", ephemeral=True)
            return
            
        try:
            from cogs.alarm.alert_eew import render_emulator_map_pil
            
            mag = self.latest_alert.get("magnitudeValue", 5.5)
            depth = self.latest_alert.get("depth", 10.0)
            lon = self.latest_alert.get("epicenterLon", 121.6)
            lat = self.latest_alert.get("epicenterLat", 23.9)
            
            time_str = self.latest_alert.get("originTime", "")
            msg_no = self.latest_alert.get("msgNo", 1)
            
            def generate():
                return render_emulator_map_pil(mag, depth, lon, lat, "逆斷層", msg_no, time_str)
                
            loop = asyncio.get_event_loop()
            out_file = await loop.run_in_executor(None, generate)
            
            file = discord.File(out_file, filename="test_eew_map.png")
            msg = f"這是以最新一報資料 (規模 {mag}, 深度 {depth}km) 產生的 EEW 地圖測試："
            await interaction.followup.send(msg, file=file, ephemeral=True)
            
            if os.path.exists(out_file):
                os.remove(out_file)
        except Exception as e:
            await interaction.followup.send(f"生成地圖時發生錯誤：{e}", ephemeral=True)

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    OWNER_SERVER_ID = int(config.get('OWNER_SERVER_ID', 0))
except Exception:
    OWNER_SERVER_ID = 0

OWNER_GUILDS = [discord.Object(id=OWNER_SERVER_ID)] if OWNER_SERVER_ID else []

class TestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @app_commands.command(name="資料測試", description="（限擁有者）測試並顯示各氣象模組抓取到的前三筆數據狀況")
    @app_commands.guilds(*OWNER_GUILDS) 
    async def test_data_command(self, interaction: discord.Interaction):
        # 權限檢查
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        content = "📊 氣象資料抓取測試狀態"
        embed = discord.Embed(
            title="",
            description="目前各氣象模組抓取到的最新數據（排序後的前三筆最高值）",
            color=0x9b59b6
        )

        # ================= 1. 降雨預報 =================
        rain_cog = self.bot.get_cog("RainForecastCog")
        if rain_cog and rain_cog.latest_rain_data:
            try:
                # 過濾空字串並轉為浮點數，同時保留網格索引
                values_with_index = []
                for idx, v in enumerate(rain_cog.latest_rain_data):
                    v_strip = v.strip()
                    if v_strip:
                        try:
                            val = float(v_strip)
                            if val >= 0.0:  # 排除 -99 等無效值
                                values_with_index.append((val, idx))
                        except ValueError:
                            pass
                            
                values_with_index.sort(key=lambda x: x[0], reverse=True)
                top_3 = values_with_index[:3]
                
                # 建立不重複的鄉鎮中心座標對照表
                unique_towns = {}
                for lst in rain_cog.town_mapping.values():
                    for fullname, lat, lon in lst:
                        if lat is not None and lon is not None:
                            unique_towns[fullname] = (lat, lon)
                            
                lines = []
                for i, (val, idx) in enumerate(top_3):
                    grid_x = idx % 441
                    grid_y = idx // 441
                    grid_lon = 117.975 + grid_x * 0.0125
                    grid_lat = 19.975 + grid_y * 0.0125
                    
                    nearest_town = "未知地區"
                    min_dist = float('inf')
                    for town_name, (t_lat, t_lon) in unique_towns.items():
                        # 計算該網格中心與所有鄉鎮市區中心的直線距離 (經緯度平方差)
                        dist = (t_lat - grid_lat)**2 + (t_lon - grid_lon)**2
                        if dist < min_dist:
                            min_dist = dist
                            nearest_town = town_name
                            
                    lines.append(f"`{i+1}.` {val} mm - **{nearest_town}** 附近")
                
                text = "\n".join(lines)
                embed.add_field(name="🌧️ 1小時網格降雨預報", value=text or "無資料", inline=False)
            except Exception as e:
                embed.add_field(name="🌧️ 1小時網格降雨預報", value=f"資料解析失敗: {e}", inline=False)
        else:
            embed.add_field(name="🌧️ 1小時網格降雨預報", value="尚未抓取或無快取資料", inline=False)

        if not self.api_key:
            embed.add_field(name="⚠️ 錯誤", value="未設定 API Key，無法查詢氣溫與累積雨量", inline=False)
            await interaction.followup.send(content=content, embed=embed)
            return

        # ================= 2. 強震即時警報 (EEW) =================
        eew_cog = self.bot.get_cog("EEWAlertCog")
        latest_alert_data = None
        
        if eew_cog and eew_cog.api_url:
            try:
                async with self.bot.session.get(eew_cog.api_url, timeout=5) as res:
                    if res.status == 200:
                        data = await res.json()
                        if data.get("success") and "data" in data:
                            alerts = data["data"]
                            if alerts:
                                alerts.sort(key=lambda x: x.get("identifier", ""), reverse=True)
                                top_5_eew = alerts[:5]
                                latest_alert_data = top_5_eew[0]
                                lines = []
                                for i, alert in enumerate(top_5_eew):
                                    mag = alert.get("magnitudeValue", 0.0)
                                    msg_no = alert.get("msgNo", 1)
                                    loc_desc = alert.get("locationDesc", ["未知"])[0]
                                    time_str = alert.get("originTime", "")
                                    lines.append(f"`{i+1}.` {time_str} - 規模 {mag} ({loc_desc}) [第 {msg_no} 報]")
                                text = "\n".join(lines)
                                embed.add_field(name="🚨 強震即時警報 (EEW)", value=text, inline=False)
                            else:
                                embed.add_field(name="🚨 強震即時警報 (EEW)", value="目前無有效警報資料", inline=False)
                        else:
                            embed.add_field(name="🚨 強震即時警報 (EEW)", value="資料格式不符", inline=False)
                    else:
                        embed.add_field(name="🚨 強震即時警報 (EEW)", value=f"API 請求失敗 ({res.status})", inline=False)
            except Exception as e:
                embed.add_field(name="🚨 強震即時警報 (EEW)", value=f"錯誤: {e}", inline=False)
        else:
            embed.add_field(name="🚨 強震即時警報 (EEW)", value="EEW模組未載入或未設定 API 網址", inline=False)

        # ================= 3. 今日雨量 =================
        url_rain = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001?Authorization={self.api_key}&RainfallElement=Now"
        try:
            async with self.bot.session.get(url_rain) as res:
                if res.status == 200:
                    data = await res.json()
                    stations = data.get('records', {}).get('Station', [])
                    rain_list = []
                    for st in stations:
                        st_name = st.get('StationName', '未知')
                        r_str = st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99')
                        try:
                            r_val = float(r_str)
                            if r_val >= 0.0:
                                rain_list.append((st_name, r_val))
                        except ValueError:
                            pass
                    rain_list.sort(key=lambda x: x[1], reverse=True)
                    top_3_rain = rain_list[:3]
                    text = "\n".join([f"`{i+1}.` {r[0]} - {r[1]} mm" for i, r in enumerate(top_3_rain)])
                    embed.add_field(name="☔ 今日累積雨量 (最高)", value=text or "無資料", inline=False)
                else:
                    embed.add_field(name="☔ 今日累積雨量", value=f"API 請求失敗 ({res.status})", inline=False)
        except Exception as e:
            embed.add_field(name="☔ 今日累積雨量", value=f"錯誤: {e}", inline=False)

        # ================= 4. 最新災防告警 (CBS) =================
        url_cbs = f"https://cbs.tw/public/upload/files/json/{datetime.now().strftime('%Y%m')}.json"
        try:
            async with self.bot.session.get(url_cbs, timeout=5) as res:
                if res.status == 200:
                    data = await res.json()
                    alerts = []
                    for date_k, time_dict in data.get("data", {}).items():
                        for time_k, id_dict in time_dict.items():
                            for j_id, alert in id_dict.items():
                                alerts.append(alert)
                    
                    if alerts:
                        alerts.sort(key=lambda x: x.get("release_time", ""), reverse=True)
                        top_3_cbs = alerts[:3]
                        lines = []
                        for i, alert in enumerate(top_3_cbs):
                            topic = alert.get("topic", "災防告警")
                            time_str = alert.get("release_time", "")
                            time_short = time_str[5:16] if len(time_str) >= 16 else time_str
                            lines.append(f"`{i+1}.` {time_short} - {topic}")
                        text = "\n".join(lines)
                        embed.add_field(name="⚠️ 最新災防告警 (前3筆)", value=text, inline=False)
                    else:
                        embed.add_field(name="⚠️ 最新災防告警 (CBS)", value="本月無告警紀錄", inline=False)
                else:
                    embed.add_field(name="⚠️ 最新災防告警 (CBS)", value=f"API 請求失敗 ({res.status})", inline=False)
        except Exception as e:
            embed.add_field(name="⚠️ 最新災防告警 (CBS)", value=f"錯誤: {e}", inline=False)

        # ================= 5. 停班停課 =================
        suspension_cog = self.bot.get_cog("SuspensionAlertCog")
        if suspension_cog:
            try:
                data = await suspension_cog.fetch_data()
                if data is None:
                    embed.add_field(name="🎒 停班停課", value="無法取得人事行政總處資料", inline=False)
                else:
                    suspended = [(c, info) for c, info in data.items() if not suspension_cog.is_normal_status(info)]
                    if suspended:
                        text = "\n".join([f"`{c}` {info[:15] + '...' if len(info)>15 else info}" for c, info in suspended[:3]])
                        if len(suspended) > 3: text += f"\n...等 {len(suspended)} 個縣市"
                        embed.add_field(name="🎒 停班停課 (異常)", value=text, inline=False)
                    else:
                        embed.add_field(name="🎒 停班停課", value="✅ 全台皆正常上班上課", inline=False)
            except Exception as e:
                embed.add_field(name="🎒 停班停課", value=f"資料解析失敗: {e}", inline=False)
        else:
            embed.add_field(name="🎒 停班停課", value="停班課模組未載入", inline=False)

        # ================= 6. 淹水測站 =================
        flood_cog = self.bot.get_cog("FloodForecastCog")
        if flood_cog:
            try:
                if not flood_cog.latest_flood_data:
                    await flood_cog.fetch_all_stations()
                if flood_cog.latest_flood_data:
                    from modules.town_mapping import load_town_mapping
                    town_mapping = load_town_mapping()
                    unique_towns = {}
                    for lst in town_mapping.values():
                        for fullname, lat, lon in lst:
                            if lat is not None and lon is not None:
                                unique_towns[fullname] = (lat, lon)
                    
                    flood_list = []
                    for st in flood_cog.latest_flood_data:
                        st_name = st.get("Thing", {}).get("properties", {}).get("stationName", "未知")
                        obs = st.get("Observations", [])
                        
                        coords = st.get("Thing", {}).get("Locations", [{}])[0].get("location", {}).get("coordinates", [])
                        nearest_town = ""
                        if len(coords) >= 2:
                            st_lon, st_lat = coords[0], coords[1]
                            min_dist = float('inf')
                            for town_name, (t_lat, t_lon) in unique_towns.items():
                                dist = (t_lat - st_lat)**2 + (t_lon - st_lon)**2
                                if dist < min_dist:
                                    min_dist = dist
                                    nearest_town = town_name
                        
                        if nearest_town and nearest_town not in st_name:
                            st_name = f"{nearest_town} {st_name}"
                            
                        if obs:
                            try:
                                val = float(obs[0].get("result", 0))
                                if 2.0 <= val < 1000.0:
                                    flood_list.append((st_name, round(val, 1)))
                            except ValueError:
                                pass
                    flood_list.sort(key=lambda x: x[1], reverse=True)
                    top_3_flood = flood_list[:3]
                    if top_3_flood:
                        text = "\n".join([f"`{i+1}.` {f[0]} - {f[1]} cm" for i, f in enumerate(top_3_flood)])
                        embed.add_field(name="💧 淹水深度 (最高)", value=text, inline=False)
                    else:
                        embed.add_field(name="💧 淹水深度", value="全台測站目前皆無積淹水", inline=False)
                else:
                    embed.add_field(name="💧 淹水深度", value="無法取得水利署資料", inline=False)
            except Exception as e:
                embed.add_field(name="💧 淹水深度", value=f"資料解析失敗: {e}", inline=False)
        else:
            embed.add_field(name="💧 淹水深度", value="淹水模組未載入", inline=False)

        # 發送結果
        await interaction.followup.send(content=content, embed=embed, view=TestEewMapView(latest_alert_data))

async def setup(bot):
    await bot.add_cog(TestCog(bot))