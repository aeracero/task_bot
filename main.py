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
        intents.message_content = True # コマンド同期に必要
        intents.members = True         
        
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await db.init_db()
        
        # Cogsのロード
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
        
        # Viewの登録
        self.add_view(TicketView())
        self.add_view(RoomControlView())
        
        # 自動同期はあえてコメントアウトし、手動同期(!sync)に頼ることも可能です
        # await self.tree.sync() 

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print("--- System Online ---")

bot = MyBot()

# --- 強制同期コマンド (!sync) ---
# チャット欄で "!sync" と打つと、そのサーバーにコマンドを即時登録します
@bot.command()
@commands.is_owner() # Botの管理者(あなた)だけが使えます
async def sync(ctx):
    await ctx.send("コマンドを同期中...")
    # 現在のサーバー(Guild)にコマンドをコピーして登録
    bot.tree.copy_global_to(guild=ctx.guild)
    await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"✅ 同期完了！ `/set_...` などのコマンドを確認してください。")

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN is not set.")
    else:
        bot.run(TOKEN)
