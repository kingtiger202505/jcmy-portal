(function () {
  try {
    var d = Object.getOwnPropertyDescriptor(HTMLScriptElement.prototype, 'src');
    var setter = d && d.set;
    if (!setter) return 'no-setter';
    window.__gt_cbs = [];
    Object.defineProperty(HTMLScriptElement.prototype, 'src', {
      configurable: true,
      get: function () { return d.get.call(this); },
      set: function (v) {
        var url = String(v);
        try {
          var m = /[?&]callback=([^&]+)/.exec(url);
          if (m) {
            var cb = decodeURIComponent(m[1]);
            var cur = window[cb];
            window[cb] = function (data) {
              try {
                window.__gt_cbs.push({ u: url.slice(0, 120), d: JSON.stringify(data).slice(0, 400) });
              } catch (e) { }
              if (typeof cur === 'function') return cur.apply(this, arguments);
            };
          }
        } catch (e) { }
        setter.call(this, v);
      }
    });
    window.__gt_hooked = true;
    return 'hooked';
  } catch (e) { return 'err:' + e.message; }
})()