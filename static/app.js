/* ============================================================
   FCA · 交互引擎（Dune × Apple Glass）
   全部动画走 requestAnimationFrame / IntersectionObserver，
   保证 60fps；不依赖任何外部库；尊重触屏与 reduced-motion。
   ============================================================ */
(function () {
  'use strict';
  var root = document.documentElement;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  var motion = fine && !reduce;

  /* ---------- 1. 自定义光标 + 鼠标跟随软光晕（rAF 缓动） ---------- */
  var cursor = document.getElementById('cursor');
  var glow = document.getElementById('glow');
  var parallaxEls = Array.prototype.slice.call(document.querySelectorAll('[data-depth]'));

  if (motion && cursor) {
    var tmx = window.innerWidth / 2, tmy = window.innerHeight / 2; // 目标（精确）
    var cmx = tmx, cmy = tmy;                                       // 光标缓动
    var gx = tmx, gy = tmy;                                         // 光晕缓动

    window.addEventListener('mousemove', function (e) {
      tmx = e.clientX; tmy = e.clientY;
    }, { passive: true });

    function frame() {
      cmx += (tmx - cmx) * 0.20;
      cmy += (tmy - cmy) * 0.20;
      gx += (tmx - gx) * 0.08;
      gy += (tmy - gy) * 0.08;

      root.style.setProperty('--mx', tmx + 'px');
      root.style.setProperty('--my', tmy + 'px');
      root.style.setProperty('--cx', cmx.toFixed(1) + 'px');
      root.style.setProperty('--cy', cmy.toFixed(1) + 'px');
      root.style.setProperty('--gx', gx.toFixed(1) + 'px');
      root.style.setProperty('--gy', gy.toFixed(1) + 'px');

      // 视差景深：多层以不同深度跟随鼠标
      for (var i = 0; i < parallaxEls.length; i++) {
        var el = parallaxEls[i];
        var d = parseFloat(el.getAttribute('data-depth')) || 0;
        var dx = (tmx - window.innerWidth / 2) * d;
        var dy = (tmy - window.innerHeight / 2) * d;
        el.style.transform = 'translate3d(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px,0)';
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);

    // 悬停可点击元素：光标放大变色
    document.addEventListener('mouseover', function (e) {
      if (e.target.closest('a,button,.btn,input,select,textarea,[data-cursor]')) {
        cursor.classList.add('hover');
      }
    });
    document.addEventListener('mouseout', function (e) {
      if (e.target.closest('a,button,.btn,input,select,textarea,[data-cursor]')) {
        cursor.classList.remove('hover');
      }
    });
    root.classList.add('cursor-on');
  }

  /* ---------- 2. 滚动叙事：进入视口从 opacity:0 + y:80 → power4.out 出现 ---------- */
  var REVEAL_SEL = '.card,.panel,.pcard,.scard,.stat,.dash,.section-title,.point-card,.lastsub,.code-panel,.table-wrap,.rank-card,.reveal-item';
  var io = null;

  if (!reduce && 'IntersectionObserver' in window) {
    io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          tweenIn(en.target);
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });

    var nodes = document.querySelectorAll(REVEAL_SEL);
    for (var j = 0; j < nodes.length; j++) {
      nodes[j].classList.add('reveal');
      io.observe(nodes[j]);
    }
  }

  function tweenIn(el) {
    var dur = 900, start = null;
    function step(now) {
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / dur);
      var e = 1 - Math.pow(1 - t, 4); // power4.out
      var y = (80 * (1 - e)).toFixed(2);
      el.style.opacity = (e).toFixed(3);
      el.style.transform = 'translate3d(0,' + y + 'px,0)';
      if (t < 1) {
        requestAnimationFrame(step);
      } else {
        el.style.opacity = '';
        el.style.transform = '';
        el.classList.remove('reveal');
      }
    }
    requestAnimationFrame(step);
  }

  /* ---------- 3. 数字计数器跳动（用于统计/排名等 [data-count]） ---------- */
  function animateCount(el) {
    var target = parseFloat(el.getAttribute('data-count')) || 0;
    var dec = (el.getAttribute('data-dec') || '0');
    var dur = 1100, start = null;
    function step(now) {
      if (start === null) start = now;
      var t = Math.min(1, (now - start) / dur);
      var e = 1 - Math.pow(1 - t, 4);
      var val = (target * e).toFixed(dec);
      el.textContent = val;
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

  /* ---------- 4. 预加载结束后允许交互（纯 CSS 已保证消失，这里仅清理指针） ---------- */
  var pre = document.getElementById('preloader');
  if (pre) {
    pre.addEventListener('animationend', function () { pre.style.display = 'none'; });
    // 兜底：2.5s 后强制隐藏
    setTimeout(function () { if (pre) pre.style.display = 'none'; }, 2500);
  }

  /* ---------- 5. 提交判题「正在测评」指示 ---------- */
  // 延迟出现，避免快速判题时一闪而过；reduced-motion 下直接显示（无旋转动画）。
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
