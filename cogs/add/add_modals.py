"""舊版表單匯入相容入口；實作依功能拆分至各 add 模組。"""

from cogs.add.add_roles import PREFIX_MAP, RoleSetupView
from cogs.add.add_earthquake import EqModal, EewModal
from cogs.add.add_town import TownModal
from cogs.add.add_hours import NotifyHoursSetupView
from cogs.add.add_rain import ThunderstormToggleView, MinRainfallSetupView
from cogs.add.add_cbs import CBSTypesSetupView, CBSFilterSetupView


__all__ = [
    "PREFIX_MAP", "RoleSetupView", "EqModal", "EewModal", "TownModal",
    "NotifyHoursSetupView", "ThunderstormToggleView", "MinRainfallSetupView",
    "CBSTypesSetupView", "CBSFilterSetupView",
]


async def setup(bot):
    pass
