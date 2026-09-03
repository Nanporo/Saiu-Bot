import discord
import logging
from typing import List, Dict, Set
from modules.database import get_all_module_switches, set_module_switch, set_multiple_module_switches, is_push_module_enabled
from modules.module_manager import PUSH_MODULES, PUSH_MODULE_DICT
from modules.ownercheck import is_owner

logger = logging.getLogger(__name__)

class ModuleSelect(discord.ui.Select):
    def __init__(self, bot: discord.Client):
        self.bot = bot
        options = []
        for mod in PUSH_MODULES:
            is_enabled = is_push_module_enabled(mod["key"])
            desc = mod.get("description")
            options.append(
                discord.SelectOption(
                    label=mod["name"],
                    value=mod["key"],
                    description=desc[:100] if desc else None,
                    emoji=mod["emoji"],
                    default=is_enabled
                )
            )

        super().__init__(
            placeholder="點此勾選要啟動的自動推送模組...",
            min_values=0,
            max_values=len(PUSH_MODULES),
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_keys: Set[str] = set(self.values)
        
        errors = []
        for mod in PUSH_MODULES:
            key = mod["key"]
            ext = mod["extension"]
            is_checked = key in selected_keys
            is_currently_loaded = ext in self.bot.extensions

            set_module_switch(key, is_checked)

            if is_checked and not is_currently_loaded:
                try:
                    await self.bot.load_extension(ext)
                    logger.info(f"🟢 [模組開關] 成功動態載入模組: {ext}")
                except Exception as e:
                    logger.error(f"❌ [模組開關] 動態載入 {ext} 失敗: {e}")
                    errors.append(f"{mod['name']} 載入失敗: {e}")
            elif not is_checked and is_currently_loaded:
                try:
                    await self.bot.unload_extension(ext)
                    logger.info(f"🔴 [模組開關] 成功動態卸載模組: {ext}")
                except Exception as e:
                    logger.error(f"❌ [模組開關] 動態卸載 {ext} 失敗: {e}")
                    errors.append(f"{mod['name']} 卸載失敗: {e}")

        # 更新下拉選單預設勾選狀態
        for option in self.options:
            option.default = option.value in selected_keys

        view: ModuleSwitchView = self.view
        embed = view.build_embed(error_msg="\n".join(errors) if errors else None)
        await interaction.edit_original_response(content="🤖 機器人模組開關", embed=embed, view=view)


class ModuleSwitchView(discord.ui.View):
    def __init__(self, bot: discord.Client, owner_user_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.owner_user_id = owner_user_id
        self.select_menu = ModuleSelect(self.bot)
        self.add_item(self.select_menu)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_user_id and not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 只有機器人擁有者可以操作此選單！", ephemeral=True)
            return False
        return True

    def build_embed(self, error_msg: str = None) -> discord.Embed:
        embed = discord.Embed(
            title="選擇機器人要啟動的模組",
            description="在下方選單**勾選或取消勾選**要啟動的自動推送模組。\n"
                        "停用的模組將**即時卸載並停止背景輪詢**，設定會保存至資料庫，重啟後依然生效。\n"
                        "（手動查詢指令如 `/天氣`、`/雷達回波` 等不受此開關影響）",
            color=0x41809b
        )

        for mod in PUSH_MODULES:
            key = mod["key"]
            ext = mod["extension"]
            enabled_in_db = is_push_module_enabled(key)
            loaded_in_bot = ext in self.bot.extensions

            if enabled_in_db and loaded_in_bot:
                status = "`🟢` 運行中"
            elif not enabled_in_db and not loaded_in_bot:
                status = "`🔴` 已停用"
            elif enabled_in_db and not loaded_in_bot:
                status = "`⚠️` 載入失敗"
            else:
                status = "`🟡` 待卸載"

            embed.add_field(name=f"{mod['emoji']} {mod['name']}", value=status, inline=True)

        # 補齊 3 的倍數排版（10 個項目補 2 個空白場位保持美觀）
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        if error_msg:
            embed.add_field(name="⚠️ 操作警告", value=f"```\n{error_msg}\n```", inline=False)
        return embed

    @discord.ui.button(label="全選開啟", style=discord.ButtonStyle.success, row=1)
    async def enable_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        errors = []
        for mod in PUSH_MODULES:
            key = mod["key"]
            ext = mod["extension"]
            set_module_switch(key, True)
            if ext not in self.bot.extensions:
                try:
                    await self.bot.load_extension(ext)
                    logger.info(f"🟢 [模組開關] 全選開啟：載入 {ext}")
                except Exception as e:
                    logger.error(f"❌ [模組開關] 全選開啟：載入 {ext} 失敗: {e}")
                    errors.append(f"{mod['name']} 載入失敗: {e}")

        for option in self.select_menu.options:
            option.default = True

        embed = self.build_embed(error_msg="\n".join(errors) if errors else None)
        await interaction.edit_original_response(content="🤖 機器人模組開關", embed=embed, view=self)

    @discord.ui.button(label="全部關閉", style=discord.ButtonStyle.danger, row=1)
    async def disable_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        errors = []
        for mod in PUSH_MODULES:
            key = mod["key"]
            ext = mod["extension"]
            set_module_switch(key, False)
            if ext in self.bot.extensions:
                try:
                    await self.bot.unload_extension(ext)
                    logger.info(f"🔴 [模組開關] 全部關閉：卸載 {ext}")
                except Exception as e:
                    logger.error(f"❌ [模組開關] 全部關閉：卸載 {ext} 失敗: {e}")
                    errors.append(f"{mod['name']} 卸載失敗: {e}")

        for option in self.select_menu.options:
            option.default = False

        embed = self.build_embed(error_msg="\n".join(errors) if errors else None)
        await interaction.edit_original_response(content="🤖 機器人模組開關", embed=embed, view=self)

    @discord.ui.button(label="關閉", style=discord.ButtonStyle.secondary, emoji="❌", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
        self.stop()

async def setup(bot):
    pass
