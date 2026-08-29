const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const messages = document.getElementById("messages");
const newChatBtn = document.getElementById("new-chat-btn");
const fileInput = document.getElementById("file-input");
const filePreview = document.getElementById("file-preview");

const SESSION_KEY = "gemini_chat_session_id";
let sessionId = localStorage.getItem(SESSION_KEY) || null;
let attachedFile = null;

function saveSessionId(id) {
  sessionId = id;
  localStorage.setItem(SESSION_KEY, id);
}

fileInput.addEventListener("change", () => {
  attachedFile = fileInput.files[0] || null;
  renderFilePreview();
});

function renderFilePreview() {
  if (!attachedFile) {
    filePreview.classList.add("hidden");
    filePreview.innerHTML = "";
    return;
  }
  filePreview.classList.remove("hidden");
  filePreview.innerHTML = `<span>📎 ${attachedFile.name}</span>`;
  const clearBtn = document.createElement("button");
  clearBtn.type = "button";
  clearBtn.textContent = "Remove";
  clearBtn.addEventListener("click", () => {
    attachedFile = null;
    fileInput.value = "";
    renderFilePreview();
  });
  filePreview.appendChild(clearBtn);
}

newChatBtn.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    saveSessionId(data.session_id);
  } catch (err) {
    localStorage.removeItem(SESSION_KEY);
    sessionId = null;
  }
  messages.innerHTML = "";
  addMessage("Started a new chat.", "bot");
  input.focus();
});

function addMessage(text, sender, sources) {
  const div = document.createElement("div");
  div.className = `msg ${sender}`;
  div.textContent = text;
  if (sources && sources.length) {
    const src = document.createElement("div");
    src.className = "sources";
    src.textContent = "Sources: " + sources.join(" · ");
    div.appendChild(src);
  }
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
  return div;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  const label = attachedFile ? `${text} [+ ${attachedFile.name}]` : text;
  addMessage(label, "user");

  const formData = new FormData();
  formData.append("message", text);
  if (sessionId) formData.append("session_id", sessionId);
  if (attachedFile) formData.append("file", attachedFile);

  input.value = "";
  attachedFile = null;
  fileInput.value = "";
  renderFilePreview();
  input.disabled = true;

  const loadingEl = addMessage("...", "bot");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (data.session_id) saveSessionId(data.session_id);
    loadingEl.textContent = data.reply || `Error: ${data.error}`;
    if (data.sources && data.sources.length) {
      const src = document.createElement("div");
      src.className = "sources";
      src.textContent = "Sources: " + data.sources.join(" · ");
      loadingEl.appendChild(src);
    }
  } catch (err) {
    loadingEl.textContent = `Error: ${err.message}`;
  } finally {
    input.disabled = false;
    input.focus();
  }
});

