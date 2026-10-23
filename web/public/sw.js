// Service worker for desktop alerts. Shows what the notifier pushed and opens the disruption
// on click. Nothing else.

/** Path on this origin, falling back to "/". Stops a crafted push from opening another site. */
function samePage(value) {
  try {
    const url = new URL(typeof value === "string" ? value : "/", self.location.origin);
    return url.origin === self.location.origin ? url.pathname + url.search : "/";
  } catch {
    return "/";
  }
}

// claim tabs opened before install, otherwise a click can't focus them
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  const url = samePage(data.url);
  event.waitUntil(
    self.registration.showNotification(data.title || "CrewOps", {
      body: data.body || "A disruption needs you.",
      tag: data.tag || undefined,
      renotify: Boolean(data.tag),
      requireInteraction: true,
      data: { url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(samePage(event.notification.data?.url), self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((open) => {
      // never a desk speaker's tab: navigating it away would silence that desk's alerts
      const tab = open.find((c) => {
        const at = new URL(c.url);
        return at.origin === self.location.origin && at.pathname !== "/speaker" && !at.pathname.startsWith("/speaker/");
      });
      if (tab) {
        return tab
          .focus()
          .then(() => tab.navigate(url))
          .catch(() => self.clients.openWindow(url));
      }
      return self.clients.openWindow(url);
    }),
  );
});
