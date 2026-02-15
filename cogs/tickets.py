import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
from dateutil import parser
import datetime

# 日本時間 (JST) 定義
JST = datetime.timezone(datetime.timedelta(hours=9))

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_event_message(self, interaction: discord.Interaction, message_id: int):
        data = await db.get_event_data(message_id)
        if not data:
            await interaction.response.send_message("このイベントデータは既に削除されています。", ephemeral=True)
            return

        event_info, participants = data
        current_count = len(participants)
        required = event_info['required_num']
        
        if current_count >= required:
            color = discord.Color.green()
            status_text = "✅ **決行決定 (人員確保済)** - 準備を進めてください"
        else:
            color = discord.Color.orange()
            status_text = f"⚠ **募集中** - あと {required - current_count} 枚必要です"

        embed = discord.Embed(title=f"📋 {event_info['title']}", color=color)
        embed.add_field(name="📅 日時", value=event_info['date_str'], inline=True)
        embed.add_field(name="📍 場所", value=event_info['location'], inline=True)
        embed.add_field(name="👥 チケット状況", value=f"目標: {required}枚 / **現在: {current_count}枚**", inline=False)
        embed.add_field(name="ステータス", value=status_text, inline=False)
        
        member_mentions = [f"<@{uid}>" for uid in participants]
        embed.add_field(name="🎫 参加者一覧", value="\n".join(member_mentions) if member_mentions else "なし", inline=False)
        embed.set_footer(text=f"Event ID: {message_id}")

        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="チケットを取る (参加)", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        
        event_info, participants = await db.get_event_data(msg_id)
        if len(participants) >= event_info['required_num']:
            if interaction.user.id not in participants:
                await interaction.response.send_message("定員に達しています！", ephemeral=True)
                return

        success = await db.add_participant(msg_id, interaction.user.id)
        
        if success:
            await self.update_event_message(interaction, msg_id)
            await interaction.response.send_message("チケットを発行しました！", ephemeral=True)

            # DM通知ロジック (決行決定時)
            event_info, new_participants = await db.get_event_data(msg_id)
            if len(new_participants) == event_info['required_num']:
                notify_text = (
                    f"🎉 **決行決定のお知らせ**\n\n"
                    f"案件「**{event_info['title']}**」のメンバーが集まりました！\n"
                    f"日時: {event_info['date_str']}\n"
                    f"場所: {event_info['location']}\n\n"
                    f"作業の準備をお願いします！"
                )
                guild = interaction.guild
                for uid in new_participants:
                    member = guild.get_member(uid)
                    if member:
                        try:
                            await member.send(notify_text)
                        except discord.Forbidden:
                            pass
        else:
            await interaction.response.send_message("既にチケットを持っています。", ephemeral=True)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="ticket:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        await db.remove_participant(msg_id, interaction.user.id)
        await self.update_event_message(interaction, msg_id)
        await interaction.response.send_message("チケットを返却しました。", ephemeral=True)

    @discord.ui.button(label="管理者削除", style=discord.ButtonStyle.danger, custom_id="ticket:delete")
    async def delete_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_info, _ = await db.get_event_data(interaction.message.id)
        if not event_info:
            await interaction.message.delete()
            return
        if interaction.user.id != event_info['owner_id'] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("削除権限がありません。", ephemeral=True)
            return
        await db.delete_event(interaction.message.id)
        await interaction.message.delete()
        await interaction.response.send_message("募集を削除しました。", ephemeral=True)


class RecruitModal(discord.ui.Modal, title="タスク募集チケットの発行"):
    task_name = discord.ui.TextInput(label="タスク・作業内容", style=discord.TextStyle.short)
    date_str = discord.ui.TextInput(label="日時 (例: 2026/02/15 21:00)", placeholder="YYYY/MM/DD HH:MM の形式推奨")
    location = discord.ui.TextInput(label="場所・マップURL", placeholder="GoogleMap URLなど")
    required_num = discord.ui.TextInput(label="必要人数", placeholder="数字のみ (例: 3)", min_length=1, max_length=2)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            req_num = int(self.required_num.value)
        except ValueError:
            await interaction.response.send_message("人数は半角数字で入力してください。", ephemeral=True)
            return

        # 日付解析処理
        try:
            # 入力された文字列をJSTとして解釈し、Unixタイムスタンプ(UTC)に変換して保存
            dt = parser.parse(self.date_str.value)
            # タイムゾーン指定がない場合はJSTとみなす
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            timestamp = dt.timestamp()
        except Exception:
            # 解析失敗時はNone (リマインダー機能は無効化されるが募集は作れる)
            timestamp = None
            warning_msg = "\n⚠ 日時形式を認識できなかったため、リマインダー機能は無効です (募集は作成されます)。"
        else:
            warning_msg = ""

        embed = discord.Embed(title=f"📋 {self.task_name.value}", color=discord.Color.orange())
        embed.add_field(name="📅 日時", value=self.date_str.value, inline=True)
        embed.add_field(name="📍 場所", value=self.location.value, inline=True)
        embed.add_field(name="👥 チケット状況", value=f"目標: {req_num}枚 / **現在: 0枚**", inline=False)
        embed.add_field(name="ステータス", value="⚠ **募集中**", inline=False)
        embed.set_footer(text="Initializing...")

        await interaction.response.send_message(embed=embed, view=TicketView())
        msg = await interaction.original_response()
        
        if warning_msg:
             await interaction.followup.send(warning_msg, ephemeral=True)

        await db.create_event(
            message_id=msg.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild_id,
            owner_id=interaction.user.id,
            title=self.task_name.value,
            date_str=self.date_str.value,
            location=self.location.value,
            required_num=req_num,
            start_timestamp=timestamp
        )

class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminder_loop.start() # ループ開始

    def cog_unload(self):
        self.reminder_loop.cancel()

    @app_commands.command(name="recruit", description="作業・タスクの募集チケットを発行します")
    async def recruit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RecruitModal())

    # --- 1分ごとの監視ループ ---
    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        try:
            events = await db.get_upcoming_events()
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()

            for event in events:
                # サーバーごとの通知設定を取得
                minutes_before = await db.get_guild_notify_time(event['guild_id'])
                notify_threshold = minutes_before * 60 # 秒換算

                # 開始時間 - 今の時間 <= 設定時間 (例: 残り15分を切った)
                time_until_start = event['start_timestamp'] - now

                if 0 < time_until_start <= notify_threshold:
                    # 通知対象！
                    await self.send_reminder(event)
                    await db.mark_notification_sent(event['message_id'])
                
                # 既に過ぎてしまったイベントも通知済み扱いにしてDB負荷を下げる
                elif time_until_start <= 0:
                    await db.mark_notification_sent(event['message_id'])

        except Exception as e:
            print(f"Loop Error: {e}")

    async def send_reminder(self, event):
        # 参加者リスト取得
        _, participants = await db.get_event_data(event['message_id'])
        if not participants:
            return

        guild = self.bot.get_guild(event['guild_id'])
        if not guild: return

        # 通知テキスト
        text = (
            f"⏰ **まもなく開始です！**\n\n"
            f"案件: **{event['title']}**\n"
            f"時間: {event['date_str']}\n"
            f"場所: {event['location']}\n\n"
            f"集合をお願いします！"
        )

        for uid in participants:
            member = guild.get_member(uid)
            if member:
                try:
                    await member.send(text)
                except discord.Forbidden:
                    pass

    @reminder_loop.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TicketsCog(bot))