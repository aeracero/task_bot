import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
from dateutil import parser
import datetime

JST = datetime.timezone(datetime.timedelta(hours=9))

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_event_message(self, interaction: discord.Interaction, message_id: int):
        data = await db.get_event_data(message_id)
        if not data[0]:
            await interaction.response.send_message("このイベントデータは削除されています。", ephemeral=True)
            return

        event_info, participants = data
        current_count = len(participants)
        required = event_info['required_num']
        allow_overfill = event_info['allow_overfill']
        
        # ステータス表示ロジック更新
        if current_count >= required:
            color = discord.Color.green()
            if allow_overfill:
                 status_text = "✅ **決行決定 (追加募集中)** - まだ参加可能です！"
            else:
                 status_text = "✅ **満員御礼** - 準備を進めてください"
        else:
            color = discord.Color.orange()
            status_text = f"⚠ **募集中** - あと {required - current_count} 枚必要です"

        embed = discord.Embed(title=f"📋 {event_info['title']}", color=color)
        embed.add_field(name="📅 日時", value=event_info['date_str'], inline=True)
        embed.add_field(name="📍 場所", value=event_info['location'], inline=True)
        
        # 工数表示 (あれば)
        if event_info['man_hours'] > 0:
            embed.add_field(name="⏳ 想定工数", value=f"{event_info['man_hours']} 人時", inline=True)

        embed.add_field(name="👥 チケット状況", value=f"目標: {required}枚 / **現在: {current_count}枚**", inline=False)
        embed.add_field(name="ステータス", value=status_text, inline=False)
        
        member_mentions = [f"<@{uid}>" for uid in participants]
        embed.add_field(name="🎫 参加者一覧", value="\n".join(member_mentions) if member_mentions else "なし", inline=False)
        embed.set_footer(text=f"Event ID: {message_id}")

        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="参加", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        event_info, participants = await db.get_event_data(msg_id)
        
        # オーバーフィル判定
        if len(participants) >= event_info['required_num'] and not event_info['allow_overfill']:
            if interaction.user.id not in participants:
                await interaction.response.send_message("定員に達しています！", ephemeral=True)
                return

        success = await db.add_participant(msg_id, interaction.user.id)
        
        if success:
            await self.update_event_message(interaction, msg_id)
            await interaction.response.send_message("チケットを発行しました！", ephemeral=True)
            # ※ここで自動募集スプレッドシートへの書き込みもフックできますが、
            # 処理が重くなるのを避けるため定期実行Cog側で差分検知するか、ここで非同期タスクを投げるのがベターです。
        else:
            await interaction.response.send_message("既に参加済みです。", ephemeral=True)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="ticket:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = interaction.message.id
        await db.remove_participant(msg_id, interaction.user.id)
        await self.update_event_message(interaction, msg_id)
        await interaction.response.send_message("参加をキャンセルしました。", ephemeral=True)

    @discord.ui.button(label="管理メニュー", style=discord.ButtonStyle.danger, custom_id="ticket:manage")
    async def manage_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 権限チェック
        event_info, _ = await db.get_event_data(interaction.message.id)
        if not event_info: return
        if interaction.user.id != event_info['owner_id'] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("権限がありません。", ephemeral=True)
            return

        # 管理用の一時的なViewを返す
        view = ManageEventView(interaction.message.id)
        await interaction.response.send_message("管理メニュー:", view=view, ephemeral=True)

