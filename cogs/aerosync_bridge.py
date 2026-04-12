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
  /sync_aerosync           — manual full sync of all RECRUITING events
  /aerosync_status         — connection check
  /set_aerosync_channel    — configure which channel receives sync notifications
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import db
import datetime
import os
import aiosqlite

try:
    from supabase import create_client, Client as SupabaseClient
    _SUPABASE_OK = True
except ImportError:
    _SUPABASE_OK = False
    print("[AeroSync Bridge] supabase-py not installed — install with: pip install supabase>=2.0.0")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

EMOJI_STATUS = {"✅": "available", "🟡": "maybe", "❌": "unavailable"}


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
        self.auto_poll_sync.start()

    def cog_unload(self):
        self.auto_sync.cancel()
        self.auto_poll_sync.cancel()

    # ------------------------------------------------------------------
    # Notification helper
    # ------------------------------------------------------------------

    async def _get_notify_channel(self, guild_id: int) -> discord.TextChannel | None:
        """Returns the recruitment channel for a guild (used for notifications)."""
        try:
            settings = await db.get_guild_settings(guild_id)
            channel_id = settings.get("recruit_channel_id")
            if channel_id:
                return self.bot.get_channel(channel_id)
        except Exception:
            pass
        return None

    async def _notify(self, guild_id: int, embed: discord.Embed):
        """Post a notification embed to the guild's recruitment channel."""
        channel = await self._get_notify_channel(guild_id)
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"[AeroSync Bridge] notify error: {e}")

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
                res = (
                    self.supabase.table("tasks")
                    .update(payload)
                    .eq("id", existing_task_id)
                    .execute()
                )
                if res.data:
                    return existing_task_id
            else:
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
        """Upsert attendance/availability to Supabase."""
        if not self.supabase:
            return False
        member = await self._member_by_discord_id(discord_user_id)
        if not member:
            return False

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
            embed = discord.Embed(
                title="📋 AeroSync: タスク登録",
                description=f"**{event['title']}** がAeroSyncに登録されました。",
                color=discord.Color.blue(),
            )
            embed.add_field(name="日時", value=event.get("date_str", "未定"), inline=True)
            embed.add_field(name="募集人数", value=str(event.get("required_num", "?")), inline=True)
            embed.set_footer(text=f"Task ID: {task_id[:8]}…")
            await self._notify(event["guild_id"], embed)

    @commands.Cog.listener()
    async def on_ticket_joined(self, message_id: int, user_id: int):
        """Called when a member joins an event."""
        event, participants = await db.get_event_data(message_id)
        if not event:
            return

        await self.push_task_to_aerosync(event, participants)

        if event.get("date_str"):
            await self.push_availability(user_id, event["date_str"], "available")

        print(f"[AeroSync Bridge] ticket_joined user={user_id} message={message_id}")

        guild = self.bot.get_guild(event["guild_id"])
        display = f"<@{user_id}>"
        if guild:
            m = guild.get_member(user_id)
            if m:
                display = m.display_name

        embed = discord.Embed(
            title="✅ AeroSync: 参加登録",
            description=f"**{display}** が **{event['title']}** に参加しました。\nAeroSyncの担当者・出欠を更新しました。",
            color=discord.Color.green(),
        )
        embed.add_field(name="現在の参加者数", value=f"{len(participants)} / {event['required_num']}", inline=True)
        await self._notify(event["guild_id"], embed)

    @commands.Cog.listener()
    async def on_ticket_left(self, message_id: int, user_id: int):
        """Called when a member cancels their participation."""
        event, participants = await db.get_event_data(message_id)
        if not event:
            return

        await self.push_task_to_aerosync(event, participants)

        if event.get("date_str"):
            await self.push_availability(user_id, event["date_str"], "unavailable")

        print(f"[AeroSync Bridge] ticket_left user={user_id} message={message_id}")

        guild = self.bot.get_guild(event["guild_id"])
        display = f"<@{user_id}>"
        if guild:
            m = guild.get_member(user_id)
            if m:
                display = m.display_name

        embed = discord.Embed(
            title="↩️ AeroSync: 参加キャンセル",
            description=f"**{display}** が **{event['title']}** をキャンセルしました。\nAeroSyncの担当者・出欠を更新しました。",
            color=discord.Color.orange(),
        )
        embed.add_field(name="現在の参加者数", value=f"{len(participants)} / {event['required_num']}", inline=True)
        await self._notify(event["guild_id"], embed)

    # ------------------------------------------------------------------
    # Background: task sync loop (hourly)
    # ------------------------------------------------------------------

    @tasks.loop(hours=1)
    async def auto_sync(self):
        """Hourly sync — pushes all RECRUITING events to AeroSync."""
        if not self.supabase:
            return
        try:
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

            if pushed:
                print(f"[AeroSync Bridge] auto_sync: {pushed}/{len(events)} events synced.")
        except Exception as e:
            print(f"[AeroSync Bridge] auto_sync error: {e}")

    @auto_sync.before_loop
    async def before_auto_sync(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Background: Discord poll reaction sync (every 30 min)
    # ------------------------------------------------------------------

    @tasks.loop(minutes=30)
    async def auto_poll_sync(self):
        """
        Reads discord_polls from Supabase, fetches reactions for each poll message,
        and upserts availability. Skips polls synced in the last 25 minutes.
        """
        if not self.supabase:
            return
        try:
            cutoff = (
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=25)
            ).isoformat()

            res = (
                self.supabase.table("discord_polls")
                .select("*")
                .or_(f"last_synced_at.is.null,last_synced_at.lt.{cutoff}")
                .execute()
            )
            polls = res.data or []
            if not polls:
                return

            print(f"[AeroSync Bridge] auto_poll_sync: processing {len(polls)} poll(s).")

            for poll in polls:
                await self._sync_poll_reactions(poll)

        except Exception as e:
            print(f"[AeroSync Bridge] auto_poll_sync error: {e}")

    async def _sync_poll_reactions(self, poll: dict):
        """Sync reactions for a single poll record into Supabase availability."""
        channel_id = int(poll["channel_id"])
        message_id = int(poll["message_id"])
        date_str = str(poll["date"])  # "YYYY-MM-DD"

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        except Exception as e:
            print(f"[AeroSync Bridge] fetch_message error: {e}")
            return

        # Build member_status: last emoji wins (available > maybe > unavailable)
        member_status: dict[int, str] = {}
        for reaction in message.reactions:
            emoji_str = str(reaction.emoji)
            status = EMOJI_STATUS.get(emoji_str)
            if not status:
                continue
            async for user in reaction.users():
                if user.bot:
                    continue
                member_status[user.id] = status

        synced = 0
        for discord_user_id, status in member_status.items():
            ok = await self.push_availability(discord_user_id, date_str, status)
            if ok:
                synced += 1

        # Update last_synced_at
        try:
            self.supabase.table("discord_polls").update(
                {"last_synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
            ).eq("message_id", poll["message_id"]).execute()
        except Exception:
            pass

        if synced:
            print(f"[AeroSync Bridge] poll {poll['message_id']} ({date_str}): {synced} member(s) synced.")

            # Notify the guild about the sync result
            guild_id = int(poll["guild_id"]) if poll.get("guild_id") else None
            if guild_id:
                embed = discord.Embed(
                    title="🔄 AeroSync: 出欠自動同期",
                    description=f"**{date_str}** の投票から {synced} 名の出欠をAeroSyncに同期しました。",
                    color=discord.Color.blurple(),
                )
                embed.set_footer(text="30分ごとに自動実行")
                await self._notify(guild_id, embed)

    @auto_poll_sync.before_loop
    async def before_auto_poll_sync(self):
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
            self.supabase.table("tasks").select("id").limit(1).execute()
            await interaction.response.send_message(
                f"✅ AeroSync (Supabase) に接続済み\nURL: `{SUPABASE_URL}`\n\n"
                "• タスク同期: 毎時\n"
                "• 投票出欠同期: 30分ごと",
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

    @app_commands.command(name="sync_polls", description="Discord投票の出欠をAeroSyncに今すぐ同期します")
    async def sync_polls(self, interaction: discord.Interaction):
        if not self.supabase:
            await interaction.response.send_message("❌ Supabase未接続。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            res = self.supabase.table("discord_polls").select("*").execute()
            polls = res.data or []
            if not polls:
                await interaction.followup.send("同期対象の投票がありません。AeroSyncから投票を送信してください。", ephemeral=True)
                return

            total_synced = 0
            for poll in polls:
                before = total_synced
                await self._sync_poll_reactions(poll)
                # We can't easily count here but at least we process all
            await interaction.followup.send(
                f"✅ {len(polls)} 件の投票を同期しました。",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"⚠ エラー: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AeroSyncBridgeCog(bot))
