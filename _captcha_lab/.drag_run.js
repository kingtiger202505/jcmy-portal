(function () {
  var btn = document.querySelector('.geetest_slider_button');
  if (!btn) return 'no-button';
  var r = btn.getBoundingClientRect();
  var sx = r.left + r.width / 2, sy = r.top + r.height / 2;
  var DIST = 133;
  function ev(type, x, y) {
    btn.dispatchEvent(new MouseEvent(type, {
      bubbles: true, cancelable: true, view: window,
      clientX: x, clientY: y, button: 0, buttons: type === 'mouseup' ? 0 : 1
    }));
  }
  ev('mousedown', sx, sy);
  var N = 42, i = 0;
  function step() {
    i++;
    var t = i / N;
    var p = 1 - Math.pow(1 - t, 2.2);                    // 先快后慢
    var over = t > 0.93 ? Math.sin((t - 0.93) / 0.07 * Math.PI) * 2.5 : 0;  // 末端微过冲再回正
    var x = sx + DIST * p + over;
    var y = sy + (Math.random() - 0.5) * 2;
    ev('mousemove', x, y);
    if (i < N) setTimeout(step, 16 + Math.random() * 22);
    else ev('mouseup', x, y);
  }
  setTimeout(step, 40);
  return 'drag dist=' + DIST + ' start=(' + Math.round(sx) + ',' + Math.round(sy) + ')';
})()