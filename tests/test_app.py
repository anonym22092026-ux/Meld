from io import BytesIO
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import app, initialize_database


class NotesAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "test.sqlite3")
        self.media_dir = str(Path(self.temp_dir.name) / "uploads")
        app.config.update(
            TESTING=True,
            DATABASE_PATH=self.database_path,
            MEDIA_DIR=self.media_dir,
            SECRET_KEY="test-secret",
        )
        initialize_database()
        self.client = app.test_client()
        self.csrf_token = self.client.get("/api/session").get_json()["csrf_token"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def post(self, path, payload):
        return self.client.post(
            path,
            json=payload,
            headers={"X-CSRF-Token": self.csrf_token},
        )

    def register(self, username):
        return self.post(
            "/api/register",
            {"username": username, "password": "a-long-test-password"},
        )

    def register_client(self, client, username):
        csrf_token = client.get("/api/session").get_json()["csrf_token"]
        response = client.post(
            "/api/register",
            json={"username": username, "password": "a-long-test-password"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 201)
        return client.get("/api/session").get_json()["csrf_token"]

    def create_note(self, client, csrf_token, visibility="public", content=""):
        response = client.post(
            "/api/notes",
            json={"title": "Mediennotiz", "content": content, "visibility": visibility},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["note"]

    def upload_png(self, client, csrf_token, note_id):
        image_bytes = BytesIO()
        Image.new("RGB", (2, 2), color="green").save(image_bytes, format="PNG")
        image_bytes.seek(0)
        return client.post(
            f"/api/notes/{note_id}/media",
            data={"file": (image_bytes, "test.png")},
            headers={"X-CSRF-Token": csrf_token},
            content_type="multipart/form-data",
        )

    def test_register_and_create_shared_note(self):
        response = self.register("mira")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["user"]["role"], "member")
        self.csrf_token = self.client.get("/api/session").get_json()["csrf_token"]

        response = self.post("/api/notes", {"title": "Idee", "content": "Zusammenarbeit"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["note"]["author"], "mira")

    def test_registration_validates_username_and_password(self):
        response = self.post("/api/register", {"username": "x", "password": "short"})
        self.assertEqual(response.status_code, 400)

    def test_private_notes_are_only_returned_to_their_creator(self):
        self.register("mira")
        owner_token = self.client.get("/api/session").get_json()["csrf_token"]
        created = self.client.post(
            "/api/notes",
            json={"title": "Privater Entwurf", "content": "Geheim", "visibility": "private"},
            headers={"X-CSRF-Token": owner_token},
        )
        self.assertEqual(created.status_code, 201)
        private_note = created.get_json()["note"]
        self.assertEqual(private_note["visibility"], "private")

        other_client = app.test_client()
        other_token = self.register_client(other_client, "jonas")
        other_notes = other_client.get("/api/notes").get_json()["notes"]
        self.assertNotIn(private_note["id"], [note["id"] for note in other_notes])
        self.assertEqual(
            other_client.put(
                f"/api/notes/{private_note['id']}",
                json={
                    "title": "Geklaut",
                    "content": "Geheim",
                    "visibility": "public",
                    "version": private_note["version"],
                },
                headers={"X-CSRF-Token": other_token},
            ).status_code,
            404,
        )
        self.assertEqual(
            other_client.delete(
                f"/api/notes/{private_note['id']}",
                headers={"X-CSRF-Token": other_token},
            ).status_code,
            404,
        )
        self.assertIn(
            private_note["id"],
            [note["id"] for note in self.client.get("/api/notes").get_json()["notes"]],
        )

    def test_only_note_creator_can_change_visibility(self):
        self.register("mira")
        owner_token = self.client.get("/api/session").get_json()["csrf_token"]
        created = self.client.post(
            "/api/notes",
            json={"title": "Teamnotiz", "content": "", "visibility": "public"},
            headers={"X-CSRF-Token": owner_token},
        )
        note = created.get_json()["note"]

        other_client = app.test_client()
        other_token = self.register_client(other_client, "jonas")
        forbidden = other_client.put(
            f"/api/notes/{note['id']}",
            json={
                "title": note["title"],
                "content": note["content"],
                "visibility": "private",
                "version": note["version"],
            },
            headers={"X-CSRF-Token": other_token},
        )
        self.assertEqual(forbidden.status_code, 403)

        changed = self.client.put(
            f"/api/notes/{note['id']}",
            json={
                "title": note["title"],
                "content": note["content"],
                "visibility": "private",
                "version": note["version"],
            },
            headers={"X-CSRF-Token": owner_token},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.get_json()["note"]["visibility"], "private")
        self.assertEqual(other_client.get("/api/notes").get_json()["notes"], [])

    def test_note_visibility_validation(self):
        self.register("mira")
        token = self.client.get("/api/session").get_json()["csrf_token"]
        response = self.client.post(
            "/api/notes",
            json={"title": "Ungültig", "content": "", "visibility": []},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 400)

    def test_note_content_sanitizes_script_and_preserves_table_markup(self):
        self.register("mira")
        token = self.client.get("/api/session").get_json()["csrf_token"]
        note = self.create_note(
            self.client,
            token,
            content="<p>Notiz</p><script>alert(1)</script><table><tr><td>Zelle</td></tr></table>",
        )
        self.assertIn("<table>", note["content"])
        self.assertIn("Zelle", note["content"])
        self.assertNotIn("<script>", note["content"])

    def test_uploaded_images_follow_private_note_access_rules(self):
        self.register("mira")
        owner_token = self.client.get("/api/session").get_json()["csrf_token"]
        private_note = self.create_note(self.client, owner_token, visibility="private")
        uploaded = self.upload_png(self.client, owner_token, private_note["id"])
        self.assertEqual(uploaded.status_code, 201)
        media = uploaded.get_json()["media"]
        self.assertEqual(media["media_type"], "image")
        self.assertEqual(media["mime_type"], "image/png")
        media_response = self.client.get(media["url"])
        self.assertEqual(media_response.status_code, 200)
        media_response.close()
        self.assertIn(media["id"], [item["id"] for item in self.client.get(
            f"/api/notes/{private_note['id']}/media"
        ).get_json()["media"]])

        other_client = app.test_client()
        other_token = self.register_client(other_client, "jonas")
        self.assertEqual(other_client.get(media["url"]).status_code, 404)
        self.assertEqual(
            other_client.post(
                f"/api/notes/{private_note['id']}/media",
                data={"file": (BytesIO(b"\x00"), "other.png")},
                headers={"X-CSRF-Token": other_token},
                content_type="multipart/form-data",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(
                f"/api/media/{media['id']}",
                headers={"X-CSRF-Token": owner_token},
            ).status_code,
            200,
        )
        self.assertEqual(list(Path(self.media_dir).iterdir()), [])

    def test_upload_rejects_non_image_and_video_files(self):
        self.register("mira")
        token = self.client.get("/api/session").get_json()["csrf_token"]
        note = self.create_note(self.client, token)
        response = self.client.post(
            f"/api/notes/{note['id']}/media",
            data={"file": (BytesIO(b"<svg onload=alert(1)>"), "image.svg")},
            headers={"X-CSRF-Token": token},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_removing_user_deletes_their_private_notes_only(self):
        self.register("admin")
        admin_id = self.client.get("/api/session").get_json()["user"]["id"]
        admin_token = self.client.get("/api/session").get_json()["csrf_token"]
        member_client = app.test_client()
        member_token = self.register_client(member_client, "mira")
        member_id = member_client.get("/api/session").get_json()["user"]["id"]
        private_note = member_client.post(
            "/api/notes",
            json={"title": "Privat", "content": "", "visibility": "private"},
            headers={"X-CSRF-Token": member_token},
        ).get_json()["note"]
        public_note = member_client.post(
            "/api/notes",
            json={"title": "Öffentlich", "content": "", "visibility": "public"},
            headers={"X-CSRF-Token": member_token},
        ).get_json()["note"]

        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE id = ?", (admin_id,)
            )
            connection.commit()
        finally:
            connection.close()

        removed = self.client.delete(
            f"/api/users/{member_id}",
            headers={"X-CSRF-Token": admin_token},
        )

        self.assertEqual(removed.status_code, 200)
        remaining = self.client.get("/api/notes").get_json()["notes"]
        remaining_ids = [note["id"] for note in remaining]
        self.assertNotIn(private_note["id"], remaining_ids)
        self.assertIn(public_note["id"], remaining_ids)

    def test_note_updates_detect_stale_versions(self):
        self.register("mira")
        self.csrf_token = self.client.get("/api/session").get_json()["csrf_token"]
        note = self.post("/api/notes", {"title": "Idee", "content": ""}).get_json()["note"]
        update = {
            "title": "Aktualisiert",
            "content": "",
            "version": note["version"],
        }
        self.assertEqual(
            self.client.put(
                f"/api/notes/{note['id']}",
                json=update,
                headers={"X-CSRF-Token": self.csrf_token},
            ).status_code,
            200,
        )
        stale = self.client.put(
            f"/api/notes/{note['id']}",
            json=update,
            headers={"X-CSRF-Token": self.csrf_token},
        )
        self.assertEqual(stale.status_code, 409)

    def test_mutations_require_csrf_token(self):
        response = self.client.post("/api/register", json={
            "username": "mira",
            "password": "a-long-test-password",
        })
        self.assertEqual(response.status_code, 400)

    def test_messenger_pages_render(self):
        self.assertIn("Meld", self.client.get("/").get_data(as_text=True))
        self.assertIn("Notizen", self.client.get("/notes").get_data(as_text=True))

    def test_direct_conversations_and_messages_are_persistent_and_private(self):
        self.register("mira")
        mira_token = self.client.get("/api/session").get_json()["csrf_token"]
        jonas_client = app.test_client()
        jonas_token = self.register_client(jonas_client, "jonas")

        contacts = self.client.get("/api/contacts").get_json()["contacts"]
        self.assertEqual([contact["username"] for contact in contacts], ["jonas"])

        created = self.client.post(
            "/api/conversations",
            json={"user_id": jonas_client.get("/api/session").get_json()["user"]["id"]},
            headers={"X-CSRF-Token": mira_token},
        )
        self.assertEqual(created.status_code, 201)
        conversation_id = created.get_json()["conversation"]["id"]
        duplicate = jonas_client.post(
            "/api/conversations",
            json={"user_id": self.client.get("/api/session").get_json()["user"]["id"]},
            headers={"X-CSRF-Token": jonas_token},
        )
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.get_json()["conversation"]["id"], conversation_id)
        invalid_message = jonas_client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "   "},
            headers={"X-CSRF-Token": jonas_token},
        )
        self.assertEqual(invalid_message.status_code, 400)

        sent = jonas_client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Hallo Mira"},
            headers={"X-CSRF-Token": jonas_token},
        )
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.get_json()["message"]["content"], "Hallo Mira")
        inbox = self.client.get("/api/conversations").get_json()["conversations"]
        self.assertEqual(inbox[0]["unread_count"], 1)

        received = self.client.get(
            f"/api/conversations/{conversation_id}/messages"
        ).get_json()["messages"]
        self.assertEqual([message["content"] for message in received], ["Hallo Mira"])
        self.assertIsNone(received[0]["read_at"])
        marked_read = self.client.post(
            f"/api/conversations/{conversation_id}/read",
            json={},
            headers={"X-CSRF-Token": mira_token},
        )
        self.assertEqual(marked_read.status_code, 200)
        received = self.client.get(
            f"/api/conversations/{conversation_id}/messages"
        ).get_json()["messages"]
        self.assertIsNotNone(received[0]["read_at"])
        self.assertEqual(
            self.client.get("/api/conversations").get_json()["conversations"][0]["unread_count"],
            0,
        )

        stranger = app.test_client()
        self.register_client(stranger, "lena")
        self.assertEqual(
            stranger.get(f"/api/conversations/{conversation_id}/messages").status_code,
            404,
        )
        self.assertEqual(
            stranger.post(
                f"/api/conversations/{conversation_id}/messages",
                json={"content": "Nicht erlaubt"},
                headers={"X-CSRF-Token": stranger.get("/api/session").get_json()["csrf_token"]},
            ).status_code,
            404,
        )

    def test_direct_messages_validate_contact_and_message_content(self):
        self.register("mira")
        token = self.client.get("/api/session").get_json()["csrf_token"]
        self.assertEqual(
            self.client.post(
                "/api/conversations",
                json={"user_id": True},
                headers={"X-CSRF-Token": token},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/conversations",
                json={"user_id": 9999},
                headers={"X-CSRF-Token": token},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/conversations",
                json={"user_id": self.client.get("/api/session").get_json()["user"]["id"]},
                headers={"X-CSRF-Token": token},
            ).status_code,
            400,
        )

    def test_session_refresh_recovers_from_deleted_user(self):
        with self.client.session_transaction() as browser_session:
            browser_session["user_id"] = 9999

        response = self.client.get("/api/session")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["user"])
        self.assertTrue(response.get_json()["csrf_token"])

    def test_existing_database_notes_migrate_as_public(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy.sqlite3")
        legacy = sqlite3.connect(legacy_path)
        try:
            legacy.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    author_id INTEGER,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO notes (title, content, created_at, updated_at)
                VALUES ('Vorhandene Notiz', 'Bleibt geteilt', '2026-01-01', '2026-01-01');
                """
            )
        finally:
            legacy.close()

        app.config["DATABASE_PATH"] = legacy_path
        initialize_database()
        initialize_database()
        migrated = sqlite3.connect(legacy_path)
        try:
            visibility = migrated.execute(
                "SELECT visibility FROM notes WHERE title = 'Vorhandene Notiz'"
            ).fetchone()[0]
        finally:
            migrated.close()
        self.assertEqual(visibility, "public")


if __name__ == "__main__":
    unittest.main()
