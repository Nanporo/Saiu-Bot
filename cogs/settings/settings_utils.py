import discord
from modules.database import get_all_settings, save_all_settings

def load_settings():
    """載入設定檔，直接讀取新版 SQLite 資料庫"""
    return get_all_settings()

def save_settings(data):
    """保存設定檔，直接寫入新版 SQLite 資料庫"""
    save_all_settings(data)


class SpecificMentionRoleSelect(discord.ui.RoleSelect):
    def __init__(self, setting_key: str, placeholder: str = "選擇預警自動標記身分組 (清除請選擇空值)", row: int = 2):
        super().__init__(placeholder=placeholder, min_values=0, max_values=1, row=row)
        self.setting_key = setting_key

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if self.values:
            view.settings[self.setting_key] = self.values[0].id
        else:
            view.settings[self.setting_key] = None
            
        view.all_settings[view.guild_id] = view.settings
        save_settings(view.all_settings)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass