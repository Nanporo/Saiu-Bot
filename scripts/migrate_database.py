import sys
import logging
from pathlib import Path

# 修正 Windows 控制台 UTF-8 輸出
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("migration_script")

from modules.database import check_and_migrate_schema, init_db, get_all_settings

def main():
    print("=" * 60)
    print("🚀 Saiu-Bot 資料庫 Schema 自動感知正規化遷移工具")
    print("=" * 60)
    print("本腳本可用於在正式伺服器上獨立進行資料庫檢查與無痛升級。\n")
    
    try:
        init_db()
        settings = get_all_settings()
        print(f"\n✅ 遷移與驗證完成！目前系統共有 {len(settings)} 個伺服器設定運作中。")
    except Exception as e:
        print(f"\n❌ 遷移過程發生錯誤: {e!r}")
        sys.exit(1)

if __name__ == '__main__':
    main()
