import discord
from discord import app_commands
from discord.ext import commands
from database import db

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # グループ化: /settings notification ... という形で使えるようになります
    settings_group = app_commands.Group(name="settings", description="Botの設定を変更します")

    @settings_group.command(name="notification", description="募集イベントの事前通知時間を設定します")
    @app_commands.describe(minutes="何分前に通知するか (例: 15, 30, 60)")
    async def set_notification(self, interaction: discord.Interaction, minutes: int):
        # 権限チェック (管理者のみ)
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("このコマンドを実行するには管理者権限が必要です。", ephemeral=True)
            return

        if minutes < 1:
            await interaction.response.send_message("1分以上の時間を指定してください。", ephemeral=True)
            return

        await db.set_guild_notify_time(interaction.guild_id, minutes)
        await interaction.response.send_message(f"✅ 設定を保存しました。\n今後、イベント開始の **{minutes}分前** に参加者へ通知を送ります。", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))