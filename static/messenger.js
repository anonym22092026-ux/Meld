const state = {
  user: null,
  csrfToken: "",
  conversations: [],
  contacts: [],
  selectedConversation: null,
  lastMessageId: 0,
  lastMessageDate: "",
  registering: false,
  toastTimer: null,
  polling: false,
};

const byId = (id) => document.getElementById(id);
const authView = byId("auth-view");
const chatView = byId("chat-view");
const contactsDialog = byId("contacts-dialog");

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
  state.toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function initials(name) {
  return (name || "?").slice(0, 1).toLocaleUpperCase("de");
}

function avatarTone(id) {
  return `avatar-tone-${(Number(id) % 5) + 1}`;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" }).format(date);
  }
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return "Gestern";
  return new Intl.DateTimeFormat("de-DE", { day: "2-digit", month: "2-digit" }).format(date);
}

function formatDay(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long" }).format(date);
}

function setAuthMode(registering) {
  state.registering = registering;
  byId("login-tab").classList.toggle("is-active", !registering);
  byId("register-tab").classList.toggle("is-active", registering);
  byId("login-tab").setAttribute("aria-selected", String(!registering));
  byId("register-tab").setAttribute("aria-selected", String(registering));
  byId("auth-title").textContent = registering ? "Schön, dass du dabei bist." : "Schön, dass du da bist.";
  byId("auth-subtitle").textContent = registering
    ? "Erstelle dein Konto und starte Gespräche mit deinem Team."
    : "Melde dich an und bleib mit den Menschen in deinem Team verbunden.";
  byId("auth-submit").innerHTML = registering
    ? 'Konto erstellen <span aria-hidden="true">→</span>'
    : 'Anmelden <span aria-hidden="true">→</span>';
  byId("password").autocomplete = registering ? "new-password" : "current-password";
  byId("password-hint").hidden = !registering;
  byId("auth-error").hidden = true;
}

function showAuthenticatedView() {
  authView.hidden = true;
  chatView.hidden = false;
  byId("profile-name").textContent = state.user.username;
  byId("profile-avatar").textContent = initials(state.user.username);
  loadConversations().catch((error) => showToast(error.message, true));
}

function renderConversations() {
  const query = byId("conversation-search").value.trim().toLocaleLowerCase("de");
  const list = byId("conversation-list");
  const filtered = state.conversations.filter((conversation) =>
    `${conversation.contact_username} ${conversation.last_message || ""}`.toLocaleLowerCase("de").includes(query)
  );
  list.replaceChildren();
  byId("conversation-count").textContent = String(state.conversations.length);
  byId("inbox-empty").hidden = state.conversations.length > 0 || Boolean(query);
  list.hidden = filtered.length === 0;

  filtered.forEach((conversation) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "conversation-item";
    if (state.selectedConversation?.id === conversation.id) button.classList.add("is-active");
    button.setAttribute("aria-label", `Chat mit ${conversation.contact_username}`);

    const avatar = document.createElement("span");
    avatar.className = `avatar ${avatarTone(conversation.contact_id)}`;
    avatar.textContent = initials(conversation.contact_username);

    const copy = document.createElement("span");
    copy.className = "conversation-copy";
    const topline = document.createElement("span");
    topline.className = "conversation-topline";
    const name = document.createElement("strong");
    name.textContent = conversation.contact_username;
    const time = document.createElement("time");
    time.className = "conversation-time";
    time.textContent = formatTime(conversation.last_message_at || conversation.updated_at);
    topline.append(name, time);
    const preview = document.createElement("span");
    preview.className = "conversation-preview";
    preview.textContent = conversation.last_message || "Schreib die erste Nachricht";
    copy.append(topline, preview);
    button.append(avatar, copy);

    if (conversation.unread_count > 0) {
      const unread = document.createElement("span");
      unread.className = "unread-badge";
      unread.textContent = conversation.unread_count > 99 ? "99+" : String(conversation.unread_count);
      button.append(unread);
    }
    button.addEventListener("click", () => selectConversation(conversation));
    list.append(button);
  });
}

async function loadConversations() {
  const { conversations } = await api("/api/conversations");
  state.conversations = conversations;
  if (state.selectedConversation) {
    const updated = conversations.find((item) => item.id === state.selectedConversation.id);
    if (updated) state.selectedConversation = updated;
  }
  renderConversations();
}

function renderMessage(message) {
  const list = byId("message-list");
  const dateLabel = formatDay(message.created_at);
  if (dateLabel && dateLabel !== state.lastMessageDate) {
    const day = document.createElement("div");
    day.className = "message-day";
    day.textContent = dateLabel;
    list.append(day);
    state.lastMessageDate = dateLabel;
  }

  const mine = message.sender_id === state.user.id;
  const row = document.createElement("article");
  row.className = `message-row${mine ? " is-mine" : ""}`;
  if (!mine && message.sender_username) {
    const sender = document.createElement("span");
    sender.className = "message-sender";
    sender.textContent = message.sender_username;
    row.append(sender);
  }
  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = message.content;
  const meta = document.createElement("time");
  meta.className = "message-meta";
  meta.dateTime = message.created_at;
  meta.textContent = formatTime(message.created_at);
  row.append(bubble, meta);
  list.append(row);
  state.lastMessageId = Math.max(state.lastMessageId, Number(message.id));
}

