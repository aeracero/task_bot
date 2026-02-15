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

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print("--- System Online ---")

bot = MyBot()

# --- コマンド整理用コマンド (!cleanup) ---
@bot.command()
@commands.is_owner()
async def cleanup(ctx):
    await ctx.send("🔄 コマンド重複を解消中...\n1. グローバルコマンドを削除します...")
    
    # 1. グローバルコマンド（全体用）を削除
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync(guild=None)
    
    await ctx.send("2. 現在のサーバー用にコマンドを再登録します...")
    
    # 2. 現在のサーバー（Guild）用にコマンドをコピーして即時登録
    bot.tree.copy_global_to(guild=ctx.guild)
    await bot.tree.sync(guild=ctx.guild)
    
    await ctx.send("✅ 完了しました！\nDiscordアプリを再読み込み(Ctrl+R / Cmd+R)すると重複が消えます。")

# --- 手動同期コマンド (!sync) ---
@bot.command()
@commands.is_owner()
async def sync(ctx):
    await ctx.send("コマンドを同期中...")
    bot.tree.copy_global_to(guild=ctx.guild)
    await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"✅ 同期完了！")

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN is not set.")
    else:
        bot.run(TOKEN)
