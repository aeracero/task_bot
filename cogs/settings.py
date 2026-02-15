import discord
from discord import app_commands
from discord.ext import commands
from database import db
import re

# シートID抽出用の正規表現
SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- 独立したコマンド群 ---

    @app_commands.command(name="set_notification", description="募集イベントの事前通知時間を設定します")
    @app_commands.describe(minutes="何分前に通知するか (例: 15)")
    async def set_notification(self, interaction: discord.Interaction, minutes: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
            return

        if minutes < 1:
            await interaction.response.send_message("1分以上の時間を指定してください。", ephemeral=True)
            return

        await db.set_guild_notify_time(interaction.guild_id, minutes)
        await interaction.response.send_message(f"✅ 通知時間を **{minutes}分前** に設定しました。", ephemeral=True)

    @app_commands.command(name="set_channel", description="自動募集を行うチャンネルを設定します")
    @app_commands.describe(channel="募集メッセージを送信するチャンネル")
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
            return
        
        await db.set_recruit_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"✅ 自動募集チャンネルを {channel.mention} に設定しました。", ephemeral=True)

    @app_commands.command(name="connect_sheet", description="連携するスプレッドシートを設定または新規作成します")
    @app_commands.describe(url="既存のシートURL (空欄の場合、新規作成します)")
    async def connect_sheet(self, interaction: discord.Interaction, url: str = None):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
            return

        # 1. 新規作成モード (URLなし)
        if not url:
            await interaction.response.defer(ephemeral=True)
            
            # SheetsSyncCogの機能を使って作成
            sync_cog = interaction.client.get_cog("SheetsSyncCog")
            if not sync_cog:
                await interaction.followup.send("❌ エラー: スプレッドシート連携機能がロードされていません。", ephemeral=True)
                return

            try:
                sheet_url, sheet_id = await sync_cog.create_new_sheet(f"TaskBot_Schedule_{interaction.guild.name}")
                await db.set_guild_sheet(interaction.guild_id, sheet_id)
                
                msg = (
                    f"✅ **新しいスプレッドシートを作成しました！**\n\n"
                    f"以下のURLからアクセスして、タスクを入力してください。\n"
                    f"URL: {sheet_url}\n\n"
                    f"※ 権限: リンクを知っている全員が編集可能\n"
                    f"※ Botが自動的に読み込みます。"
                )
                await interaction.followup.send(msg, ephemeral=True)
                
            except Exception as e:
                await interaction.followup.send(f"❌ シート作成に失敗しました: {e}", ephemeral=True)
            
            return

        # 2. 既存リンク登録モード (URLあり)
        match = SHEET_ID_PATTERN.search(url)
        if not match:
            await interaction.response.send_message("❌ 無効なGoogleスプレッドシートのURLです。", ephemeral=True)
            return
        
        sheet_id = match.group(1)
        
        # アクセステスト
        sync_cog = interaction.client.get_cog("SheetsSyncCog")
        if sync_cog:
            try:
                await sync_cog.test_access(sheet_id)
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Botがそのシートにアクセスできません。\n"
                    f"共有設定でBotのメールアドレス (credentials.json) を編集者に追加してください。\nERROR: {e}", 
                    ephemeral=True
                )
                return

        await db.set_guild_sheet(interaction.guild_id, sheet_id)
        await interaction.response.send_message(f"✅ スプレッドシートを連携しました。\nID: `{sheet_id}`", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))