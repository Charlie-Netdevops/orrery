/* Orrery - login page (only used when APP_PASSWORD is set). */
"use strict";
const form = document.getElementById("loginForm");
const input = document.getElementById("password");
const err = document.getElementById("loginErr");
const btn = document.getElementById("loginBtn");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!input.value) { err.textContent = "Enter the password."; input.focus(); return; }
  btn.disabled = true; err.textContent = "";
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: input.value }),
    });
    if (r.ok) { location.replace("/"); return; }
    const j = await r.json().catch(() => ({}));
    err.textContent = j.detail || `Login failed (${r.status}).`;
  } catch (x) {
    err.textContent = "Can't reach the Orrery server. Is the container running?";
  } finally {
    btn.disabled = false; input.value = ""; input.focus();
  }
});
