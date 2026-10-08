// Runs in the page's MAIN world at document_start, before Teams boots.
// Wraps the Notification API so every toast Teams raises is mirrored to the
// extension. The original call is always forwarded untouched, so the normal
// Windows/browser notification still appears.
(() => {
  "use strict";

  const CHANNEL = "__teams_to_ntfy__";

  if (window[CHANNEL]) return;
  window[CHANNEL] = true;

  function emit(source, title, options) {
    try {
      const opts = options || {};
      window.postMessage(
        {
          __teamsToNtfy: true,
          source,
          title: typeof title === "string" ? title : String(title ?? ""),
          body: typeof opts.body === "string" ? opts.body : "",
          tag: typeof opts.tag === "string" ? opts.tag : "",
          origin: location.origin,
          at: Date.now(),
        },
        location.origin
      );
    } catch (_) {
      // Never let instrumentation break the page.
    }
  }

  // --- new Notification(title, options) -------------------------------------
  const NativeNotification = window.Notification;
  if (typeof NativeNotification === "function") {
    // Native statics such as requestPermission brand-check `this`, which would
    // be the proxy and raise "Illegal invocation". Hand out bound copies, and
    // cache them so repeated reads stay reference-equal.
    const boundStatics = new Map();

    // A Proxy keeps every static (permission, requestPermission, maxActions)
    // and the prototype chain intact, so instanceof and feature detection in
    // Teams keep working.
    window.Notification = new Proxy(NativeNotification, {
      construct(target, args) {
        emit("Notification", args[0], args[1]);
        return Reflect.construct(target, args);
      },
      get(target, prop) {
        // Read with `this` = the native constructor: static accessors such as
        // Notification.permission brand-check their receiver and would throw
        // "Illegal invocation" if handed the proxy.
        const value = Reflect.get(target, prop, target);
        if (typeof value !== "function") return value;
        if (!boundStatics.has(prop)) boundStatics.set(prop, value.bind(target));
        return boundStatics.get(prop);
      },
    });
  }

  // --- registration.showNotification(title, options) -------------------------
  const swProto = window.ServiceWorkerRegistration && window.ServiceWorkerRegistration.prototype;
  if (swProto && typeof swProto.showNotification === "function") {
    const nativeShow = swProto.showNotification;
    swProto.showNotification = function (title, options) {
      emit("showNotification", title, options);
      return nativeShow.apply(this, arguments);
    };
  }
})();
