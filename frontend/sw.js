// sw.js - Renewly service worker. Receives Web Push messages sent by the Renewly Cloud
// backend and shows them as system notifications, even when the Renewly page is closed.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "Renewly";
  const options = {
    body: data.body || data.message || "You have a renewal reminder.",
    tag: data.tag || "renewly-reminder",
    renotify: Boolean(data.tag),
    data: { url: data.url || "/" },
  };
  // Browsers require a visible notification for every push (userVisibleOnly).
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if ("focus" in client) return client.focus();
    }
    // The page needs the per-launch token from the desktop app, so a fresh tab can only
    // show the "open from Renewly" hint; this still brings the user back to the right place.
    if (self.clients.openWindow) return self.clients.openWindow(target);
  })());
});