async function loadMessages() {
  const conversation = state.selectedConversation;
  if (!conversation) return;
  const { messages } = await api(
    `/api/conversations/${conversation.id}/messages?after=${state.lastMessageId}`
  );
  await api(`/api/conversations/${conversation.id}/read`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (messages.length === 0) return;
  const list = byId("message-list");
  const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 100;
  messages.forEach(renderMessage);
  if (nearBottom || messages.some((message) => message.sender_id === state.user.id)) {
    list.scrollTop = list.scrollHeight;
  }
}

async function selectConversation(conversation) {
  state.selectedConversation = conversation;
  state.lastMessageId = 0;
  state.lastMessageDate = "";
  byId("welcome-panel").hidden = true;
  byId("conversation-panel").hidden = false;
  byId("chat-avatar").textContent = initials(conversation.contact_username);
  byId("chat-avatar").className = `avatar chat-avatar ${avatarTone(conversation.contact_id)}`;
  byId("chat-contact-name").textContent = conversation.contact_username;
  byId("message-list").replaceChildren();
  byId("message-input").focus();
  chatView.classList.add("mobile-chat-open");
  renderConversations();
  try {
    await loadMessages();
    await loadConversations();
    byId("message-list").scrollTop = byId("message-list").scrollHeight;
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderContacts() {
  const query = byId("contact-search-input").value.trim().toLocaleLowerCase("de");
  const contacts = state.contacts.filter((contact) => contact.username.toLocaleLowerCase("de").includes(query));
  const list = byId("contact-list");
  list.replaceChildren();
  byId("contact-empty").hidden = contacts.length !== 0;
  contacts.forEach((contact) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "contact-option";
    const avatar = document.createElement("span");
    avatar.className = `avatar ${avatarTone(contact.id)}`;
    avatar.textContent = initials(contact.username);
    const name = document.createElement("strong");
    name.textContent = contact.username;
    const arrow = document.createElement("span");
    arrow.textContent = "→";
    button.append(avatar, name, arrow);
    button.addEventListener("click", () => startConversation(contact));
    list.append(button);
  });
}

async function openContacts() {
  byId("contact-error").hidden = true;
  byId("contact-search-input").value = "";
  try {
    const { contacts } = await api("/api/contacts");
    state.contacts = contacts;
    renderContacts();
    contactsDialog.showModal();
    byId("contact-search-input").focus();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function startConversation(contact) {
  try {
    const { conversation } = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ user_id: contact.id }),
    });
    contactsDialog.close();
    await loadConversations();
    const selected = state.conversations.find((item) => item.id === conversation.id) || {
      id: conversation.id,
      contact_id: contact.id,
      contact_username: contact.username,
      last_message: null,
      unread_count: 0,
    };
    await selectConversation(selected);
  } catch (error) {
    byId("contact-error").textContent = error.message;
    byId("contact-error").hidden = false;
  }
}

async function refreshSession() {
  const data = await api("/api/session");
  state.user = data.user;
  state.csrfToken = data.csrf_token;
  if (state.user) showAuthenticatedView();
  else {
    chatView.hidden = true;
    authView.hidden = false;
    setAuthMode(false);
  }
}

byId("login-tab").addEventListener("click", () => setAuthMode(false));
byId("register-tab").addEventListener("click", () => setAuthMode(true));
byId("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const errorElement = byId("auth-error");
  errorElement.hidden = true;
  const endpoint = state.registering ? "/api/register" : "/api/login";
  try {
    await api(endpoint, {
      method: "POST",
      body: JSON.stringify({
        username: byId("username").value.trim(),
        password: byId("password").value,
      }),
    });
    byId("auth-form").reset();
    await refreshSession();
  } catch (error) {
    errorElement.textContent = error.message;
    errorElement.hidden = false;
  }
});

["new-chat", "empty-new-chat", "welcome-new-chat"].forEach((id) => {
  byId(id).addEventListener("click", openContacts);
});
byId("close-contacts").addEventListener("click", () => contactsDialog.close());
byId("contact-search-input").addEventListener("input", renderContacts);
byId("conversation-search").addEventListener("input", renderConversations);
byId("back-to-inbox").addEventListener("click", () => chatView.classList.remove("mobile-chat-open"));

byId("message-input").addEventListener("input", (event) => {
  event.target.style.height = "auto";
  event.target.style.height = `${Math.min(event.target.scrollHeight, 150)}px`;
});
byId("message-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    byId("message-form").requestSubmit();
  }
});
byId("message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("message-input");
  const content = input.value.trim();
  if (!state.selectedConversation || !content) return;
  const sendButton = byId("send-message");
  sendButton.disabled = true;
  try {
    const { message } = await api(
      `/api/conversations/${state.selectedConversation.id}/messages`,
      { method: "POST", body: JSON.stringify({ content }) }
    );
    input.value = "";
    input.style.height = "auto";
    renderMessage(message);
    byId("message-list").scrollTop = byId("message-list").scrollHeight;
    await loadConversations();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    sendButton.disabled = false;
    input.focus();
  }
});

byId("logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: JSON.stringify({}) });
    state.selectedConversation = null;
    state.conversations = [];
    await refreshSession();
  } catch (error) {
    showToast(error.message, true);
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !chatView.hidden) {
    event.preventDefault();
    byId("conversation-search").focus();
  }
  if (event.key === "Escape" && contactsDialog.open) contactsDialog.close();
});

setInterval(async () => {
  if (!state.user || state.polling) return;
  state.polling = true;
  try {
    await loadConversations();
    await loadMessages();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.polling = false;
  }
}, 4000);

refreshSession().catch((error) => {
  authView.hidden = false;
  byId("auth-error").textContent = error.message;
  byId("auth-error").hidden = false;
});
