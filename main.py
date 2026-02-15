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
        # Intents(権限)の設定
        intents = discord.Intents.default()
        intents.message_content = True 
        intents.members = True         
        
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Bot起動時の初期化処理"""
        print("--- Initializing ---")

        # 1. データベースの初期化
        await db.init_db()
        
        # 2. Cogs(機能モジュール)のロード
        extensions = [
            "cogs.tickets",
            "cogs.rooms",
            "cogs.settings",
            "cogs.sheets_sync",
            "cogs.ai_chat"
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"Loaded extension: {ext}")
            except Exception as e:
                print(f"Failed to load extension {ext}: {e}")
        
        # 3. Viewの永続化登録
        self.add_view(TicketView())
        self.add_view(RoomControlView())
        print("--- Views Registered ---")

    async def on_ready(self):
        """Bot起動完了時の処理 (ここでコマンドを強制同期します)"""
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connected to {len(self.guilds)} guilds")

        # --- コマンド自動同期処理 ---
        print("Starting automatic command sync...")
        
        # 参加している全てのサーバー(Guild)に対して、コマンドを即時登録します
        # これにより、最大1時間の待ち時間なしでコマンドが表示されます
        for guild in self.guilds:
            try:
                print(f"Syncing commands to guild: {guild.name} (ID: {guild.id})...")
                # グローバルコマンドの定義をこのサーバー用にコピー
                self.tree.copy_global_to(guild=guild)
                # 同期実行
                await self.tree.sync(guild=guild)
                print(f"✅ Synced to {guild.name}")
            except Exception as e:
                print(f"❌ Failed to sync to {guild.name}: {e}")

        print("--- System Online: All commands synced! ---")

bot = MyBot()

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN is not set.")
    else:
        bot.run(TOKEN)