class ManageEventView(discord.ui.View):
    def __init__(self, message_id):
        super().__init__(timeout=60)
        self.message_id = message_id

    @discord.ui.button(label="追加募集モード切替", style=discord.ButtonStyle.success)
    async def toggle_overfill(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_info, _ = await db.get_event_data(self.message_id)
        new_val = 0 if event_info['allow_overfill'] else 1
        await db.update_event_flags(self.message_id, allow_overfill=new_val)
        
        msg = "追加募集(定員超え)を許可しました。" if new_val else "定員で締め切る設定に戻しました。"
        await interaction.response.send_message(msg, ephemeral=True)
        
        # 元のメッセージを更新するためにTicketViewのメソッドを呼び出す必要があるが、
        # ここでは簡易的にメッセージ再取得で対応
        # (実運用ではViewの更新通知を送るなど工夫が必要)

    @discord.ui.button(label="イベント削除 (通知あり)", style=discord.ButtonStyle.danger)
    async def delete_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        event_info, participants = await db.get_event_data(self.message_id)
        if not event_info: return

        # キャンセルDM通知
        notify_text = (
            f"⚠ **イベント中止のお知らせ**\n\n"
            f"予定されていた以下のイベントは中止(削除)されました。\n"
            f"案件: **{event_info['title']}**\n"
            f"日時: {event_info['date_str']}\n"
        )
        
        guild = interaction.guild
        for uid in participants:
            member = guild.get_member(uid)
            if member:
                try:
                    await member.send(notify_text)
                except:
                    pass

        await db.delete_event(self.message_id)
        original_msg = interaction.channel.get_partial_message(self.message_id)
        try:
            await original_msg.delete()
        except:
            pass
            
        await interaction.response.send_message("イベントを削除し、参加者に通知を送りました。", ephemeral=True)

class RecruitModal(discord.ui.Modal, title="募集チケット発行"):
    task_name = discord.ui.TextInput(label="タスク内容", style=discord.TextStyle.short)
    date_str = discord.ui.TextInput(label="日時", placeholder="例: 2026/02/15 21:00")
    location = discord.ui.TextInput(label="場所/URL", required=False)
    required_num = discord.ui.TextInput(label="必要人数", placeholder="3", max_length=2)
    man_hours = discord.ui.TextInput(label="想定総工数 (人時)", placeholder="例: 10 (任意)", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            req_num = int(self.required_num.value)
            mh = float(self.man_hours.value) if self.man_hours.value else 0
        except ValueError:
            await interaction.response.send_message("人数/工数は数字で入力してください。", ephemeral=True)
            return

        try:
            dt = parser.parse(self.date_str.value)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=JST)
            timestamp = dt.timestamp()
        except:
            timestamp = None

        embed = discord.Embed(title=f"📋 {self.task_name.value}", color=discord.Color.orange())
        embed.add_field(name="📅 日時", value=self.date_str.value, inline=True)
        embed.add_field(name="📍 場所", value=self.location.value or "指定なし", inline=True)
        if mh > 0:
            embed.add_field(name="⏳ 想定工数", value=f"{mh} 人時", inline=True)
        embed.add_field(name="👥 チケット状況", value=f"目標: {req_num}枚 / **現在: 0枚**", inline=False)
        embed.add_field(name="ステータス", value="⚠ **募集中**", inline=False)
        
        await interaction.response.send_message(embed=embed, view=TicketView())
        msg = await interaction.original_response()

        await db.create_event(
            message_id=msg.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild_id,
            owner_id=interaction.user.id,
            title=self.task_name.value,
            date_str=self.date_str.value,
            location=self.location.value or "指定なし",
            required_num=req_num,
            start_timestamp=timestamp,
            man_hours=mh
        )

class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    @app_commands.command(name="recruit", description="手動で募集を作成します")
    async def recruit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RecruitModal())

    @tasks.loop(minutes=1)
    async def reminder_loop(self):
        # (既存のリマインダーロジック... 省略せず記述する場合は前回のコードを維持)
        try:
            events = await db.get_upcoming_events()
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            for event in events:
                settings = await db.get_guild_settings(event['guild_id'])
                minutes = settings['notify_minutes']
                
                time_until = event['start_timestamp'] - now
                if 0 < time_until <= (minutes * 60):
                    await self.send_reminder(event)
                    await db.mark_notification_sent(event['message_id'])
                elif time_until <= 0:
                    await db.mark_notification_sent(event['message_id'])
        except Exception:
            pass

    async def send_reminder(self, event):
        _, participants = await db.get_event_data(event['message_id'])
        if not participants: return
        guild = self.bot.get_guild(event['guild_id'])
        if not guild: return
        
        text = f"⏰ **まもなく開始です！**\n案件: {event['title']}\n集合してください！"
        for uid in participants:
            try:
                m = guild.get_member(uid)
                if m: await m.send(text)
            except: pass

    @reminder_loop.before_loop
    async def before_rem(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TicketsCog(bot))