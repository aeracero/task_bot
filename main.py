import discord
from discord.ext import commands
import os
from database import db

# 永続化するViewをインポート
from cogs.tickets import TicketView
from cogs.rooms import RoomControlView

TOKEN = os.getenv("DISCORD_TOKEN")

class MyBot(commands.Bot):
    def __init__(self):
        # Intentsの設定
        intents = discord.Intents.default()
        intents.message_content = True # メッセージ内容の取得 (コマンド等に必要)
        intents.members = True         # メンバー情報の取得 (通知等に必要)
        
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # データベースの初期化 (テーブル作成・マイグレーション)
        await db.init_db()
        
        # Cogs(拡張機能)のロード
        # 注意: ファイル名に合わせてパスを指定してください
        await self.load_extension("cogs.tickets")      # チケット募集機能
        await self.load_extension("cogs.rooms")        # 使い捨てVC機能
        await self.load_extension("cogs.settings")     # 設定機能
        await self.load_extension("cogs.sheets_sync")  # スプレッドシート連携機能 (NEW)
        
        # 再起動後もボタンが動作するようにViewを登録
        self.add_view(TicketView())
        self.add_view(RoomControlView())
        
        # コマンドツリーの同期 (スラッシュコマンドの登録)
        await self.tree.sync()
        print("--- System Online: Commands synced & Views registered ---")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} guilds")

bot = MyBot()

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN environment variable is not set.")
    else:
        bot.run(TOKEN)