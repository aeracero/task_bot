import discord
from discord.ext import commands
import os
from database import db
from cogs.tickets import TicketView
from cogs.rooms import RoomControlView

TOKEN = os.getenv("DISCORD_TOKEN")

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await db.init_db()
        
        # Cogsのロード (settingsを追加)
        await self.load_extension("cogs.tickets")
        await self.load_extension("cogs.rooms")
        await self.load_extension("cogs.settings") # <--- NEW
        
        self.add_view(TicketView())
        self.add_view(RoomControlView())
        
        await self.tree.sync()
        print("--- System Online: Commands synced & Views registered ---")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

bot = MyBot()

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN is not found.")
    else:
        bot.run(TOKEN)