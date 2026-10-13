/* 平湖精彩贸易 · 页面脚本
   >>>compose … <<<compose 之间的函数是纯函数，改完用同名的 .test.html 校验 */
(function () {
  'use strict';

  var yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // >>>compose
  function composeReport(d) {
    var lines = ['【羽绒服报货】'];
    lines.push('称呼：' + d.name);
    lines.push('联系方式：' + d.contact);
    lines.push('货品类型：' + d.kind);
    lines.push('数量：' + (d.qty ? d.qty + ' 件' : '待确认'));
    lines.push('含绒量：' + (d.down || '不清楚'));
    lines.push('货在哪里：' + (d.where || '待确认'));
    if (d.note) lines.push('补充：' + d.note);
    lines.push('（麻烦回个价格区间，谢谢）');
    return lines.join('\n');
  }

  function validateReport(d) {
    var errors = {};
    if (!d.name.trim()) errors.name = '请填一下称呼';
    if (!d.contact.trim()) errors.contact = '请留个能联系上的电话或微信';
    if (d.qty.trim() && !/^\d+$/.test(d.qty.trim())) errors.qty = '数量只填数字，不清楚可以留空';
    return errors;
  }
  // <<<compose

  // 导出给同源的自检页用
  window.__jcmy = { composeReport: composeReport, validateReport: validateReport };

  function initForm(form) {
    var out = form.querySelector('.out');
    var outText = out ? out.querySelector('textarea') : null;
    var copyBtn = out ? out.querySelector('[data-copy]') : null;
    var copyMsg = out ? out.querySelector('[data-copy-msg]') : null;
    if (!out || !outText) return;

    function clearErrors() {
      form.querySelectorAll('[data-err]').forEach(function (p) { p.textContent = ''; });
      form.querySelectorAll('[aria-invalid]').forEach(function (i) { i.removeAttribute('aria-invalid'); });
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      clearErrors();
      var d = {
        name: form.elements.name.value,
        contact: form.elements.contact.value,
        kind: form.elements.kind.value,
        qty: form.elements.qty.value,
        down: form.elements.down.value.trim(),
        where: form.elements.where.value.trim(),
        note: form.elements.note.value.trim()
      };
      var errors = validateReport(d);
      var keys = Object.keys(errors);
      if (keys.length) {
        keys.forEach(function (k) {
          var p = form.querySelector('[data-err="' + k + '"]');
          if (p) p.textContent = errors[k];
          var input = form.elements[k];
          if (input) input.setAttribute('aria-invalid', 'true');
        });
        form.elements[keys[0]].focus();
        return;
      }
      outText.value = composeReport(d);
      out.dataset.open = 'true';
      if (copyMsg) copyMsg.textContent = '';
      outText.focus();
      outText.select();
    });

    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        outText.select();
        var ok = false;
        try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
        function say(t) { if (copyMsg) copyMsg.textContent = t; }
        if (navigator.clipboard) {
          navigator.clipboard.writeText(outText.value).then(function () {
            say('已复制');
          }, function () {
            say(ok ? '已复制' : '复制失败，请手动选中复制');
          });
        } else {
          say(ok ? '已复制' : '复制失败，请手动选中复制');
        }
      });
    }
  }

  document.querySelectorAll('form[data-quote]').forEach(initForm);
})();
