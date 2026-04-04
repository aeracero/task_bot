import discord
from discord import app_commands
from discord.ext import commands

class RoomControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="削除 (解散)", style=discord.ButtonStyle.danger, emoji="💥", custom_id="room:delete")
    async def delete_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("3秒後に爆破します...", ephemeral=True)
        await interaction.channel.delete()

    @discord.ui.button(label="ロック/解除", style=discord.ButtonStyle.secondary, emoji="🔒", custom_id="room:lock")
    async def lock_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.channel
        if vc.user_limit == 0:
            current_members = len(vc.members)
            await vc.edit(user_limit=current_members)
            await interaction.response.send_message(f"部屋をロックしました（定員: {current_members}人）。", ephemeral=True)
        else:
            await vc.edit(user_limit=0)
            await interaction.response.send_message("部屋のロックを解除しました。", ephemeral=True)

class RoomsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="temp_vc", description="使い捨て会議室(VC)を作成します")
    @app_commands.describe(name="会議室名")
    async def temp_vc(self, interaction: discord.Interaction, name: str = "緊急会議室"):
        guild = interaction.guild
        category = interaction.channel.category

        try:
            vc = await guild.create_voice_channel(name=f"🔊 {name}", category=category)
        except Exception as e:
            await interaction.response.send_message(f"作成に失敗しました: {e}", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🛠 会議室コントロール",
            description="このチャンネルは使い捨てです。用が済んだら削除ボタンを押してください。",
            color=discord.Color.blue()
        )
        await vc.send(embed=embed, view=RoomControlView())

        await interaction.response.send_message(f"{vc.mention} を作成しました。移動してください。", ephemeral=True)

async def setup(bot):
    await bot.add_cog(RoomsCog(bot))
