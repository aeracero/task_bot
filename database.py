import aiosqlite
import os

# Railway Volumeのマウントパス
DB_PATH = os.getenv("DB_PATH", "./data/bot.db")

class Database:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.db_path = DB_PATH

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            # 1. イベントテーブル作成
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
                    notification_sent INTEGER DEFAULT 0
                )
            """)
            
            # 2. 参加者テーブル作成
            await db.execute("""
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_message_id INTEGER,
                    user_id INTEGER,
                    FOREIGN KEY(event_message_id) REFERENCES events(message_id)
                )
            """)

            # 3. サーバー設定テーブル作成 (NEW)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    notify_minutes INTEGER DEFAULT 15
                )
            """)
            
            # --- マイグレーション (既存DBへの列追加対応) ---
            try:
                await db.execute("ALTER TABLE events ADD COLUMN start_timestamp REAL")
            except Exception:
                pass # 既に存在する場合は無視
            
            try:
                await db.execute("ALTER TABLE events ADD COLUMN notification_sent INTEGER DEFAULT 0")
            except Exception:
                pass

            await db.commit()

    # --- イベント関連 ---
    async def create_event(self, message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num, start_timestamp=None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO events (message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num, start_timestamp, notification_sent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (message_id, channel_id, guild_id, owner_id, title, date_str, location, required_num, start_timestamp))
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
                if not event: return None
            
            async with db.execute("SELECT user_id FROM participants WHERE event_message_id = ?", (message_id,)) as cursor:
                rows = await cursor.fetchall()
                participants = [row['user_id'] for row in rows]
            return dict(event), participants

    async def delete_event(self, message_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM events WHERE message_id = ?", (message_id,))
            await db.execute("DELETE FROM participants WHERE event_message_id = ?", (message_id,))
            await db.commit()

    # --- リマインダー・設定関連 (NEW) ---
    async def get_upcoming_events(self):
        """通知未送信かつ、時間が設定されているイベントを取得"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # start_timestampが入っていて、notification_sentが0のもの
            async with db.execute("SELECT * FROM events WHERE start_timestamp IS NOT NULL AND notification_sent = 0") as cursor:
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

    async def get_guild_notify_time(self, guild_id):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT notify_minutes FROM guild_settings WHERE guild_id = ?", (guild_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 15  # デフォルト15分

db = Database()