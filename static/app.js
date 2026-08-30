/* ===========================================================
   FCA · 交互引擎（简洁版）
   仅保留功能性交互：CSRF 注入、数字计数、判题遮罩。
   无鼠标跟随 / 视差 / 开场动画等装饰逻辑。
   =========================================================== */
(function () {
  'use strict';
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- CSRF：为所有 POST 表单自动注入 csrf_token ---------- */
  (function () {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (!meta) return;
    var token = meta.getAttribute('content');
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (!form || !form.method) return;
      if (form.method.toLowerCase() !== 'post') return;
      if (form.querySelector('input[name="csrf_token"]')) return;
      var inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = 'csrf_token';
      inp.value = token;
      form.appendChild(inp);
    });
  })();

  /* ---------- 数字计数器跳动（[data-count]） ---------- */
  function animateCount(el) {
    var target = parseFloat(el.getAttribute('data-count')) || 0;
    var dec = (el.getAttribute('data-dec') || '0');
    var dur = 1000, start = null;
    function step(now) {
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / dur);
      var e = 1 - Math.pow(1 - t, 4);
      el.textContent = (target * e).toFixed(dec);
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = target.toFixed(dec);
    }
    requestAnimationFrame(step);
  }
  if (!reduce && 'IntersectionObserver' in window) {
    var cio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { animateCount(en.target); cio.unobserve(en.target); }
      });
    }, { threshold: 0.5 });
    document.querySelectorAll('[data-count]').forEach(function (n) { cio.observe(n); });
  }

  /* ---------- 提交判题「正在测评」指示 ---------- */
  window.showJudging = function () {
    var el = document.getElementById('judging');
    if (!el) return;
    if (reduce) { el.classList.add('show'); return; }
    el.classList.remove('show');
    setTimeout(function () { el.classList.add('show'); }, 350);
  };
  window.hideJudging = function () {
    var el = document.getElementById('judging');
    if (el) el.classList.remove('show');
  };
})();
