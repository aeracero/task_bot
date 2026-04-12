import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
import gspread
from google import genai
import datetime
import os
import json

GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_KEY:
    client = genai.Client(api_key=GEMINI_KEY)
    MODEL_ID = 'gemini-3-flash-preview'
else:
    client = None

class ReportModal(discord.ui.Modal, title="作業進捗報告"):
    progress = discord.ui.TextInput(label="進捗状況 (詳細)", style=discord.TextStyle.paragraph, placeholder="例: リブ切り完了。やすりがけは30%進みました。")
    remaining = discord.ui.TextInput(label="残作業の有無", style=discord.TextStyle.short, placeholder="なし / あり (あと2時間くらい)")

    def __init__(self, message_id, row_index, sheet_id):
        super().__init__()
        self.message_id = message_id
        self.row_index = row_index
        self.sheet_id = sheet_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        raw_text = f"状況: {self.progress.value}\n残り: {self.remaining.value}"
        summary = raw_text
        if client:
            try:
                response = client.models.generate_content(
                    model=MODEL_ID,
                    contents=f"以下の作業報告をスプレッドシートの1セルに収まるように簡潔に要約してください。文体は「である調」で統一すること:\n{raw_text}"
                )
                summary = response.text.strip()
            except Exception as e:
                print(f"Gemini Error: {e}")

        await db.update_event_flags(self.message_id, report_status="REPORTED")

        cog = interaction.client.get_cog("SheetsSyncCog")
        if cog:
            await cog.update_sheet_cell_by_id(self.sheet_id, self.row_index, 10, summary)

        await interaction.followup.send(f"✅ 報告を受け付けました！\nAI要約: {summary}", ephemeral=True)

class SheetsSyncCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gc = None
        
        try:
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if creds_json:
                creds_dict = json.loads(creds_json)
                self.gc = gspread.service_account_from_dict(creds_dict)
            elif os.path.exists("credentials.json"):
                self.gc = gspread.service_account(filename="credentials.json")
        except Exception as e:
            print(f"Sheets Init Error: {e}")

        self.sync_tasks_loop.start()
        self.progress_check_loop.start()

    def cog_unload(self):
        self.sync_tasks_loop.cancel()
        self.progress_check_loop.cancel()

    async def create_new_sheet(self, title):
        if not self.gc: raise Exception("Google API Client not initialized")
        def _create():
            sh = self.gc.create(title)
            sh.share(None, perm_type='anyone', role='writer')
            worksheet = sh.sheet1
            headers = ["ID", "タスク名", "必要人数", "工数(人時)", "週", "フェーズ", "ステータス", "担当者", "実行日", "進捗報告"]
            worksheet.append_row(headers)
            return sh.url, sh.id
        return await self.bot.loop.run_in_executor(None, _create)

    async def test_access(self, sheet_id):
        if not self.gc: return
        await self.bot.loop.run_in_executor(None, lambda: self.gc.open_by_key(sheet_id))

    async def get_sheet_data_by_id(self, sheet_id):
        if not self.gc: return []
        def _get():
            sh = self.gc.open_by_key(sheet_id)
            return sh.sheet1.get_all_values()
        return await self.bot.loop.run_in_executor(None, _get)

    async def update_sheet_cell_by_id(self, sheet_id, row, col, value):
        if not self.gc: return
        def _update():
            sh = self.gc.open_by_key(sheet_id)
            sh.sheet1.update_cell(row, col, value)
        await self.bot.loop.run_in_executor(None, _update)

    @tasks.loop(hours=24)
    async def sync_tasks_loop(self):
        await self.bot.wait_until_ready()
        if not self.gc: return
        guild_settings_list = await db.get_all_guild_settings()
        today = datetime.datetime.now()
        current_week = today.isocalendar()[1]
        weekday = today.weekday()
        is_weekend = weekday >= 4 

        for setting in guild_settings_list:
            guild_id = setting['guild_id']
            sheet_id = setting['sheet_id']
            channel_id = setting['recruit_channel_id']
            if not sheet_id or not channel_id: continue
            try:
                rows = await self.get_sheet_data_by_id(sheet_id)
                if len(rows) < 2: continue
                channel = self.bot.get_channel(channel_id)
                if not channel: continue
                for i, row in enumerate(rows[1:]):
                    row_idx = i + 2
                    if len(row) < 7: continue
                    task_name = row[1]
                    status = row[6]
                    assigned_week = row[4]
                    if status == "未着手" and assigned_week and assigned_week.isdigit() and int(assigned_week) <= current_week:
                        description = f"作業内容: {task_name}\nフェーズ: {row[5]}"
                        flavor_text = "メンバー募集中！"
                        if client:
                            try:
                                res = client.models.generate_content(
                                    model=MODEL_ID,
                                    contents=f"Discord募集用: 以下のタスクをやる気にさせる短い文章で紹介して。絵文字多めで。\n{description}"
                                )
                                flavor_text = res.text.strip()
                            except: pass
                        color = discord.Color.red() if is_weekend else discord.Color.blue()
                        title_prefix = "🔥緊急募集🔥 " if is_weekend else "📋 "
                        try:
                            req_num = int(row[2])
                            man_hours = float(row[3])
                        except:
                            req_num = 1; man_hours = 0
                            
                        embed = discord.Embed(title=f"{title_prefix}{task_name}", description=flavor_text, color=color)
                        embed.add_field(name="工数", value=f"{man_hours}人時", inline=True)
                        embed.add_field(name="募集人数", value=f"{req_num}人", inline=True)
                        
                        from cogs.tickets import TicketView
                        view = TicketView()
                        msg = await channel.send(embed=embed, view=view)
                        
                        # 自動募集は時間を「未定 (None)」として作成し、参加時に時刻をセットする
                        await db.create_event(msg.id, channel.id, guild_id, self.bot.user.id, task_name, today.strftime("%Y/%m/%d") + " (未定)", "Discord/未定", req_num, None, man_hours, row_idx)
                        await self.update_sheet_cell_by_id(sheet_id, row_idx, 7, "募集中")
                        self.bot.dispatch("event_created", msg.id)  # → AeroSync Bridge
            except Exception as e:
                print(f"Sync Error: {e}")

    @tasks.loop(minutes=10)
    async def progress_check_loop(self):
        await self.bot.wait_until_ready()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        try:
            targets = await db.get_events_needing_report(now)
            for event in targets:
                _, participants = await db.get_event_data(event['message_id'])
                if not participants: continue
                
                # 工数が0の場合はデフォルトで24時間後に確認
                if event['man_hours'] > 0:
                    duration_sec = (event['man_hours'] / len(participants)) * 3600
                else:
                    duration_sec = 24 * 3600
                    
                end_timestamp = event['start_timestamp'] + duration_sec
                if now < end_timestamp: continue
                
                guild = self.bot.get_guild(event['guild_id'])
                if not guild: continue
                channel = guild.get_channel(event['channel_id'])
                if not channel: continue
                
                settings = await db.get_guild_settings(event['guild_id'])
                sheet_id = settings['sheet_id']
                
                view = discord.ui.View()
                btn = ReportButton(event['message_id'], event['sheet_row_index'], sheet_id)
                view.add_item(btn)
                
                mentions = " ".join([f"<@{uid}>" for uid in participants])
                try:
                    await channel.send(f"{mentions}\n🤖 **進捗確認**\n案件「**{event['title']}**」の作業予定時間を過ぎました。状況の報告をお願いします！", view=view)
                    # 無限ループを防止するため、フラグを更新
                    await db.update_event_flags(event['message_id'], report_status="WAITING")
                except Exception as e:
                    print(f"Progress Send Error: {e}")
        except Exception as e:
            print(f"Progress Check Error: {e}")

    @commands.Cog.listener()
    async def on_event_full(self, message_id, participants):
        event, _ = await db.get_event_data(message_id)
        if not event or event['sheet_row_index'] == -1: return
        settings = await db.get_guild_settings(event['guild_id'])
        sheet_id = settings['sheet_id']
        if not sheet_id: return
        guild = self.bot.get_guild(event['guild_id'])
        names = [guild.get_member(uid).display_name if guild.get_member(uid) else str(uid) for uid in participants]
        await self.update_sheet_cell_by_id(sheet_id, event['sheet_row_index'], 8, ", ".join(names))
        
        # 実行日は現在の日付を書き込む
        today_str = datetime.datetime.now().strftime("%Y/%m/%d")
        await self.update_sheet_cell_by_id(sheet_id, event['sheet_row_index'], 9, today_str)

class ReportButton(discord.ui.Button):
    def __init__(self, message_id, row_idx, sheet_id):
        super().__init__(label="進捗報告する", style=discord.ButtonStyle.success)
        self.msg_id = message_id
        self.row_idx = row_idx
        self.sheet_id = sheet_id
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ReportModal(self.msg_id, self.row_idx, self.sheet_id))

async def setup(bot):
    await bot.add_cog(SheetsSyncCog(bot))
