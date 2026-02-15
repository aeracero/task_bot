import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
import gspread
import google.generativeai as genai
import datetime
import os
import json

GEMINI_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-3-flash-preview')

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
        if GEMINI_KEY:
            try:
                response = model.generate_content(f"以下の作業報告をスプレッドシートの1セルに収まるように簡潔に要約してください。文体は「である調」で統一すること:\n{raw_text}")
                summary = response.text.strip()
            except Exception as e:
                print(f"Gemini Error: {e}")

        await db.update_event_flags(self.message_id, report_status="REPORTED")

        cog = interaction.client.get_cog("SheetsSyncCog")
        if cog:
            # 指定されたシートIDを使って書き込み
            await cog.update_sheet_cell_by_id(self.sheet_id, self.row_index, 10, summary)

        await interaction.followup.send(f"✅ 報告を受け付けました！\nAI要約: {summary}", ephemeral=True)


class SheetsSyncCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.gc = None
        
        # --- Google認証 (ファイル または 環境変数) ---
        try:
            # 1. 環境変数 GOOGLE_CREDENTIALS_JSON があればそれを使う (Railway/Heroku等推奨)
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if creds_json:
                creds_dict = json.loads(creds_json)
                self.gc = gspread.service_account_from_dict(creds_dict)
                print("Loaded credentials from environment variable.")
            
            # 2. なければローカルの credentials.json を探す
            elif os.path.exists("credentials.json"):
                self.gc = gspread.service_account(filename="credentials.json")
                print("Loaded credentials from credentials.json file.")
            
            else:
                print("Warning: 'credentials.json' not found and 'GOOGLE_CREDENTIALS_JSON' env var not set.")
                print("Sheet features will be disabled.")
                
        except Exception as e:
            print(f"Sheets Init Error: {e}")

        self.sync_tasks_loop.start()
        self.progress_check_loop.start()

    def cog_unload(self):
        self.sync_tasks_loop.cancel()
        self.progress_check_loop.cancel()

    # --- シート操作ヘルパー ---
    
    async def create_new_sheet(self, title):
        """新しいシートを作成し、URLとIDを返す"""
        if not self.gc: raise Exception("Google API Client not initialized")
        
        def _create():
            # シート作成
            sh = self.gc.create(title)
            # 権限設定: リンクを知っている全員が編集可能 (簡易セットアップのため)
            sh.share(None, perm_type='anyone', role='writer')
            
            # ヘッダー書き込み
            worksheet = sh.sheet1
            headers = ["ID", "タスク名", "必要人数", "工数(人時)", "週", "フェーズ", "ステータス", "担当者", "実行日", "進捗報告"]
            worksheet.append_row(headers)
            return sh.url, sh.id

        return await self.bot.loop.run_in_executor(None, _create)

    async def test_access(self, sheet_id):
        """アクセス可能かテスト"""
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

    # --- メインループ: 全サーバーのタスク同期 ---
    @tasks.loop(hours=24) # 毎日実行
    # @tasks.loop(minutes=5) # デバッグ用
    async def sync_tasks_loop(self):
        await self.bot.wait_until_ready()
        if not self.gc: return

        print("--- Starting Sheet Sync ---")
        
        # シート設定がある全ギルドを取得
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
                if len(rows) < 2: continue # ヘッダーのみの場合はスキップ

                channel = self.bot.get_channel(channel_id)
                if not channel: continue
                guild = channel.guild

                # データ解析
                for i, row in enumerate(rows[1:]):
                    row_idx = i + 2 # 1始まり+ヘッダー
                    
                    # 列不足チェック
                    if len(row) < 7: continue

                    task_name = row[1]
                    status = row[6]
                    assigned_week = row[4]

                    # 募集条件: 「未着手」 かつ 「今の週以前」
                    if status == "未着手" and assigned_week and assigned_week.isdigit() and int(assigned_week) <= current_week:
                        
                        # Gemini 要約
                        description = f"作業内容: {task_name}\nフェーズ: {row[5]}"
                        if GEMINI_KEY:
                            try:
                                prompt = f"Discord募集用: 以下のタスクをやる気にさせる短い文章で紹介して。絵文字多めで。\n{description}"
                                res = model.generate_content(prompt)
                                flavor_text = res.text.strip()
                            except:
                                flavor_text = "協力してタスクを完了させましょう！"
                        else:
                            flavor_text = "メンバー募集中！"

                        # 緊急度色分け
                        color = discord.Color.red() if is_weekend else discord.Color.blue()
                        title_prefix = "🔥緊急募集🔥 " if is_weekend else "📋 "

                        # 数値パース
                        try:
                            req_num = int(row[2])
                            man_hours = float(row[3])
                        except:
                            req_num = 1; man_hours = 0

                        embed = discord.Embed(title=f"{title_prefix}{task_name}", description=flavor_text, color=color)
                        embed.add_field(name="工数", value=f"{man_hours}人時", inline=True)
                        embed.add_field(name="募集人数", value=f"{req_num}人", inline=True)
                        
                        # View作成
                        from cogs.tickets import TicketView
                        view = TicketView()
                        
                        msg = await channel.send(embed=embed, view=view)

                        # DB登録
                        await db.create_event(
                            message_id=msg.id,
                            channel_id=channel.id,
                            guild_id=guild_id,
                            owner_id=self.bot.user.id,
                            title=task_name,
                            date_str=today.strftime("%Y/%m/%d"),
                            location="Discord/未定",
                            required_num=req_num,
                            man_hours=man_hours,
                            sheet_row_index=row_idx,
                            start_timestamp=today.timestamp()
                        )

                        # シート更新: 「未着手」->「募集中」
                        await self.update_sheet_cell_by_id(sheet_id, row_idx, 7, "募集中")

            except Exception as e:
                print(f"Sync Error in guild {guild_id}: {e}")

    # --- 進捗管理ループ ---
    @tasks.loop(minutes=10)
    async def progress_check_loop(self):
        await self.bot.wait_until_ready()
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        
        try:
            # 報告が必要なイベントを取得
            targets = await db.get_events_needing_report(now)
            
            for event in targets:
                # 参加者がいない、または工数未設定の場合はスキップ
                _, participants = await db.get_event_data(event['message_id'])
                if not participants or event['man_hours'] <= 0: continue

                # 終了予定時刻 = 開始 + (総工数 / 人数)
                duration_sec = (event['man_hours'] / len(participants)) * 3600
                end_timestamp = event['start_timestamp'] + duration_sec
                
                # まだ終了時刻になっていない
                if now < end_timestamp: continue

                # 終了時刻を過ぎている -> 催促
                guild = self.bot.get_guild(event['guild_id'])
                if not guild: continue
                
                # シートIDを取得
                settings = await db.get_guild_settings(event['guild_id'])
                sheet_id = settings['sheet_id']

                for uid in participants:
                    member = guild.get_member(uid)
                    if member:
                        view = discord.ui.View()
                        # ここでモーダルを開くためのボタンを送信
                        # 引数に sheet_id を渡す必要があるため、専用のButtonクラスを作るか、lambdaでラップする
                        # buttonのcallbackでmodalを出すのが定石
                        
                        btn = ReportButton(event['message_id'], event['sheet_row_index'], sheet_id)
                        view.add_item(btn)
                        
                        try:
                            await member.send(
                                f"🤖 **進捗確認**\n案件「{event['title']}」の作業予定時間が終了しました。\n状況を報告してください。",
                                view=view
                            )
                        except:
                            pass
                
                # 何度も送らないようにDB側で一時フラグを立てる等の処理が必要だが、
                # 今回は単純化のため、ReportModal送信まで保留 (実運用では 'WAITING_REPORT' ステータスを作ると良い)
                
        except Exception as e:
            print(f"Progress Check Error: {e}")

    # イベント成立時の書き込みリスナー
    @commands.Cog.listener()
    async def on_event_full(self, message_id, participants):
        event, _ = await db.get_event_data(message_id)
        if not event or event['sheet_row_index'] == -1: return

        # シートID取得
        settings = await db.get_guild_settings(event['guild_id'])
        sheet_id = settings['sheet_id']
        if not sheet_id: return

        row_idx = event['sheet_row_index']
        
        guild = self.bot.get_guild(event['guild_id'])
        names = []
        for uid in participants:
            m = guild.get_member(uid)
            names.append(m.display_name if m else str(uid))
        
        names_str = ", ".join(names)
        
        # H列(担当者), I列(実行日時)
        await self.update_sheet_cell_by_id(sheet_id, row_idx, 8, names_str)
        await self.update_sheet_cell_by_id(sheet_id, row_idx, 9, event['date_str'])

# DMでボタンを押したときの処理用クラス
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
