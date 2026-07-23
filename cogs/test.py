import discord
from discord.ext import commands
from discord import app_commands
from discord.app_commands import Choice
import json
import re
from datetime import datetime
import asyncio
import os
from modules.ownercheck import is_owner
import aiohttp

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    OWNER_SERVER_ID = int(config.get('OWNER_SERVER_ID', 0))
    API_KEY = config.get('CWA_API_KEY')
except Exception:
    OWNER_SERVER_ID = 0
    API_KEY = None

OWNER_GUILDS = [discord.Object(id=OWNER_SERVER_ID)] if OWNER_SERVER_ID else []

class TestEewMapButton(discord.ui.Button):
    def __init__(self, latest_alert):
        super().__init__(label="測試 EEW", style=discord.ButtonStyle.primary, emoji="🚨")
        self.latest_alert = latest_alert
        if not latest_alert:
            self.disabled = True
            self.label = "無 EEW 資料可供測試"

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not self.latest_alert:
            await interaction.followup.send("沒有最新的 EEW 資料可以進行測試。", ephemeral=True)
            return
            
        try:
            from cogs.alarm.alert_eew import (
                render_emulator_map_pil, get_epicenter_name, simulate_gm,
                calc_cdi, cdi_style, get_vs30, load_vs30_grid, TPE_TZ,
                get_county_order, format_fullwidth_grade,
                get_mag_emoji, get_depth_emoji, get_intensity_emoji
            )
            from cogs.list_eq import get_eq_color
            import modules.travel_time as tt
            from modules.location_matcher import town_mapping_cache
            import math
            from datetime import datetime, timedelta
            
            mag = self.latest_alert.get("magnitudeValue", 5.5)
            depth = self.latest_alert.get("depth", 10.0)
            lon = self.latest_alert.get("epicenterLon", 121.6)
            lat = self.latest_alert.get("epicenterLat", 23.9)
            time_str = self.latest_alert.get("originTime", "")
            msg_no = self.latest_alert.get("msgNo", 1)
            loc_desc = self.latest_alert.get("locationDesc", [])
            
            now = datetime.now(TPE_TZ)
            try:
                origin_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TPE_TZ)
            except Exception:
                origin_time = now

            def generate():
                return render_emulator_map_pil(mag, depth, lon, lat, "逆斷層", msg_no, time_str)
                
            loop = asyncio.get_running_loop()
            bytes_io = await loop.run_in_executor(None, generate)

            is_subduction = False
            if depth > 35 and ((lat > 23.5 and lon > 121.5) or (lat < 23.0 and lon < 121.5) or depth > 45):
                is_subduction = True

            nearest_town = "臺北市"
            if town_mapping_cache:
                min_dist = float('inf')
                for _, items in town_mapping_cache.items():
                    for item in items:
                        fullname, t_lat, t_lon = item[0], item[1], item[2]
                        if t_lat is not None and t_lon is not None:
                            dx = (lon - t_lon) * 111 * math.cos(math.radians((lat + t_lat) / 2))
                            dy = (lat - t_lat) * 111
                            dist = math.hypot(dx, dy)
                            if dist < min_dist:
                                min_dist = dist
                                nearest_town = fullname

            sample_locations = [
                ("全台接收", nearest_town),
                ("臺北市中正區", "臺北市中正區"),
                ("花蓮縣花蓮市", "花蓮縣花蓮市"),
                ("高雄市苓雅區", "高雄市苓雅區")
            ]

            load_vs30_grid()
            valid_locs = []
            max_int_val = 0.0
            min_s_ts = float('inf')

            GRADE_TO_FLOAT = {
                '0': 0.0, '1': 1.0, '2': 2.0, '3': 3.0, '4': 4.0,
                '5弱': 5.0, '5強': 5.5, '6弱': 6.0, '6強': 6.5, '7': 7.0
            }

            for display_name, target_name in sample_locations:
                county = target_name[:3] if len(target_name) >= 6 else target_name
                town = target_name[3:] if len(target_name) >= 6 else ""

                target_lon, target_lat = lon, lat
                t_site = None
                if town_mapping_cache and target_name in town_mapping_cache:
                    for item in town_mapping_cache[target_name]:
                        if item[0] == target_name and item[1] is not None and item[2] is not None:
                            target_lat, target_lon = item[1], item[2]
                            if len(item) > 3: t_site = item[3]
                            break

                vs30 = get_vs30(county, town, target_lon, target_lat)
                pga, pgv = simulate_gm(mag, depth, lon, lat, "逆斷層", target_lon, target_lat, is_subduction, vs30, site_factor=t_site)
                cdi = calc_cdi(pga, pgv)
                col, display_grade, _ = cdi_style(cdi)

                dx = (lon - target_lon) * 111 * math.cos(math.radians((lat + target_lat) / 2))
                dy = (lat - target_lat) * 111
                epi_dist_km = math.hypot(dx, dy)

                s_wave_travel_time = tt.travel_times(depth_km=depth, epicentral_km=epi_dist_km)['S']
                s_ts = int((origin_time + timedelta(seconds=s_wave_travel_time)).timestamp())

                val = GRADE_TO_FLOAT.get(str(display_grade), 0.0)
                if val > max_int_val: max_int_val = val
                if s_ts < min_s_ts: min_s_ts = s_ts

                valid_locs.append((display_name, display_grade, s_ts, target_name))

            embed_color = get_eq_color(mag, max_int_val)
            embed = discord.Embed(description="", color=embed_color)

            mag_emoji = get_mag_emoji(mag)
            depth_emoji = get_depth_emoji(depth, mag)

            embed.add_field(name=f"{mag_emoji} 規模", value=str(mag), inline=True)
            embed.add_field(name=f"{depth_emoji} 深度", value=f"{depth} 公里", inline=True)

            content = f"🚨 地震速報 規模 {mag} [測試模式]"
            embed.add_field(name="⚠️ 抵達 (最快)", value=f"<t:{min_s_ts}:R>", inline=True)

            ts = int(origin_time.timestamp())
            embed.add_field(name="發生時間", value=f"<t:{ts}:f>", inline=True)

            epi_name_api = loc_desc[0] if loc_desc else "未知"
            epi_name = get_epicenter_name(lon, lat, epi_name_api)
            embed.add_field(name="震央", value=epi_name, inline=True)

            loc_strings = []
            def get_test_eew_sort_key(item_with_idx):
                idx, (d_name, _, _, _) = item_with_idx
                if d_name == "全台接收":
                    return (0, 0, idx)
                return (1, get_county_order(d_name), idx)

            sorted_valid_locs = [item for _, item in sorted(enumerate(valid_locs), key=get_test_eew_sort_key)]
            for d_name, display_grade, s_ts, t_name in sorted_valid_locs:
                full_grade = format_fullwidth_grade(display_grade)
                int_emoji = get_intensity_emoji(display_grade)
                if d_name == "全台接收":
                    loc_strings.append(f"`{int_emoji}` **{full_grade} {t_name}** | <t:{s_ts}:R> (震央最近區域)")
                else:
                    loc_strings.append(f"`{int_emoji}` **{full_grade} {d_name}** | <t:{s_ts}:R>")
            embed.add_field(name="預估震度", value="\n".join(loc_strings), inline=False)

            file = discord.File(bytes_io, filename="test_eew_map.png")
            embed.set_image(url="attachment://test_eew_map.png")
            embed.set_footer(text=f"中央氣象署 • 接收時間 {now.strftime('%H:%M:%S')} (第 {msg_no} 報) [測試模式]", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")

            await interaction.followup.send(content=content, embed=embed, file=file, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"生成測試 EEW 時發生錯誤：{e!r}", ephemeral=True)

class TestCategorySelect(discord.ui.Select):
    def __init__(self, current_category="status"):
        options = [
            discord.SelectOption(label="API 連線狀態", value="status", emoji="🌐", default=(current_category=="status")),
            discord.SelectOption(label="1小時網格降雨預報", value="rain", emoji="🌧️", default=(current_category=="rain")),
            discord.SelectOption(label="強震即時警報 (EEW)", value="eew", emoji="🚨", default=(current_category=="eew")),
            discord.SelectOption(label="最新災防告警 (CBS)", value="cbs", emoji="⚠️", default=(current_category=="cbs")),
            discord.SelectOption(label="停班停課", value="work", emoji="🎒", default=(current_category=="work")),
            discord.SelectOption(label="淹水測站", value="flood", emoji="💧", default=(current_category=="flood")),
        ]
        super().__init__(placeholder="選擇要測試的資料類別...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.view.update_view(interaction, self.values[0])


class TestView(discord.ui.View):
    def __init__(self, bot, current_category="status"):
        super().__init__(timeout=300)
        self.bot = bot
        self.current_category = current_category
        self.add_item(TestCategorySelect(current_category))
        
        self.eew_button = None

    async def update_view(self, interaction: discord.Interaction, category: str):
        self.current_category = category
        await interaction.response.defer(ephemeral=True)
        
        embed, eew_data = await self.fetch_category_data(category)
        
        # 重新建立 Select 與 Button
        self.clear_items()
        self.add_item(TestCategorySelect(category))
        
        if category == "eew":
            self.eew_button = TestEewMapButton(eew_data)
            self.add_item(self.eew_button)
            
        await interaction.edit_original_response(embed=embed, view=self)

    async def fetch_category_data(self, category: str):
        if category == "status":
            return await self._get_status(), None
        elif category == "rain":
            return await self._get_rain(), None
        elif category == "eew":
            embed, eew_data = await self._get_eew()
            return embed, eew_data
        elif category == "cbs":
            return await self._get_cbs(), None
        elif category == "work":
            return await self._get_work(), None
        elif category == "flood":
            return await self._get_flood(), None

    async def _get_status(self):
        embed = discord.Embed(title="🌐 API 連線狀態", description="正在測試各 API 連線...", color=0x3498db)
        
        statuses = []
        
        # 降雨預報
        rain_cog = self.bot.get_cog("RainForecastCog")
        if rain_cog and rain_cog.latest_rain_data:
            statuses.append("🌧️ **1小時網格降雨預報**: ✅ 已載入資料")
        else:
            statuses.append("🌧️ **1小時網格降雨預報**: ❌ 尚未載入或失敗")
            
        # EEW
        eew_cog = self.bot.get_cog("EEWAlertCog")
        if eew_cog and eew_cog.api_url:
            try:
                async with self.bot.session.get(eew_cog.api_url, timeout=5) as res:
                    if res.status == 200: statuses.append("🚨 **強震即時警報 (EEW)**: ✅ 連線正常 (200)")
                    else: statuses.append(f"🚨 **強震即時警報 (EEW)**: ❌ 異常 ({res.status})")
            except Exception as e:
                statuses.append(f"🚨 **強震即時警報 (EEW)**: ❌ 連線失敗")
        else:
            statuses.append("🚨 **強震即時警報 (EEW)**: ❌ 模組未設定")
            
        # CBS
        url_cbs = f"https://cbs.tw/public/upload/files/json/{datetime.now().strftime('%Y%m')}.json"
        try:
            async with self.bot.session.get(url_cbs, timeout=5) as res:
                if res.status == 200: statuses.append("⚠️ **災防告警 (CBS)**: ✅ 連線正常 (200)")
                elif res.status == 404: statuses.append("⚠️ **災防告警 (CBS)**: 🟢 尚無本月資料 (404)")
                else: statuses.append(f"⚠️ **災防告警 (CBS)**: ❌ 異常 ({res.status})")
        except Exception:
            statuses.append("⚠️ **災防告警 (CBS)**: ❌ 連線失敗")
            
        # Work
        suspension_cog = self.bot.get_cog("SuspensionAlertCog")
        if suspension_cog:
            data = await suspension_cog.fetch_data()
            if data: statuses.append("🎒 **停班停課**: ✅ 連線正常")
            else: statuses.append("🎒 **停班停課**: ❌ 資料取得失敗")
        else:
            statuses.append("🎒 **停班停課**: ❌ 模組未載入")
            
        # Flood
        flood_cog = self.bot.get_cog("FloodForecastCog")
        if flood_cog:
            if flood_cog.latest_flood_data: statuses.append("💧 **淹水深度**: ✅ 已載入資料")
            else: statuses.append("💧 **淹水深度**: ❌ 尚未載入")
        else:
            statuses.append("💧 **淹水深度**: ❌ 模組未載入")
            
        embed.description = "\n\n".join(statuses)
        return embed
        
    async def _get_rain(self):
        embed = discord.Embed(title="🌧️ 1小時網格降雨預報 (最高10筆)", color=0x2980b9)
        rain_cog = self.bot.get_cog("RainForecastCog")
        if rain_cog and rain_cog.latest_rain_data:
            try:
                values_with_index = []
                for idx, v in enumerate(rain_cog.latest_rain_data):
                    v_strip = v.strip()
                    if v_strip:
                        try:
                            val = float(v_strip)
                            if val >= 0.0:
                                values_with_index.append((val, idx))
                        except ValueError:
                            pass
                values_with_index.sort(key=lambda x: x[0], reverse=True)
                top_10 = values_with_index[:10]
                
                unique_towns = {}
                for lst in rain_cog.town_mapping.values():
                    for item in lst:
                        fullname, lat, lon = item[0], item[1], item[2]
                        if lat is not None and lon is not None:
                            unique_towns[fullname] = (lat, lon)
                            
                lines = []
                for i, (val, idx) in enumerate(top_10):
                    grid_x = idx % 441
                    grid_y = idx // 441
                    grid_lon = 117.975 + grid_x * 0.0125
                    grid_lat = 19.975 + grid_y * 0.0125
                    
                    nearest_town = "未知地區"
                    min_dist = float('inf')
                    for town_name, (t_lat, t_lon) in unique_towns.items():
                        dist = (t_lat - grid_lat)**2 + (t_lon - grid_lon)**2
                        if dist < min_dist:
                            min_dist = dist
                            nearest_town = town_name
                            
                    lines.append(f"`{i+1}.` {val} mm - **{nearest_town}** 附近")
                
                embed.description = "\n".join(lines) if lines else "目前全台皆無降雨預報"
            except Exception as e:
                embed.description = f"資料解析失敗: {e!r}"
        else:
            embed.description = "尚未抓取或無快取資料"
        return embed

    async def _get_eew(self):
        embed = discord.Embed(title="🚨 強震即時警報 (最新10筆)", color=0xe74c3c)
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
                                top_10_eew = alerts[:10]
                                latest_alert_data = top_10_eew[0]
                                lines = []
                                for i, alert in enumerate(top_10_eew):
                                    mag = alert.get("magnitudeValue", 0.0)
                                    msg_no = alert.get("msgNo", 1)
                                    loc_desc = alert.get("locationDesc", ["未知"])[0]
                                    time_str = alert.get("originTime", "")
                                    lines.append(f"`{i+1}.` {time_str} - 規模 {mag} ({loc_desc}) [第 {msg_no} 報]")
                                embed.description = "\n".join(lines)
                            else:
                                embed.description = "目前無有效警報資料"
                        else:
                            embed.description = "資料格式不符"
                    else:
                        embed.description = f"API 請求失敗 ({res.status})"
            except Exception as e:
                embed.description = f"錯誤: {e!r}"
        else:
            embed.description = "EEW模組未載入或未設定 API 網址"
            
        return embed, latest_alert_data
        
    async def _get_cbs(self):
        embed = discord.Embed(title="⚠️ 最新災防告警 (最新10筆)", color=0xf1c40f)
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
                        top_10_cbs = alerts[:10]
                        lines = []
                        for i, alert in enumerate(top_10_cbs):
                            topic = alert.get("topic", "災防告警")
                            time_str = alert.get("release_time", "")
                            time_short = time_str[5:16] if len(time_str) >= 16 else time_str
                            lines.append(f"`{i+1}.` {time_short} - {topic}")
                        embed.description = "\n".join(lines)
                    else:
                        embed.description = "本月無告警紀錄"
                elif res.status == 404:
                    embed.description = "本月尚無告警紀錄 (404)"
                else:
                    embed.description = f"API 請求失敗 ({res.status})"
        except Exception as e:
            embed.description = f"錯誤: {e!r}"
        return embed
        
    async def _get_work(self):
        embed = discord.Embed(title="🎒 停班停課異常地區", color=0x2ecc71)
        suspension_cog = self.bot.get_cog("SuspensionAlertCog")
        if suspension_cog:
            try:
                data = await suspension_cog.fetch_data()
                if data is None:
                    embed.description = "無法取得人事行政總處資料"
                else:
                    suspended = [(c, info) for c, info in data.items() if not suspension_cog.is_normal_status(info)]
                    if suspended:
                        lines = [f"`{c}` {info[:20] + '...' if len(info)>20 else info}" for c, info in suspended[:10]]
                        if len(suspended) > 10:
                            lines.append(f"...等共 {len(suspended)} 個縣市或地區")
                        embed.description = "\n".join(lines)
                        embed.color = 0xe74c3c
                    else:
                        embed.description = "✅ 全台皆正常上班上課"
            except Exception as e:
                embed.description = f"資料解析失敗: {e!r}"
        else:
            embed.description = "停班課模組未載入"
        return embed
        
    async def _get_flood(self):
        embed = discord.Embed(title="💧 淹水深度測站 (最高10筆)", color=0x3498db)
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
                        for item in lst:
                            fullname, lat, lon = item[0], item[1], item[2]
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
                    top_10_flood = flood_list[:10]
                    if top_10_flood:
                        embed.description = "\n".join([f"`{i+1}.` {f[0]} - {f[1]} cm" for i, f in enumerate(top_10_flood)])
                    else:
                        embed.description = "全台測站目前皆無積淹水"
                else:
                    embed.description = "無法取得水利署資料"
            except Exception as e:
                embed.description = f"資料解析失敗: {e!r}"
        else:
            embed.description = "淹水模組未載入"
        return embed


class TestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @app_commands.command(name="資料測試", description="（限擁有者）測試並顯示各模組的API與資料狀況")
    @app_commands.choices(
        category=[
            Choice(name="API 連線狀態", value="status"),
            Choice(name="1小時網格降雨預報", value="rain"),
            Choice(name="強震即時警報 (EEW)", value="eew"),
            Choice(name="最新災防告警 (CBS)", value="cbs"),
            Choice(name="停班停課", value="work"),
            Choice(name="淹水深度", value="flood"),
        ]
    )
    @app_commands.guilds(*OWNER_GUILDS) 
    async def test_data_command(self, interaction: discord.Interaction, category: str = "status"):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        view = TestView(self.bot, category)
        embed, eew_data = await view.fetch_category_data(category)
        
        if category == "eew":
            view.eew_button = TestEewMapButton(eew_data)
            view.add_item(view.eew_button)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(TestCog(bot))