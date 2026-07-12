"""
走時表載入 / 查詢 — 搭配 time.json
================================================================
專案：ExpTech　作者：YuYu1015
time.json 格式（86.7 KB；深度 0–300 km、震央距 0–600 km，涵蓋台灣所有地震）：
    {
      "meta": {...},
      "depth": [...],          # 深度網格 (km)，遞增
      "dist":  [...],          # 震央距網格 (km)，遞增
      "p":  [[...], ...],      # P 走時 (s)，rows=depth、cols=dist
      "sp": [[...], ...]       # S-P (s)；S 走時 = p + sp
    }
查詢用雙線性內插；超出網格邊界則夾到邊界（clamp）。
"""
from __future__ import annotations
import json, os, bisect
from typing import Optional

_T: Optional[dict] = None

def _load() -> dict:
    global _T
    if _T is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "time.json")
        _T = json.load(open(p, encoding="utf-8"))
    return _T


def _loc(grid: list, v: float):
    """回傳 (下界索引 i, 內插比例 f)，並在邊界外夾住。"""
    if v <= grid[0]:
        return 0, 0.0
    if v >= grid[-1]:
        return len(grid) - 2, 1.0
    i = bisect.bisect_right(grid, v) - 1
    f = (v - grid[i]) / (grid[i + 1] - grid[i])
    return i, f


def _bilin(tab: list, dgrid: list, rgrid: list, depth: float, dist: float) -> float:
    i, fi = _loc(dgrid, depth)
    j, fj = _loc(rgrid, dist)
    return (tab[i][j]     * (1 - fi) * (1 - fj)
            + tab[i + 1][j]   * fi       * (1 - fj)
            + tab[i][j + 1]   * (1 - fi) * fj
            + tab[i + 1][j + 1] * fi       * fj)


def travel_times(depth_km: float, epicentral_km: float) -> dict:
    """回傳某深度、某震央距的 P 波、S 波走時與 S-P 前置時間 (秒)。"""
    T = _load()
    p = _bilin(T["p"], T["depth"], T["dist"], depth_km, epicentral_km)
    sp = _bilin(T["sp"], T["depth"], T["dist"], depth_km, epicentral_km)
    return {"P": round(p, 3), "S": round(p + sp, 3), "SP": round(sp, 3)}


def warning_time(depth_km: float, epicentral_km: float, elapsed_since_ot: float = 0.0) -> dict:
    """
    預警倒數：距離「S 波（主要震動）抵達」還有幾秒。
    elapsed_since_ot：從發震時刻到「現在（發報當下）」已過的秒數
                      （＝定位+估規模所花的時間，例如 eBEAR 約 10~20s）。
    """
    tt = travel_times(depth_km, epicentral_km)
    return {**tt, "lead_s": round(tt["S"] - elapsed_since_ot, 2)}


if __name__ == "__main__":
    for D, R in [(10, 50), (30, 150), (100, 200)]:
        print(f"depth={D:3d}km dist={R:4d}km -> {travel_times(D, R)}")
    print("預警範例：深10km、距80km、發報時已過12s ->",
          warning_time(10, 80, elapsed_since_ot=12))
