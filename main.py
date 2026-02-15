import discord
from discord.ext import commands
import os
from database import db

# 永続化するViewをインポート (再起動後もボタンが動くように)
# ※ファイルが存在しない場合はエラーになるため、ファイル構成を確認してください
from cogs.tickets import TicketView
from cogs.rooms import RoomControlView

TOKEN = os.getenv("DISCORD_TOKEN")

class MyBot(commands.Bot):
    def __init__(self):
        # Intents(権限)の設定
        intents = discord.Intents.default()
        intents.message_content = True # メッセージ内容の取得 (コマンド等に必要)
        intents.members = True         # メンバー情報の取得 (DM通知等に必要)
        
        # コマンドプレフィックスは "!" ですが、今回は主にスラッシュコマンドを使用します
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Bot起動時の初期化処理"""
        print("--- Initializing ---")

        # 1. データベースの初期化
        await db.init_db()
        
        # 2. Cogs(機能モジュール)のロード
        # 各機能は独立したファイルとして管理されています
        extensions = [
            "cogs.tickets",      # タスク募集・チケット機能 (/recruit)
            "cogs.rooms",        # 使い捨てVC機能 (/temp_vc)
            "cogs.settings",     # 設定機能 (/set_..., /connect_sheet)
            "cogs.sheets_sync",  # スプレッドシート連携・自動募集
            "cogs.ai_chat"       # AIチャット機能 (/ask)
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"Loaded extension: {ext}")
            except Exception as e:
                print(f"Failed to load extension {ext}: {e}")
        
        # 3. Viewの永続化登録
        # これを行うことで、Bot再起動後も既存メッセージのボタンが機能します
        self.add_view(TicketView())
        self.add_view(RoomControlView())
        
        # 4. コマンドツリーの同期
        # スラッシュコマンドをDiscord側に登録します
        await self.tree.sync()
        print("--- System Online: Commands synced & Views registered ---")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} guilds")

bot = MyBot()

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN environment variable is not set.")
        print("Please check your .env file or Railway variables.")
    else:
        bot.run(TOKEN)