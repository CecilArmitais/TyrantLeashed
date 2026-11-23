/* The sign-in page: asks the server (/account) what it needs, signs in
 * (/account/sign-in), and opens the game once signed in.
 *
 * Bringing progress: the chosen file is read here, in the browser. A save from another
 * server goes as it is. The official game's login data (the init reply, or a HAR
 * recording with it in) goes without the account's credentials: the same keys the
 * server drops (login_import.py's SECRET_KEYS) are taken out before anything is sent,
 * so a HAR's passwords, cookies and Kongregate session never leave this computer. */
(function () {
  "use strict";

  var SECRET_KEYS = ["request", "password", "new_password", "syncode", "new_syncode", "hash", "client_signature",
                     "udid", "kong_id", "kong_name", "kong_token", "kongregate_id", "kongregate_token"];
  var INIT_CALL = /(?:^|[?&])message=init(?:&|$)/;

  var $ = function (id) { return document.getElementById(id); };
  var form = $("form"), message = $("message"), submit = $("submit");
  var info = { passwords: false, imports: "off", name: null };

  function say(text, kind) {
    message.textContent = text || "";
    message.className = "message" + (kind ? " " + kind : "");
  }

  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                         body: JSON.stringify(body || {}), credentials: "same-origin" })
      .then(function (r) { return r.json(); });
  }

  function show() {
    $("password-part").hidden = !info.passwords;
    $("no-password-note").hidden = !!info.passwords;
    $("import-part").hidden = info.imports === "off";
    $("import-review").hidden = info.imports !== "review";
    $("current").hidden = !info.name;
    if (info.name) {
      $("current-name").textContent = info.name;
      submit.textContent = "Sign in as someone else";
    }
  }

  function withoutSecrets(value) {
    if (Array.isArray(value)) return value.map(withoutSecrets);
    if (value && typeof value === "object") {
      var out = {};
      Object.keys(value).forEach(function (k) {
        if (SECRET_KEYS.indexOf(k) < 0) out[k] = withoutSecrets(value[k]);
      });
      return out;
    }
    return value;
  }

  function loginFromHar(har) {
    var found = null;
    ((har.log || {}).entries || []).forEach(function (entry) {
      var request = entry.request || {};
      var url = String(request.url || ""), posted = String((request.postData || {}).text || "");
      var query = url.indexOf("?") >= 0 ? url.slice(url.indexOf("?") + 1) : "";
      if (!INIT_CALL.test(query) && !INIT_CALL.test(posted)) return;
      var content = (entry.response || {}).content || {};
      var text = content.text || "";
      try {
        if (content.encoding === "base64") text = atob(text);
        var body = JSON.parse(text);
        if (body && typeof body.user_data === "object") found = body;
      } catch (e) { /* not this one */ }
    });
    if (!found) throw new Error("That recording has no login data in it. Reload the game with the Network tab open, then save it again.");
    return found;
  }

  /* The record to send for the chosen file, or null with none. */
  function readRecord() {
    var file = $("record").files[0];
    if (!file || info.imports === "off") return Promise.resolve(null);
    return file.text().then(function (text) {
      var data;
      try { data = JSON.parse(text); } catch (e) { throw new Error("That file isn't a save or a recording (not JSON)."); }
      if (data && data.log && data.log.entries) return withoutSecrets(loginFromHar(data));
      if (data && data.player && data.version) return data;
      if (data && typeof data.user_data === "object") return withoutSecrets(data);
      throw new Error("That file is neither a save from a server like this one nor the game's login data.");
    });
  }

  fetch("/account", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      info = data;
      if (info.single_player) { location.href = "/"; return; }
      show();
    })
    .catch(function () { say("The server didn't answer. Is it running?", "error"); });

  $("play").addEventListener("click", function () { location.href = "/"; });
  $("sign-out").addEventListener("click", function () {
    post("/account/sign-out").then(function () { info.name = null; location.reload(); });
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var name = $("name").value.trim();
    if (!name) { say("Type a name.", "error"); return; }
    var body = { name: name };
    if (info.passwords) body.password = $("password").value;
    submit.disabled = true;
    say("Signing in...");
    readRecord()
      .then(function (record) {
        if (record) body.record = record;
        return post("/account/sign-in", body);
      })
      .then(function (reply) {
        if (!reply.ok) { say(reply.message, "error"); return; }
        var lines = [(reply["new"] ? "Welcome, " : "Welcome back, ") + reply.name + "."];
        if (reply["import"] === "loaded") lines.push("Your progress is loaded.");
        if (reply["import"] === "waiting") lines.push("Your progress waits for the host's OK; until then you start fresh, and it replaces that save once accepted.");
        (reply.notes || []).forEach(function (n) { lines.push(n); });
        lines.push("Starting the game...");
        say(lines.join(" "), "ok");
        setTimeout(function () { location.href = "/"; }, reply["import"] ? 3500 : 600);
      })
      .catch(function (e) { say(e && e.message ? e.message : "The server didn't answer. Try again.", "error"); })
      .then(function () { submit.disabled = false; });
  });
})();
