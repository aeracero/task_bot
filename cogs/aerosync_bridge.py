"""
AeroSync Bridge Cog
===================
Bidirectional sync between task_bot (Discord) and AeroSync (Supabase).

Environment variables required:
  SUPABASE_URL          — e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  — service_role key (bypasses RLS)

Events consumed (dispatched by tickets.py / sheets_sync.py):
  event_created(message_id)
  ticket_joined(message_id, user_id)
  ticket_left(message_id, user_id)

Slash commands:
  /sync_aerosync       — manual full sync of all RECRUITING events
  /aerosync_status     — connection check
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
import datetime
import os

try:
    from supabase import create_client, Client as SupabaseClient
    _SUPABASE_OK = True
except ImportError:
    _SUPABASE_OK = False
    print("[AeroSync Bridge] supabase-py not installed — install with: pip install supabase>=2.0.0")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


class AeroSyncBridgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.supabase: "SupabaseClient | None" = None

        if _SUPABASE_OK and SUPABASE_URL and SUPABASE_KEY:
            try:
                self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                print("[AeroSync Bridge] Supabase client initialized.")
            except Exception as e:
                print(f"[AeroSync Bridge] Failed to init Supabase: {e}")
        else:
            if _SUPABASE_OK:
                print("[AeroSync Bridge] SUPABASE_URL / SUPABASE_SERVICE_KEY not set — bridge disabled.")

        self.auto_sync.start()

    def cog_unload(self):
        self.auto_sync.cancel()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _member_by_discord_id(self, discord_id: int) -> dict | None:
        """Look up an AeroSync member row by Discord user ID."""
        if not self.supabase:
            return None
        try:
            res = (
                self.supabase.table("members")
                .select("id, email, display_name, discord_id")
                .eq("discord_id", str(discord_id))
                .single()
                .execute()
            )
            return res.data
        except Exception:
            return None

    async def _resolve_assignees(self, discord_user_ids: list[int]) -> list[str]:
        """Convert a list of Discord user IDs → list of AeroSync member emails."""
        emails: list[str] = []
        for uid in discord_user_ids:
            member = await self._member_by_discord_id(uid)
            if member and member.get("email"):
                emails.append(member["email"])
        return emails

    async def push_task_to_aerosync(self, event: dict, participants: list[int]) -> str | None:
        """
        Upsert a bot event into Supabase tasks table.
        Returns the AeroSync task UUID on success, or None on failure.
        """
        if not self.supabase:
            return None

        existing_task_id: str | None = event.get("aerosync_task_id")
        assignee_emails = await self._resolve_assignees(participants)

        # Parse date string — event['date_str'] may be "2026/03/15 (未定)" or ISO-ish
        date_val: str | None = None
        raw_date = (event.get("date_str") or "").split(" ")[0]
        try:
            dt = datetime.datetime.strptime(raw_date, "%Y/%m/%d")
            date_val = dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

        payload = {
            "title": event.get("title", "無題"),
            "description": f"Discord bot経由 (message_id: {event['message_id']})",
            "status": "募集中" if event.get("status") == "RECRUITING" else event.get("status", "未着手"),
            "priority": "medium",
            "assignees": assignee_emails,
        }
        if date_val:
            payload["date"] = date_val
        if event.get("man_hours"):
            payload["man_hours"] = event["man_hours"]

        try:
            if existing_task_id:
                # UPDATE existing task
                res = (
                    self.supabase.table("tasks")
                    .update(payload)
                    .eq("id", existing_task_id)
                    .execute()
                )
                if res.data:
                    return existing_task_id
            else:
                # INSERT new task
                res = (
                    self.supabase.table("tasks")
                    .insert(payload)
                    .execute()
                )
                if res.data:
                    new_id: str = res.data[0]["id"]
                    await db.set_aerosync_task_id(event["message_id"], new_id)
                    return new_id
        except Exception as e:
            print(f"[AeroSync Bridge] push_task_to_aerosync error: {e}")
        return None

    async def push_availability(self, discord_user_id: int, date_str: str, status: str) -> bool:
        """
        Upsert attendance/availability to Supabase.
        date_str should be "YYYY-MM-DD"; status one of available/maybe/unavailable.
        """
        if not self.supabase:
            return False
        member = await self._member_by_discord_id(discord_user_id)
        if not member:
            return False

        # Parse date
        raw = date_str.split(" ")[0]
        for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
            try:
                date_val = datetime.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        else:
            return False

        try:
            res = self.supabase.table("availability").upsert(
                {
                    "user_id": member["id"],
                    "user_email": member["email"],
                    "date": date_val,
                    "status": status,
                    "note": "Discord bot経由",
                },
                upsert_options={"onConflict": "user_id,date"},
            ).execute()
            return bool(res.data)
        except Exception as e:
            print(f"[AeroSync Bridge] push_availability error: {e}")
            return False

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_event_created(self, message_id: int):
        """Called after a new recruitment event is posted (manual or auto)."""
        event, participants = await db.get_event_data(message_id)
        if not event:
            return
        task_id = await self.push_task_to_aerosync(event, participants)
        if task_id:
            print(f"[AeroSync Bridge] event_created → task {task_id}")

    @commands.Cog.listener()
    async def on_ticket_joined(self, message_id: int, user_id: int):
        """Called when a member joins an event."""
        event, participants = await db.get_event_data(message_id)
        if not event:
            return

        # Update task assignees in AeroSync
        await self.push_task_to_aerosync(event, participants)

        # Mark the member as "available" on the event date
        if event.get("date_str"):
            await self.push_availability(user_id, event["date_str"], "available")

        print(f"[AeroSync Bridge] ticket_joined user={user_id} message={message_id}")

    @commands.Cog.listener()
    async def on_ticket_left(self, message_id: int, user_id: int):
        """Called when a member cancels their participation."""
        event, participants = await db.get_event_data(message_id)
        if not event:
            return

        # Update task assignees (user already removed from DB before dispatch)
        await self.push_task_to_aerosync(event, participants)

        # Mark the member as "unavailable" on the event date
        if event.get("date_str"):
            await self.push_availability(user_id, event["date_str"], "unavailable")

        print(f"[AeroSync Bridge] ticket_left user={user_id} message={message_id}")

    # ------------------------------------------------------------------
    # Background sync loop
    # ------------------------------------------------------------------

    @tasks.loop(hours=1)
    async def auto_sync(self):
        """Hourly sync — pushes all RECRUITING events to AeroSync."""
        if not self.supabase:
            return
        try:
            import aiosqlite
            async with aiosqlite.connect(db.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT * FROM events WHERE status = 'RECRUITING'"
                ) as cursor:
                    events = [dict(r) for r in await cursor.fetchall()]

            for event in events:
                async with aiosqlite.connect(db.db_path) as conn:
                    conn.row_factory = aiosqlite.Row
                    async with conn.execute(
                        "SELECT user_id FROM participants WHERE event_message_id = ?",
                        (event["message_id"],),
                    ) as cursor:
                        participants = [r["user_id"] for r in await cursor.fetchall()]
                await self.push_task_to_aerosync(event, participants)
        except Exception as e:
            print(f"[AeroSync Bridge] auto_sync error: {e}")

    @auto_sync.before_loop
    async def before_auto_sync(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @app_commands.command(name="aerosync_status", description="AeroSync連携の接続状態を確認します")
    async def aerosync_status(self, interaction: discord.Interaction):
        if not _SUPABASE_OK:
            await interaction.response.send_message(
                "❌ `supabase` ライブラリが未インストールです。\n```\npip install supabase>=2.0.0\n```",
                ephemeral=True,
            )
            return
        if not self.supabase:
            await interaction.response.send_message(
                "❌ Supabase未接続。`SUPABASE_URL` と `SUPABASE_SERVICE_KEY` を環境変数に設定してください。",
                ephemeral=True,
            )
            return
        try:
            res = self.supabase.table("tasks").select("id").limit(1).execute()
            await interaction.response.send_message(
                f"✅ AeroSync (Supabase) に接続済みです！\nURL: `{SUPABASE_URL}`",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"⚠ 接続エラー: {e}", ephemeral=True)

    @app_commands.command(name="sync_aerosync", description="全募集中イベントをAeroSyncに手動同期します")
    async def sync_aerosync(self, interaction: discord.Interaction):
        if not self.supabase:
            await interaction.response.send_message("❌ Supabase未接続。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            import aiosqlite
            async with aiosqlite.connect(db.db_path) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute("SELECT * FROM events WHERE status = 'RECRUITING'") as cursor:
                    events = [dict(r) for r in await cursor.fetchall()]

            pushed = 0
            for event in events:
                async with aiosqlite.connect(db.db_path) as conn:
                    conn.row_factory = aiosqlite.Row
                    async with conn.execute(
                        "SELECT user_id FROM participants WHERE event_message_id = ?",
                        (event["message_id"],),
                    ) as cursor:
                        participants = [r["user_id"] for r in await cursor.fetchall()]
                task_id = await self.push_task_to_aerosync(event, participants)
                if task_id:
                    pushed += 1

            await interaction.followup.send(
                f"✅ 同期完了: {pushed}/{len(events)} 件のイベントをAeroSyncに送信しました。",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"⚠ 同期エラー: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AeroSyncBridgeCog(bot))
