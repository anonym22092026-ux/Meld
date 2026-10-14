import os
import re
import secrets
import sqlite3
import html
import uuid
from contextlib import contextmanager
from datetime import timedelta, timezone, datetime
from functools import wraps
from html.parser import HTMLParser
from pathlib import Path

import click
import bleach
from flask import Flask, abort, g, jsonify, render_template, request, send_file, session
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", DATA_DIR / "notes.sqlite3"))
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", DATA_DIR / "uploads"))
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
ALLOWED_CONTENT_TAGS = {
    "a", "blockquote", "br", "caption", "div", "em", "h2", "h3", "li",
    "ol", "p", "s", "strong", "table", "tbody", "td", "th", "thead",
    "tr", "u", "ul",
}
IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
}
VIDEO_SIGNATURES = (
    (lambda header: len(header) >= 12 and header[4:8] == b"ftyp", "video/mp4", ".mp4"),
    (lambda header: header.startswith(b"\x1a\x45\xdf\xa3"), "video/webm", ".webm"),
)
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_MEDIA_PER_NOTE = 20
MAX_MEDIA_BYTES_PER_NOTE = 100 * 1024 * 1024


def load_secret_key():
    configured_key = os.environ.get("SECRET_KEY")
    if configured_key:
        return configured_key

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_path = DATA_DIR / ".secret-key"
    try:
        with key_path.open("x", encoding="utf-8") as key_file:
            key_file.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return key_path.read_text(encoding="utf-8").strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    DATABASE_PATH=str(DATABASE_PATH),
    MEDIA_DIR=str(MEDIA_DIR),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=MAX_VIDEO_BYTES + 1024 * 1024,
)


