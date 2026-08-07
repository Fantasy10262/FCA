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

  /* ---------- 1. 已移除自定义光标与鼠标视差（恢复原生光标，去除 AI 套壳感） ---------- */

  /* ---------- 1b. 极光背景随鼠标轻微视差（不隐藏原生光标） ---------- */
  var aurora = document.querySelector('.aurora');
  if (aurora && fine) {
    var ax = 0, ay = 0, tx = 0, ty = 0, raf = null;
    window.addEventListener('mousemove', function (e) {
      tx = (e.clientX / window.innerWidth - 0.5) * 2;   // -1 ~ 1
      ty = (e.clientY / window.innerHeight - 0.5) * 2;
      root.style.setProperty('--mx', e.clientX + 'px');
      root.style.setProperty('--my', e.clientY + 'px');
      if (!raf) raf = requestAnimationFrame(step);
    }, { passive: true });
    function step() {
      ax += (tx - ax) * 0.06; ay += (ty - ay) * 0.06;
      root.style.setProperty('--ax', ax.toFixed(3));
      root.style.setProperty('--ay', ay.toFixed(3));
      if (Math.abs(tx - ax) > 0.001 || Math.abs(ty - ay) > 0.001) raf = requestAnimationFrame(step);
      else raf = null;
    }
  }

  /* ---------- 1c. 文字景深视差：少量文字随鼠标小幅位移（不隐藏原生光标） ---------- */
  var depthEls = document.querySelectorAll('[data-depth]');
  if (depthEls.length && motion) {
    var ddx = 0, ddy = 0, dtx = 0, dty = 0, rafD = null;
    window.addEventListener('mousemove', function (e) {
      dtx = (e.clientX / window.innerWidth - 0.5) * 2;   // -1 ~ 1
      dty = (e.clientY / window.innerHeight - 0.5) * 2;
      if (!rafD) rafD = requestAnimationFrame(stepDepth);
    }, { passive: true });
    function stepDepth() {
      ddx += (dtx - ddx) * 0.08; ddy += (dty - ddy) * 0.08;
      for (var i = 0; i < depthEls.length; i++) {
        var d = parseFloat(depthEls[i].getAttribute('data-depth')) || 0;
        depthEls[i].style.transform = 'translate3d(' + (ddx * d).toFixed(2) + 'px,' + (ddy * d).toFixed(2) + 'px,0)';
      }
      if (Math.abs(dtx - ddx) > 0.001 || Math.abs(dty - ddy) > 0.001) rafD = requestAnimationFrame(stepDepth);
      else rafD = null;
    }
  }

  /* ---------- 1d. 卡片指针聚光（high-tech 鼠标跟随高光，不隐藏原生光标） ---------- */
  var spots = document.querySelectorAll('.pcard, .scard, .stat, .card, .panel');
  if (spots.length && motion) {
    Array.prototype.forEach.call(spots, function (el) {
      el.addEventListener('mousemove', function (e) {
        var r = el.getBoundingClientRect();
        el.style.setProperty('--cx', (((e.clientX - r.left) / r.width) * 100).toFixed(1) + '%');
        el.style.setProperty('--cy', (((e.clientY - r.top) / r.height) * 100).toFixed(1) + '%');
      }, { passive: true });
    });
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
