/* Offline mode: send the game's network requests to the local server.
 *
 * Loaded first on the page by the offline server. The game asks for
 * https://mobile.tyrantonline.com/... (API and data XMLs) and
 * https://cdn.synapsegames.com/... (art bundles, images); every such address
 * becomes /tu/<host>/<path> on this server. The dev and staging API hosts the
 * client also knows about go to the same place. Anything else is left alone and
 * stopped by the page's Content-Security-Policy, which reports it to the server.
 */
(function () {
  "use strict";

  // The host must end right there (port, path, query or end of string), so look-alikes
  // such as cdn.synapsegames.com.example.net are left alone.
  var GAME_URL = /^(?:https?:)?\/\/(mobile(?:-dev|-staging)?\.tyrantonline\.com|cdn\.synapsegames\.com)(?::\d+)?(?=[\/?#]|$)(\/[^?#]*)?(\?[^#]*)?/i;

  function rewrite(url) {
    var s = String(url);
    var m = GAME_URL.exec(s);
    if (!m) return s;
    var host = m[1].toLowerCase();
    if (/tyrantonline\.com$/.test(host)) host = "mobile.tyrantonline.com";
    return "/tu/" + host + (m[2] || "/") + (m[3] || "");
  }

  window.__tuOffline = { rewrite: rewrite, rewritten: 0 };

  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    var args = Array.prototype.slice.call(arguments);
    var local = rewrite(url);
    if (local !== String(url)) window.__tuOffline.rewritten++;
    args[1] = local;
    return origOpen.apply(this, args);
  };

  if (typeof window.fetch === "function") {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      if (typeof input === "string") {
        input = rewrite(input);
      } else if (input && input.url && rewrite(input.url) !== input.url) {
        input = new Request(rewrite(input.url), input);
      }
      return origFetch.call(this, input, init);
    };
  }
})();
