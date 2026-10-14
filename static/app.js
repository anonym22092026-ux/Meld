const state = {
  user: null,
  csrfToken: "",
  notes: [],
  editingNote: null,
  pendingFiles: [],
  editorRange: null,
  visibilityFilter: "all",
  registering: false,
  toastTimer: null,
};

const byId = (id) => document.getElementById(id);
const authView = byId("auth-view");
const workspaceView = byId("workspace-view");
const noteDialog = byId("note-dialog");
const usersDialog = byId("users-dialog");

async function api(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", state.csrfToken);
  const response = await fetch(url, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Etwas ist schiefgelaufen.");
  return data;
}

function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3000);
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("de-DE", { day: "numeric", month: "short" }).format(date);
}

function initials(name) {
  return (name || "?").slice(0, 1).toUpperCase();
}

function setAuthMode(registering) {
  state.registering = registering;
  byId("login-tab").classList.toggle("is-active", !registering);
  byId("register-tab").classList.toggle("is-active", registering);
  byId("auth-title").textContent = registering ? "Gemeinsam loslegen." : "Schön, dass du da bist.";
  byId("auth-subtitle").textContent = registering
    ? "Erstelle dein Konto für den gemeinsamen Arbeitsbereich."
    : "Melde dich an und halte Ideen an einem Ort fest.";
  byId("auth-submit").innerHTML = registering
    ? 'Konto erstellen <span aria-hidden="true">→</span>'
    : 'Anmelden <span aria-hidden="true">→</span>';
  byId("password").autocomplete = registering ? "new-password" : "current-password";
  byId("password-hint").hidden = !registering;
  byId("auth-error").hidden = true;
}

function showAuthenticatedView() {
  authView.hidden = true;
  workspaceView.hidden = false;
  byId("current-username").textContent = state.user.username;
  byId("user-avatar").textContent = initials(state.user.username);
  byId("current-role").textContent = state.user.role === "admin" ? "Administrator" : "Mitglied";
  byId("admin-link").hidden = state.user.role !== "admin";
  loadNotes();
}

