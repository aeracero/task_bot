import aiosqlite
import os

DB_PATH = os.getenv("DB_PATH", "./data/bot.db")

class Database:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.db_path = DB_PATH

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    message_id INTEGER PRIMARY KEY,
                    channel_id INTEGER,
                    guild_id INTEGER,
                    owner_id INTEGER,
                    title TEXT,
                    date_str TEXT,
                    location TEXT,
                    required_num INTEGER,
                    status TEXT DEFAULT 'RECRUITING',
                    start_timestamp REAL,
                    notification_sent INTEGER DEFAULT 0,
                    man_hours REAL DEFAULT 0,
                    allow_overfill INTEGER DEFAULT 0,
                    sheet_row_index INTEGER DEFAULT -1,
                    report_status TEXT DEFAULT 'NONE'
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_message_id INTEGER,
                    user_id INTEGER,
                    FOREIGN KEY(event_message_id) REFERENCES events(message_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    notify_minutes INTEGER DEFAULT 15,
                    recruit_channel_id INTEGER,
                    sheet_id TEXT
                )
            """)
            
            event_columns = [
                ("man_hours", "REAL DEFAULT 0"),
                ("allow_overfill", "INTEGER DEFAULT 0"),
                ("sheet_row_index", "INTEGER DEFAULT -1"),
                ("report_status", "TEXT DEFAULT 'NONE'"),
                ("aerosync_task_id", "TEXT DEFAULT NULL"),  # AeroSync Supabase UUID
            ]
            for col_name, col_def in event_columns:
                try:
                    await db.execute(f"ALTER TABLE events ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass
            
            setting_columns = [
                ("recruit_channel_id", "INTEGER"),
                ("sheet_id", "TEXT")
            ]
            for col_name, col_def in setting_columns:
                try:
                    await db.execute(f"ALTER TABLE guild_settings ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass

            await db.commit()

    async def create_event(self, message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num, start_timestamp=None, man_hours=0, sheet_row_index=-1):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO events (message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num, start_timestamp, notification_sent, man_hours, sheet_row_index, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'RECRUITING')
            """, (message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num, start_timestamp, man_hours, sheet_row_index))
            await db.commit()

    async def add_participant(self, message_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id FROM participants WHERE event_message_id = ? AND user_id = ?", (message_id, user_id)) as cursor:
                if await cursor.fetchone():
                    return False
            await db.execute("INSERT INTO participants (event_message_id, user_id) VALUES (?, ?)", (message_id, user_id))
            await db.commit()
            return True

    async def remove_participant(self, message_id, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM participants WHERE event_message_id = ? AND user_id = ?", (message_id, user_id))
            await db.commit()

    async def get_event_data(self, message_id):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM events WHERE message_id = ?", (message_id,)) as cursor:
                event = await cursor.fetchone()
                if not event: return None, []
            
            async with db.execute("SELECT user_id FROM participants WHERE event_message_id = ?", (message_id,)) as cursor:
                rows = await cursor.fetchall()
                participants = [row['user_id'] for row in rows]
            return dict(event), participants

    async def set_aerosync_task_id(self, message_id, task_id: str):
        """AeroSync (Supabase) の task UUID を保存"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE events SET aerosync_task_id = ? WHERE message_id = ?",
                (task_id, message_id)
            )
            await db.commit()

    async def delete_event(self, message_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
            await db.execute("DELETE FROM participants WHERE event_message_id = ?", (message_id,))
            await db.commit()

    async def update_event_flags(self, message_id, allow_overfill=None, report_status=None):
        async with aiosqlite.connect(self.db_path) as db:
            if allow_overfill is not None:
                await db.execute("UPDATE events SET allow_overfill = ? WHERE message_id = ?", (allow_overfill, message_id))
            if report_status is not None:
                await db.execute("UPDATE events SET report_status = ? WHERE message_id = ?", (report_status, message_id))
            await db.commit()
            
    async def set_start_timestamp(self, message_id, timestamp):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE events SET start_timestamp = ? WHERE message_id = ? AND start_timestamp IS NULL", (timestamp, message_id))
            await db.commit()

    async def get_upcoming_events(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM events WHERE start_timestamp IS NOT NULL AND notification_sent = 0") as cursor:
                return [dict(row) for row in await cursor.fetchall()]
    
    async def get_events_needing_report(self, current_timestamp):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM events 
                WHERE start_timestamp IS NOT NULL 
                AND start_timestamp < ? 
                AND (report_status IS NULL OR report_status = 'NONE')
            """, (current_timestamp,)) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def mark_notification_sent(self, message_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE events SET notification_sent = 1 WHERE message_id = ?", (message_id,))
            await db.commit()

    async def set_guild_notify_time(self, guild_id, minutes):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO guild_settings (guild_id, notify_minutes) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET notify_minutes = excluded.notify_minutes
            """, (guild_id, minutes))
            await db.commit()

    async def set_recruit_channel(self, guild_id, channel_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO guild_settings (guild_id, recruit_channel_id) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET recruit_channel_id = excluded.recruit_channel_id
            """, (guild_id, channel_id))
            await db.commit()
            
    async def set_guild_sheet(self, guild_id, sheet_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO guild_settings (guild_id, sheet_id) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET sheet_id = excluded.sheet_id
            """, (guild_id, sheet_id))
            await db.commit()

    async def get_guild_settings(self, guild_id):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else {
                    "notify_minutes": 15, 
                    "recruit_channel_id": None, 
                    "sheet_id": None
                }
                
    async def get_all_guild_settings(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM guild_settings WHERE sheet_id IS NOT NULL") as cursor:
                return [dict(row) for row in await cursor.fetchall()]

db = Database()