def connect_db():
    connection = sqlite3.connect(app.config["DATABASE_PATH"], timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def database():
    connection = connect_db()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    database_path = Path(app.config["DATABASE_PATH"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member'
                    CHECK (role IN ('admin', 'member')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                content_format TEXT NOT NULL DEFAULT 'text'
                    CHECK (content_format IN ('text', 'html')),
                author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                visibility TEXT NOT NULL DEFAULT 'public'
                    CHECK (visibility IN ('public', 'private')),
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS note_media (
                id TEXT PRIMARY KEY,
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                original_name TEXT NOT NULL,
                media_type TEXT NOT NULL CHECK (media_type IN ('image', 'video')),
                mime_type TEXT NOT NULL,
                file_path TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_one_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                user_two_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (user_one_id, user_two_id),
                CHECK (user_one_id < user_two_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                sender_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            );
            CREATE INDEX IF NOT EXISTS messages_conversation_id_id
                ON messages (conversation_id, id);
            """
        )
        note_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(notes)")
        }
        if "content_format" not in note_columns:
            connection.execute(
                """
                ALTER TABLE notes
                ADD COLUMN content_format TEXT NOT NULL DEFAULT 'text'
                    CHECK (content_format IN ('text', 'html'))
                """
            )
        if "visibility" not in note_columns:
            connection.execute(
                """
                ALTER TABLE notes
                ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'
                    CHECK (visibility IN ('public', 'private'))
                """
            )
    Path(app.config["MEDIA_DIR"]).mkdir(parents=True, exist_ok=True)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    with database() as connection:
        row = connection.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if row is None:
        session.clear()
        return None
    return dict(row)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def sanitize_note_content(content):
    return bleach.clean(
        content,
        tags=ALLOWED_CONTENT_TAGS,
        attributes={"a": ["href", "title"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def plain_note_content(content):
    extractor = _TextExtractor()
    extractor.feed(content)
    return " ".join(" ".join(extractor.parts).split())


def note_media_list(connection, note_id):
    rows = connection.execute(
        """
        SELECT id, original_name, media_type, mime_type, size_bytes, created_at
        FROM note_media WHERE note_id = ? ORDER BY created_at, id
        """,
        (note_id,),
    ).fetchall()
    return [
        {
            **dict(row),
            "url": f"/api/media/{row['id']}",
        }
        for row in rows
    ]


def can_access_note(connection, note_id, user_id):
    return connection.execute(
        """
        SELECT 1 FROM notes
        WHERE id = ? AND (visibility = 'public' OR author_id = ?)
        """,
        (note_id, user_id),
    ).fetchone() is not None


def serialize_note(connection, row):
    note = dict(row)
    if note.get("content_format") == "text":
        note["content"] = html.escape(note["content"]).replace("\n", "<br>")
    else:
        note["content"] = sanitize_note_content(note["content"])
    note["content_text"] = plain_note_content(note["content"])
    note["media"] = note_media_list(connection, note["id"])
    return note


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="Die Datei ist zu groß. Bilder dürfen höchstens 15 MB und Videos höchstens 50 MB groß sein."), 413


def delete_media_files(paths):
    media_dir = Path(app.config["MEDIA_DIR"]).resolve()
    for filename in paths:
        path = (media_dir / filename).resolve()
        if path.parent == media_dir:
            path.unlink(missing_ok=True)


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify(error="Bitte melde dich an."), 401
        g.user = user
        return view(*args, **kwargs)

    return wrapped


def require_admin(view):
    @wraps(view)
    @require_login
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            return jsonify(error="Dafür brauchst du Admin-Rechte."), 403
        return view(*args, **kwargs)

    return wrapped


@app.before_request
def protect_state_changes():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        return jsonify(error="Sitzung abgelaufen. Bitte lade die Seite neu."), 400
    return None


def request_data():
    if not request.is_json:
        return None
    payload = request.get_json()
    return payload if isinstance(payload, dict) else None


@app.get("/")
def index():
    return render_template("messenger.html")


@app.get("/notes")
def notes_workspace():
    return render_template("index.html")


@app.get("/healthz")
def health_check():
    return jsonify(status="ok")


@app.get("/api/session")
def get_session():
    user = current_user()
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(
        user=user,
        csrf_token=session["csrf_token"],
    )


@app.post("/api/register")
def register():
    payload = request_data()
    if payload is None:
        return jsonify(error="Ungültige Anfrage."), 400
    username = str(payload.get("username", "")).strip()
    password = payload.get("password", "")
    if not USERNAME_PATTERN.fullmatch(username):
        return jsonify(error="Der Benutzername muss 3–32 Zeichen lang sein und darf Buchstaben, Zahlen, Punkt, _ oder - enthalten."), 400
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        return jsonify(error="Das Passwort muss zwischen 12 und 128 Zeichen lang sein."), 400

    try:
        with database() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), timestamp()),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="Dieser Benutzername ist bereits vergeben."), 409

    session.clear()
    session["user_id"] = user_id
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return jsonify(user={"id": user_id, "username": username, "role": "member"}), 201


@app.post("/api/login")
def login():
    payload = request_data()
    if payload is None:
        return jsonify(error="Ungültige Anfrage."), 400
    username = str(payload.get("username", "")).strip()
    password = payload.get("password", "")
    with database() as connection:
        user = connection.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if user is None or not isinstance(password, str) or not check_password_hash(
        user["password_hash"], password
    ):
        return jsonify(error="Benutzername oder Passwort ist falsch."), 401

    session.clear()
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return jsonify(user={"id": user["id"], "username": user["username"], "role": user["role"]})


@app.post("/api/logout")
@require_login
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/contacts")
@require_login
def list_contacts():
    search = request.args.get("q", "").strip()
    with database() as connection:
        rows = connection.execute(
            """
            SELECT id, username FROM users
            WHERE id != ? AND username LIKE ?
            ORDER BY username COLLATE NOCASE LIMIT 100
            """,
            (g.user["id"], f"%{search}%"),
        ).fetchall()
    return jsonify(contacts=[dict(row) for row in rows])


@app.get("/api/conversations")
@require_login
def list_conversations():
    with database() as connection:
        rows = connection.execute(
            """
            SELECT conversations.id, conversations.updated_at,
                   users.id AS contact_id, users.username AS contact_username,
                   (SELECT messages.content FROM messages
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.id DESC LIMIT 1) AS last_message,
                   (SELECT messages.created_at FROM messages
                    WHERE messages.conversation_id = conversations.id
                    ORDER BY messages.id DESC LIMIT 1) AS last_message_at,
                   (SELECT COUNT(*) FROM messages
                    WHERE messages.conversation_id = conversations.id
                      AND messages.sender_id != ?
                      AND messages.read_at IS NULL) AS unread_count
            FROM conversations
            JOIN users ON users.id = CASE
                WHEN conversations.user_one_id = ? THEN conversations.user_two_id
                ELSE conversations.user_one_id
            END
            WHERE conversations.user_one_id = ? OR conversations.user_two_id = ?
            ORDER BY conversations.updated_at DESC, conversations.id DESC
            """,
            (g.user["id"], g.user["id"], g.user["id"], g.user["id"]),
        ).fetchall()
    return jsonify(conversations=[dict(row) for row in rows])


@app.post("/api/conversations")
@require_login
def create_conversation():
    payload = request_data()
    contact_id = payload.get("user_id") if payload else None
    if isinstance(contact_id, bool) or not isinstance(contact_id, int):
        return jsonify(error="Wähle einen gültigen Kontakt."), 400
    if contact_id == g.user["id"]:
        return jsonify(error="Du kannst keinen Chat mit dir selbst starten."), 400

    user_one_id, user_two_id = sorted((g.user["id"], contact_id))
    with database() as connection:
        contact = connection.execute(
            "SELECT id, username FROM users WHERE id = ?", (contact_id,)
        ).fetchone()
        if contact is None:
            return jsonify(error="Dieses Konto gibt es nicht."), 404
        connection.execute(
            """
            INSERT OR IGNORE INTO conversations
                (user_one_id, user_two_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_one_id, user_two_id, timestamp(), timestamp()),
        )
        conversation = connection.execute(
            """
            SELECT id FROM conversations
            WHERE user_one_id = ? AND user_two_id = ?
            """,
            (user_one_id, user_two_id),
        ).fetchone()
    return jsonify(
        conversation={"id": conversation["id"], "contact": dict(contact)}
    ), 201


@app.get("/api/conversations/<int:conversation_id>/messages")
@require_login
def list_messages(conversation_id):
    after = request.args.get("after", "0")
    if not after.isdecimal():
        return jsonify(error="Ungültige Nachrichten-ID."), 400
    with database() as connection:
        conversation = connection.execute(
            """
            SELECT 1 FROM conversations
            WHERE id = ? AND (user_one_id = ? OR user_two_id = ?)
            """,
            (conversation_id, g.user["id"], g.user["id"]),
        ).fetchone()
        if conversation is None:
            return jsonify(error="Diesen Chat gibt es nicht."), 404
        rows = connection.execute(
            """
            SELECT messages.id, messages.sender_id, messages.content,
                   messages.created_at, messages.read_at,
                   users.username AS sender_username
            FROM messages LEFT JOIN users ON users.id = messages.sender_id
            WHERE messages.conversation_id = ? AND messages.id > ?
            ORDER BY messages.id
            """,
            (conversation_id, int(after)),
        ).fetchall()
    return jsonify(messages=[dict(row) for row in rows])


@app.post("/api/conversations/<int:conversation_id>/read")
@require_login
def mark_conversation_read(conversation_id):
    with database() as connection:
        conversation = connection.execute(
            """
            SELECT 1 FROM conversations
            WHERE id = ? AND (user_one_id = ? OR user_two_id = ?)
            """,
            (conversation_id, g.user["id"], g.user["id"]),
        ).fetchone()
        if conversation is None:
            return jsonify(error="Diesen Chat gibt es nicht."), 404
        connection.execute(
            """
            UPDATE messages SET read_at = ?
            WHERE conversation_id = ? AND sender_id != ? AND read_at IS NULL
            """,
            (timestamp(), conversation_id, g.user["id"]),
        )
    return jsonify(ok=True)


@app.post("/api/conversations/<int:conversation_id>/messages")
@require_login
def create_message(conversation_id):
    payload = request_data()
    content = payload.get("content") if payload else None
    if not isinstance(content, str) or not 1 <= len(content.strip()) <= 4000:
        return jsonify(error="Eine Nachricht muss zwischen 1 und 4.000 Zeichen lang sein."), 400
    content = content.strip()
    now = timestamp()
    with database() as connection:
        conversation = connection.execute(
            """
            SELECT 1 FROM conversations
            WHERE id = ? AND (user_one_id = ? OR user_two_id = ?)
            """,
            (conversation_id, g.user["id"], g.user["id"]),
        ).fetchone()
        if conversation is None:
            return jsonify(error="Diesen Chat gibt es nicht."), 404
        cursor = connection.execute(
            """
            INSERT INTO messages (conversation_id, sender_id, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, g.user["id"], content, now),
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        message_id = cursor.lastrowid
    return jsonify(
        message={
            "id": message_id,
            "sender_id": g.user["id"],
            "sender_username": g.user["username"],
            "content": content,
            "created_at": now,
            "read_at": None,
        }
    ), 201


@app.get("/api/notes")
@require_login
def list_notes():
    search = request.args.get("q", "").strip()
    with database() as connection:
        rows = connection.execute(
            """
            SELECT notes.id, notes.title, notes.content, notes.content_format, notes.author_id,
                   notes.visibility, notes.version, notes.created_at, notes.updated_at,
                   users.username AS author
            FROM notes LEFT JOIN users ON users.id = notes.author_id
            WHERE (notes.visibility = 'public' OR notes.author_id = ?)
              AND (notes.title LIKE ? OR notes.content LIKE ?)
            ORDER BY notes.updated_at DESC, notes.id DESC
            """,
            (g.user["id"], f"%{search}%", f"%{search}%"),
        ).fetchall()
        notes = [serialize_note(connection, row) for row in rows]
    return jsonify(notes=notes)


@app.post("/api/notes")
@require_login
def create_note():
    payload = request_data()
    if payload is None:
        return jsonify(error="Ungültige Anfrage."), 400
    title = payload.get("title", "")
    content = payload.get("content", "")
    visibility = payload.get("visibility", "public")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        return jsonify(error="Der Titel muss zwischen 1 und 160 Zeichen lang sein."), 400
    if not isinstance(content, str) or len(content) > 20000:
        return jsonify(error="Der Inhalt darf höchstens 20.000 Zeichen lang sein."), 400
    content = sanitize_note_content(content)
    if not isinstance(visibility, str) or visibility not in {"public", "private"}:
        return jsonify(error="Wähle „öffentlich“ oder „privat“."), 400
    now = timestamp()
    with database() as connection:
        cursor = connection.execute(
            """
            INSERT INTO notes
                (title, content, content_format, author_id, visibility, created_at, updated_at)
            VALUES (?, ?, 'html', ?, ?, ?, ?)
            """,
            (title.strip(), content, g.user["id"], visibility, now, now),
        )
        note_id = cursor.lastrowid
        note = connection.execute(
            """
            SELECT notes.id, notes.title, notes.content, notes.content_format, notes.author_id,
                   notes.visibility, notes.version, notes.created_at, notes.updated_at,
                   users.username AS author
            FROM notes LEFT JOIN users ON users.id = notes.author_id
            WHERE notes.id = ?
            """,
            (note_id,),
        ).fetchone()
        serialized_note = serialize_note(connection, note)
    return jsonify(note=serialized_note), 201


@app.put("/api/notes/<int:note_id>")
@require_login
def update_note(note_id):
    payload = request_data()
    if payload is None:
        return jsonify(error="Ungültige Anfrage."), 400
    title = payload.get("title", "")
    content = payload.get("content", "")
    requested_visibility = payload.get("visibility")
    version = payload.get("version")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 160:
        return jsonify(error="Der Titel muss zwischen 1 und 160 Zeichen lang sein."), 400
    if not isinstance(content, str) or len(content) > 20000:
        return jsonify(error="Der Inhalt darf höchstens 20.000 Zeichen lang sein."), 400
    content = sanitize_note_content(content)
    if not isinstance(version, int) or isinstance(version, bool):
        return jsonify(error="Ungültige Notizversion."), 400
    if requested_visibility is not None and (
        not isinstance(requested_visibility, str)
        or requested_visibility not in {"public", "private"}
    ):
        return jsonify(error="Wähle „öffentlich“ oder „privat“."), 400

    with database() as connection:
        existing_note = connection.execute(
            "SELECT author_id, visibility FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if existing_note is None or (
            existing_note["visibility"] == "private"
            and existing_note["author_id"] != g.user["id"]
        ):
            return jsonify(error="Diese Notiz gibt es nicht."), 404
        visibility = requested_visibility or existing_note["visibility"]
        if (
            visibility != existing_note["visibility"]
            and existing_note["author_id"] != g.user["id"]
        ):
            return jsonify(error="Nur die erstellende Person kann die Sichtbarkeit ändern."), 403

        cursor = connection.execute(
            """
            UPDATE notes
            SET title = ?, content = ?, content_format = 'html', visibility = ?,
                version = version + 1, updated_at = ?
            WHERE id = ? AND version = ?
              AND ((visibility = 'public' AND ? = 'public') OR author_id = ?)
            """,
            (
                title.strip(),
                content,
                visibility,
                timestamp(),
                note_id,
                version,
                visibility,
                g.user["id"],
            ),
        )
        if cursor.rowcount == 0:
            exists = connection.execute(
                "SELECT author_id, visibility FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
            if exists is None or (
                exists["visibility"] == "private"
                and exists["author_id"] != g.user["id"]
            ):
                return jsonify(error="Diese Notiz gibt es nicht mehr."), 404
            return jsonify(error="Diese Notiz wurde zwischenzeitlich geändert. Lade sie neu, bevor du weiterarbeitest."), 409
        note = connection.execute(
            """
            SELECT notes.id, notes.title, notes.content, notes.content_format, notes.author_id,
                   notes.visibility, notes.version, notes.created_at, notes.updated_at,
                   users.username AS author
            FROM notes LEFT JOIN users ON users.id = notes.author_id
            WHERE notes.id = ?
            """,
            (note_id,),
        ).fetchone()
        serialized_note = serialize_note(connection, note)
    return jsonify(note=serialized_note)


@app.get("/api/notes/<int:note_id>/media")
@require_login
def list_note_media(note_id):
    with database() as connection:
        if not can_access_note(connection, note_id, g.user["id"]):
            return jsonify(error="Diese Notiz gibt es nicht."), 404
        media = note_media_list(connection, note_id)
    return jsonify(media=media)


@app.post("/api/notes/<int:note_id>/media")
@require_login
def upload_note_media(note_id):
    uploaded_file = request.files.get("file")
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify(error="Wähle zuerst eine Bild- oder Videodatei aus."), 400
    with database() as connection:
        if not can_access_note(connection, note_id, g.user["id"]):
            return jsonify(error="Diese Notiz gibt es nicht."), 404
        existing_media = connection.execute(
            """
            SELECT COUNT(*) AS item_count, COALESCE(SUM(size_bytes), 0) AS total_bytes
            FROM note_media WHERE note_id = ?
            """,
            (note_id,),
        ).fetchone()
    if existing_media["item_count"] >= MAX_MEDIA_PER_NOTE:
        return jsonify(error=f"Pro Notiz sind höchstens {MAX_MEDIA_PER_NOTE} Anhänge erlaubt."), 413
    available_note_bytes = MAX_MEDIA_BYTES_PER_NOTE - existing_media["total_bytes"]
    if available_note_bytes <= 0:
        return jsonify(error="Diese Notiz hat bereits das maximale Medienvolumen erreicht."), 413

    original_name = secure_filename(Path(uploaded_file.filename).name)[:180]
    if not original_name:
        return jsonify(error="Der Dateiname ist ungültig."), 400
    header = uploaded_file.stream.read(32)
    uploaded_file.stream.seek(0)

    try:
        image = Image.open(uploaded_file.stream)
        image_format = image.format
        width, height = image.size
        image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        image_format = None
        width = height = 0
    uploaded_file.stream.seek(0)

    if image_format in IMAGE_FORMATS:
        media_type = "image"
        mime_type, extension = IMAGE_FORMATS[image_format]
        max_size = MAX_IMAGE_BYTES
        if width * height > 40_000_000:
            return jsonify(error="Das Bild hat eine zu hohe Auflösung."), 400
    else:
        video_signature = next(
            ((mime, suffix) for matches, mime, suffix in VIDEO_SIGNATURES if matches(header)),
            None,
        )
        if video_signature is None:
            return jsonify(error="Erlaubt sind JPEG-, PNG-, GIF- und WebP-Bilder sowie MP4- und WebM-Videos."), 400
        media_type = "video"
        mime_type, extension = video_signature
        max_size = MAX_VIDEO_BYTES
    max_size = min(max_size, available_note_bytes)

    media_dir = Path(app.config["MEDIA_DIR"])
    media_dir.mkdir(parents=True, exist_ok=True)
    storage_name = f"{uuid.uuid4().hex}{extension}"
    storage_path = media_dir / storage_name
    size_bytes = 0
    try:
        with storage_path.open("xb") as destination:
            while chunk := uploaded_file.stream.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > max_size:
                    destination.close()
                    storage_path.unlink(missing_ok=True)
                    return jsonify(
                        error=f"Die Datei ist zu groß. Maximum: {max_size // (1024 * 1024)} MB."
                    ), 413
                destination.write(chunk)
        if size_bytes == 0:
            storage_path.unlink(missing_ok=True)
            return jsonify(error="Die Datei ist leer."), 400

        media_id = uuid.uuid4().hex
        with database() as connection:
            if not can_access_note(connection, note_id, g.user["id"]):
                storage_path.unlink(missing_ok=True)
                return jsonify(error="Diese Notiz gibt es nicht."), 404
            current_media = connection.execute(
                """
                SELECT COUNT(*) AS item_count, COALESCE(SUM(size_bytes), 0) AS total_bytes
                FROM note_media WHERE note_id = ?
                """,
                (note_id,),
            ).fetchone()
            if (
                current_media["item_count"] >= MAX_MEDIA_PER_NOTE
                or current_media["total_bytes"] + size_bytes > MAX_MEDIA_BYTES_PER_NOTE
            ):
                storage_path.unlink(missing_ok=True)
                return jsonify(error="Das maximale Medienvolumen dieser Notiz wurde erreicht."), 413
            connection.execute(
                """
                INSERT INTO note_media
                    (id, note_id, original_name, media_type, mime_type, file_path, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    media_id,
                    note_id,
                    original_name,
                    media_type,
                    mime_type,
                    storage_name,
                    size_bytes,
                    timestamp(),
                ),
            )
            media = connection.execute(
                """
                SELECT id, original_name, media_type, mime_type, size_bytes, created_at
                FROM note_media WHERE id = ?
                """,
                (media_id,),
            ).fetchone()
    except Exception:
        storage_path.unlink(missing_ok=True)
        raise

    return jsonify(media={**dict(media), "url": f"/api/media/{media_id}"}), 201


@app.get("/api/media/<media_id>")
@require_login
def get_media(media_id):
    with database() as connection:
        media = connection.execute(
            """
            SELECT note_media.file_path, note_media.mime_type, note_media.original_name
            FROM note_media
            JOIN notes ON notes.id = note_media.note_id
            WHERE note_media.id = ?
              AND (notes.visibility = 'public' OR notes.author_id = ?)
            """,
            (media_id, g.user["id"]),
        ).fetchone()
    if media is None:
        abort(404)
    media_path = (Path(app.config["MEDIA_DIR"]) / media["file_path"]).resolve()
    if media_path.parent != Path(app.config["MEDIA_DIR"]).resolve() or not media_path.is_file():
        abort(404)
    response = send_file(
        media_path,
        mimetype=media["mime_type"],
        as_attachment=False,
        download_name=media["original_name"],
        conditional=True,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.delete("/api/media/<media_id>")
@require_login
def delete_media(media_id):
    with database() as connection:
        media = connection.execute(
            """
            SELECT note_media.file_path
            FROM note_media
            JOIN notes ON notes.id = note_media.note_id
            WHERE note_media.id = ?
              AND (notes.visibility = 'public' OR notes.author_id = ?)
            """,
            (media_id, g.user["id"]),
        ).fetchone()
        if media is None:
            return jsonify(error="Dieser Anhang ist nicht verfügbar."), 404
        connection.execute("DELETE FROM note_media WHERE id = ?", (media_id,))
        filename = media["file_path"]
    delete_media_files([filename])
    return jsonify(ok=True)


@app.delete("/api/notes/<int:note_id>")
@require_login
def delete_note(note_id):
    with database() as connection:
        if not can_access_note(connection, note_id, g.user["id"]):
            return jsonify(error="Diese Notiz gibt es nicht."), 404
        media_paths = [
            row["file_path"]
            for row in connection.execute(
                "SELECT file_path FROM note_media WHERE note_id = ?", (note_id,)
            ).fetchall()
        ]
        cursor = connection.execute(
            """
            DELETE FROM notes
            WHERE id = ? AND (visibility = 'public' OR author_id = ?)
            """,
            (note_id, g.user["id"]),
        )
    if cursor.rowcount == 0:
        return jsonify(error="Diese Notiz gibt es nicht."), 404
    delete_media_files(media_paths)
    return jsonify(ok=True)


@app.get("/api/users")
@require_admin
def list_users():
    with database() as connection:
        rows = connection.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
    return jsonify(users=[dict(row) for row in rows])


@app.patch("/api/users/<int:user_id>")
@require_admin
def update_user_role(user_id):
    payload = request_data()
    role = payload.get("role") if payload else None
    if role not in {"admin", "member"}:
        return jsonify(error="Ungültige Rolle."), 400
    if user_id == g.user["id"]:
        return jsonify(error="Deine eigene Rolle kannst du hier nicht ändern."), 400
    with database() as connection:
        target = connection.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            return jsonify(error="Dieses Konto gibt es nicht."), 404
        if target["role"] == "admin" and role == "member":
            admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin'"
            ).fetchone()[0]
            if admins <= 1:
                return jsonify(error="Das letzte Admin-Konto kann nicht herabgestuft werden."), 400
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    return jsonify(ok=True)


@app.delete("/api/users/<int:user_id>")
@require_admin
def delete_user(user_id):
    if user_id == g.user["id"]:
        return jsonify(error="Du kannst dein eigenes Konto hier nicht löschen."), 400
    with database() as connection:
        target = connection.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            return jsonify(error="Dieses Konto gibt es nicht."), 404
        if target["role"] == "admin":
            admins = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin'"
            ).fetchone()[0]
            if admins <= 1:
                return jsonify(error="Das letzte Admin-Konto kann nicht gelöscht werden."), 400
        private_media = connection.execute(
            """
            SELECT note_media.file_path
            FROM note_media
            JOIN notes ON notes.id = note_media.note_id
            WHERE notes.author_id = ? AND notes.visibility = 'private'
            """,
            (user_id,),
        ).fetchall()
        connection.execute(
            "DELETE FROM notes WHERE author_id = ? AND visibility = 'private'",
            (user_id,),
        )
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    delete_media_files([row["file_path"] for row in private_media])
    return jsonify(ok=True)


@app.cli.command("create-admin")
def create_admin():
    """Create an administrator account or promote an existing account."""
    username = click.prompt("Benutzername")
    if not USERNAME_PATTERN.fullmatch(username):
        raise click.ClickException("Ungültiger Benutzername (3–32 Zeichen: Buchstaben, Zahlen, Punkt, _ oder -).")
    password = click.prompt("Passwort", hide_input=True, confirmation_prompt=True)
    if not 12 <= len(password) <= 128:
        raise click.ClickException("Das Passwort muss zwischen 12 und 128 Zeichen lang sein.")
    with database() as connection:
        existing = connection.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            connection.execute(
                "UPDATE users SET role = 'admin', password_hash = ? WHERE id = ?",
                (generate_password_hash(password), existing["id"]),
            )
            click.echo("Konto wurde zum Admin hochgestuft.")
        else:
            connection.execute(
                """
                INSERT INTO users (username, password_hash, role, created_at)
                VALUES (?, ?, 'admin', ?)
                """,
                (username, generate_password_hash(password), timestamp()),
            )
            click.echo("Admin-Konto wurde erstellt.")


initialize_database()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