function renderNotes() {
  const grid = byId("notes-grid");
  const query = byId("search").value.trim().toLocaleLowerCase("de");
  const filtered = state.notes.filter((note) =>
    (state.visibilityFilter === "all" || note.visibility === state.visibilityFilter)
      && `${note.title}\n${note.content_text}\n${note.author || ""}`.toLocaleLowerCase("de").includes(query)
  );
  grid.replaceChildren();
  byId("note-count").textContent = state.notes.length;
  byId("results-label").textContent = `${filtered.length} ${filtered.length === 1 ? "Notiz" : "Notizen"}`;
  byId("empty-state").hidden = filtered.length !== 0;
  grid.hidden = filtered.length === 0;
  document.querySelectorAll(".view-tab").forEach((tab) => {
    const selected = tab.dataset.filter === state.visibilityFilter;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-pressed", String(selected));
  });
  const emptyTitle = byId("empty-title");
  const emptyDescription = byId("empty-description");
  if (query) {
    emptyTitle.textContent = "Keine passenden Notizen gefunden.";
    emptyDescription.textContent = "Passe deine Suche an oder wähle einen anderen Filter.";
  } else if (state.visibilityFilter === "private") {
    emptyTitle.textContent = "Noch keine privaten Notizen.";
    emptyDescription.textContent = "Erstelle eine Notiz, die nur du sehen kannst.";
  } else if (state.visibilityFilter === "public") {
    emptyTitle.textContent = "Noch keine öffentlichen Notizen.";
    emptyDescription.textContent = "Erstelle eine öffentliche Notiz, um sie mit allen zu teilen.";
  } else {
    emptyTitle.textContent = "Der Anfang ist eine leere Seite.";
    emptyDescription.textContent = "Erstelle eine private Notiz oder teile einen Gedanken mit deinem Team.";
  }

  filtered.forEach((note) => {
    const card = document.createElement("article");
    card.className = "note-card";
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Notiz bearbeiten: ${note.title}, ${note.visibility === "private" ? "privat" : "öffentlich"}`);

    const type = document.createElement("div");
    type.className = `note-type ${note.visibility === "private" ? "is-private" : ""}`;
    type.textContent = note.visibility === "private" ? "PRIVAT · NUR FÜR DICH" : "ÖFFENTLICH · GEMEINSAM";
    const title = document.createElement("h2");
    title.textContent = note.title;
    const preview = document.createElement("p");
    preview.className = "note-preview";
    preview.textContent = note.content_text || (note.media?.length ? "Medienanhang" : "Noch kein Inhalt. Klicke, um weiterzuschreiben.");
    const meta = document.createElement("div");
    meta.className = "note-meta";
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = initials(note.author || "N");
    const author = document.createElement("span");
    author.textContent = note.author || "Ehemaliges Mitglied";
    const date = document.createElement("time");
    date.dateTime = note.updated_at;
    date.textContent = formatDate(note.updated_at);
    meta.append(avatar, author, date);
    card.append(type, title, preview, meta);
    card.addEventListener("click", () => openNote(note));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openNote(note);
      }
    });
    grid.append(card);
  });
}

async function loadNotes() {
  try {
    const { notes } = await api("/api/notes");
    state.notes = notes;
    renderNotes();
  } catch (error) {
    showToast(error.message, true);
  }
}

function openNote(note = null) {
  state.editingNote = note ? { ...note } : null;
  state.pendingFiles = [];
  state.editorRange = null;
  byId("dialog-label").textContent = note ? "NOTIZ BEARBEITEN" : "NEUE NOTIZ";
  byId("note-title").value = note?.title || "";
  byId("note-content").innerHTML = note?.content || "";
  renderMedia(note?.media || []);
  const visibility = byId("note-visibility");
  visibility.value = note?.visibility || "public";
  visibility.disabled = Boolean(note && note.author_id !== state.user.id);
  byId("visibility-help").textContent = visibility.disabled
    ? "Nur die erstellende Person kann die Sichtbarkeit ändern."
    : "Öffentliche Notizen sehen alle. Private Notizen kannst nur du sehen.";
  byId("note-author").textContent = note
    ? `Erstellt von ${note.author || "ehemaligem Mitglied"} · ${note.visibility === "private" ? "Nur für dich sichtbar" : "Öffentlich im Team"}`
    : `Erstellt von ${state.user.username}`;
  byId("delete-note").hidden = !note;
  noteDialog.showModal();
  requestAnimationFrame(() => byId("note-title").focus());
}

function formatFileSize(size) {
  return size >= 1024 * 1024
    ? `${(size / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(size / 1024))} KB`;
}

function renderMedia(media) {
  const list = byId("note-media-list");
  list.replaceChildren();
  media.forEach((item) => {
    const row = document.createElement("div");
    row.className = "media-item";
    const name = document.createElement("span");
    name.className = "media-item-name";
    name.textContent = item.original_name || item.file.name;
    const size = document.createElement("small");
    size.textContent = `${formatFileSize(item.size_bytes ?? item.file.size)}${item.pending ? " · wird beim Speichern hochgeladen" : ""}`;
    row.append(name, size);

    if (!item.pending) {
      if (item.media_type === "image") {
        const image = document.createElement("img");
        image.src = item.url;
        image.alt = item.original_name;
        image.loading = "lazy";
        row.prepend(image);
      } else {
        const video = document.createElement("video");
        video.controls = true;
        video.preload = "metadata";
        video.playsInline = true;
        const source = document.createElement("source");
        source.src = item.url;
        source.type = item.mime_type;
        video.append(source);
        row.prepend(video);
      }
      const remove = document.createElement("button");
      remove.className = "media-remove";
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Anhang ${item.original_name} entfernen`);
      remove.addEventListener("click", async () => {
        try {
          await api(`/api/media/${item.id}`, { method: "DELETE" });
          state.editingNote.media = state.editingNote.media.filter((entry) => entry.id !== item.id);
          renderMedia([...state.editingNote.media, ...state.pendingFiles.map((file) => ({ file, pending: true }))]);
        } catch (error) {
          showToast(error.message, true);
        }
      });
      row.append(remove);
    } else {
      const remove = document.createElement("button");
      remove.className = "media-remove";
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Ausgewählte Datei ${item.file.name} entfernen`);
      remove.addEventListener("click", () => {
        state.pendingFiles = state.pendingFiles.filter((file) => file !== item.file);
        renderMedia([...(state.editingNote?.media || []), ...state.pendingFiles.map((file) => ({ file, pending: true }))]);
      });
      row.append(remove);
    }
    list.append(row);
  });
}

async function uploadPendingMedia(noteId) {
  for (let index = 0; index < state.pendingFiles.length; index += 1) {
    const file = state.pendingFiles[index];
    const form = new FormData();
    form.append("file", file);
    try {
      await api(`/api/notes/${noteId}/media`, { method: "POST", body: form });
    } catch (error) {
      state.pendingFiles = state.pendingFiles.slice(index);
      showToast(`Notiz gespeichert, aber „${file.name}“ konnte nicht hochgeladen werden: ${error.message}`, true);
      return false;
    }
  }
  state.pendingFiles = [];
  return true;
}

function insertTable() {
  const dimensions = window.prompt("Wie viele Zeilen und Spalten? (z. B. 3x3, höchstens 12x8)", "3x3");
  if (dimensions === null) return;
  const match = dimensions.trim().match(/^(\d{1,2})\s*[x×]\s*(\d{1,2})$/i);
  const rows = Number(match?.[1]);
  const columns = Number(match?.[2]);
  if (!match || rows < 1 || rows > 12 || columns < 1 || columns > 8) {
    showToast("Bitte eine gültige Tabellengröße eingeben (maximal 12 × 8).", true);
    return;
  }

  const editor = byId("note-content");
  editor.focus();
  const selection = window.getSelection();
  if (state.editorRange) {
    selection.removeAllRanges();
    selection.addRange(state.editorRange);
  }
  const table = document.createElement("table");
  const body = document.createElement("tbody");
  for (let rowIndex = 0; rowIndex < rows; rowIndex += 1) {
    const row = document.createElement("tr");
    for (let columnIndex = 0; columnIndex < columns; columnIndex += 1) {
      const cell = document.createElement(rowIndex === 0 ? "th" : "td");
      cell.textContent = rowIndex === 0 ? `Spalte ${columnIndex + 1}` : "";
      row.append(cell);
    }
    body.append(row);
  }
  table.append(body);
  const range = selection.rangeCount ? selection.getRangeAt(0) : document.createRange();
  if (!selection.rangeCount) {
    range.selectNodeContents(editor);
    range.collapse(false);
  }
  range.deleteContents();
  range.insertNode(table);
  const paragraph = document.createElement("p");
  paragraph.append(document.createElement("br"));
  table.after(paragraph);
  range.setStart(paragraph, 0);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  state.editorRange = range.cloneRange();
}

async function loadUsers() {
  const { users } = await api("/api/users");
  const list = byId("users-list");
  list.replaceChildren();
  users.forEach((user) => {
    const row = document.createElement("div");
    row.className = "user-row";
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = initials(user.username);
    const info = document.createElement("div");
    info.className = "user-row-info";
    const name = document.createElement("strong");
    name.textContent = user.username;
    const joined = document.createElement("small");
    joined.textContent = `Dabei seit ${formatDate(user.created_at)}`;
    info.append(name, joined);
    row.append(avatar, info);

    const actions = document.createElement("div");
    actions.className = "user-row-actions";
    if (user.id !== state.user.id) {
      const role = document.createElement("select");
      role.setAttribute("aria-label", `Rolle für ${user.username}`);
      [["member", "Mitglied"], ["admin", "Admin"]].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        role.append(option);
      });
      role.value = user.role;
      role.addEventListener("change", async () => {
        try {
          await api(`/api/users/${user.id}`, {
            method: "PATCH",
            body: JSON.stringify({ role: role.value }),
          });
          showToast(`${user.username}: Rolle aktualisiert.`);
        } catch (error) {
          role.value = user.role;
          showToast(error.message, true);
        }
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "Entfernen";
      remove.addEventListener("click", async () => {
        if (!window.confirm(`Konto „${user.username}“ wirklich entfernen? Öffentliche Notizen bleiben erhalten; private Notizen dieses Kontos werden gelöscht.`)) return;
        try {
          await api(`/api/users/${user.id}`, { method: "DELETE" });
          row.remove();
          showToast("Konto wurde entfernt.");
        } catch (error) {
          showToast(error.message, true);
        }
      });
      actions.append(role, remove);
    } else {
      const badge = document.createElement("small");
      badge.textContent = user.role === "admin" ? "Du · Admin" : "Du";
      actions.append(badge);
    }
    row.append(actions);
    list.append(row);
  });
}

byId("login-tab").addEventListener("click", () => setAuthMode(false));
byId("register-tab").addEventListener("click", () => setAuthMode(true));

byId("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const errorBox = byId("auth-error");
  errorBox.hidden = true;
  const payload = {
    username: byId("username").value.trim(),
    password: byId("password").value,
  };
  try {
    const endpoint = state.registering ? "/api/register" : "/api/login";
    await api(endpoint, { method: "POST", body: JSON.stringify(payload) });
    await refreshSession();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
});

byId("new-note").addEventListener("click", () => openNote());
byId("top-new-note").addEventListener("click", () => openNote());
byId("empty-state").addEventListener("click", (event) => {
  if (event.target.closest('[data-action="new"]')) openNote();
});
byId("search").addEventListener("input", renderNotes);
document.querySelectorAll(".editor-tool[data-command]").forEach((button) => {
  button.addEventListener("mousedown", (event) => event.preventDefault());
  button.addEventListener("click", () => {
    byId("note-content").focus();
    document.execCommand(button.dataset.command, false);
  });
});
byId("insert-table").addEventListener("mousedown", (event) => event.preventDefault());
byId("insert-table").addEventListener("click", insertTable);
byId("add-media").addEventListener("click", () => byId("media-files").click());
byId("media-files").addEventListener("change", () => {
  const files = [...byId("media-files").files];
  state.pendingFiles.push(...files);
  renderMedia([...(state.editingNote?.media || []), ...state.pendingFiles.map((file) => ({ file, pending: true }))]);
  byId("media-files").value = "";
});
document.addEventListener("selectionchange", () => {
  const editor = byId("note-content");
  const selection = window.getSelection();
  if (selection.rangeCount && editor.contains(selection.anchorNode)) {
    state.editorRange = selection.getRangeAt(0).cloneRange();
  }
});
document.querySelectorAll(".view-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    state.visibilityFilter = tab.dataset.filter;
    renderNotes();
  });
});
byId("all-notes").addEventListener("click", () => {
  byId("search").value = "";
  state.visibilityFilter = "all";
  renderNotes();
});
byId("note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = byId("note-title").value.trim();
  const content = byId("note-content").innerHTML;
  const visibility = byId("note-visibility").value;
  if (!title) {
    byId("note-title").focus();
    return;
  }
  try {
    let savedNote;
    if (state.editingNote) {
      const result = await api(`/api/notes/${state.editingNote.id}`, {
        method: "PUT",
        body: JSON.stringify({ title, content, visibility, version: state.editingNote.version }),
      });
      savedNote = result.note;
    } else {
      const result = await api("/api/notes", {
        method: "POST",
        body: JSON.stringify({ title, content, visibility }),
      });
      savedNote = result.note;
    }
    state.editingNote = savedNote;
    if (!(await uploadPendingMedia(savedNote.id))) return;
    const attachments = await api(`/api/notes/${savedNote.id}/media`);
    state.editingNote.media = attachments.media;
    noteDialog.close();
    await loadNotes();
    showToast("Notiz gespeichert.");
  } catch (error) {
    showToast(error.message, true);
    if (error.message.includes("zwischenzeitlich geändert")) await loadNotes();
  }
});
byId("delete-note").addEventListener("click", async () => {
  const confirmation = state.editingNote?.visibility === "private"
    ? "Diese private Notiz wirklich löschen?"
    : "Diese öffentliche Notiz wirklich löschen?";
  if (!state.editingNote || !window.confirm(confirmation)) return;
  try {
    await api(`/api/notes/${state.editingNote.id}`, { method: "DELETE" });
    noteDialog.close();
    await loadNotes();
    showToast("Notiz gelöscht.");
  } catch (error) {
    showToast(error.message, true);
  }
});
byId("close-dialog").addEventListener("click", () => noteDialog.close());
byId("cancel-note").addEventListener("click", () => noteDialog.close());
byId("user-menu").addEventListener("click", () => {
  const dropdown = byId("user-dropdown");
  dropdown.hidden = !dropdown.hidden;
  byId("user-menu").setAttribute("aria-expanded", String(!dropdown.hidden));
});
byId("logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: JSON.stringify({}) });
    state.user = null;
    await refreshSession();
  } catch (error) {
    showToast(error.message, true);
  }
});
byId("admin-link").addEventListener("click", async () => {
  try {
    await loadUsers();
    usersDialog.showModal();
    byId("user-dropdown").hidden = true;
  } catch (error) {
    showToast(error.message, true);
  }
});
byId("close-users").addEventListener("click", () => usersDialog.close());
byId("collapse-sidebar").addEventListener("click", () => {
  workspaceView.classList.toggle("sidebar-collapsed");
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    byId("search").focus();
  }
  if (event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !event.altKey
      && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)
      && !noteDialog.open && !usersDialog.open && !workspaceView.hidden) {
    event.preventDefault();
    openNote();
  }
});

async function refreshSession() {
  const data = await api("/api/session");
  state.user = data.user;
  state.csrfToken = data.csrf_token;
  if (state.user) showAuthenticatedView();
  else {
    workspaceView.hidden = true;
    authView.hidden = false;
    setAuthMode(false);
  }
}

refreshSession().catch((error) => {
  authView.hidden = false;
  byId("auth-error").textContent = error.message;
  byId("auth-error").hidden = false;
});
