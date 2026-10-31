"""加入指令的流程路由與操作使用者檢查。"""

import discord
from cogs.add.add_county import CountySelect
from cogs.add.add_earthquake import EqSetupButton
from cogs.add.add_safety import SafetySetupButton
from cogs.add.add_town import TownSetupButton
from cogs.add.add_tsunami import TsunamiRegionJoinSelect

LOCATION_TYPES = {"suspension", "typhoon", "traffic", "rain", "temp", "cbs", "eew", "aqi"}
SETUP_COMPONENTS = {
    **{category: CountySelect for category in ("suspension", "typhoon", "traffic")},
    **{category: TownSetupButton for category in ("rain", "temp", "cbs", "eew", "aqi")},
    "earthquake": EqSetupButton,
    "safety": SafetySetupButton,
    "tsunami": TsunamiRegionJoinSelect,
}


class AlertSetupView(discord.ui.View):
    def __init__(self, alert_type, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        factory = SETUP_COMPONENTS[alert_type]
        self.add_item(factory(alert_type) if alert_type in LOCATION_TYPES else factory())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True


async def setup(bot):
    pass
