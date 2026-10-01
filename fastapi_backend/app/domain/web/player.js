/* Archive player: audio + synced transcript + index. Used by the app, /embed/<id> and reports.
   ArchivePlayer.mount(element, data, {start: seconds, messages: bool, media: an existing <video> to drive}) -> {seek(ms, play), audio} */
(function () {
  'use strict';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tc = ms => { const s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0'); };
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  function mount(root, d, opts) {
    opts = opts || {};
    const L = d.labels || { palette: {}, emoji: {}, events: {} };
    const segs = d.segments || [], spk = {};
    (d.speakers || []).forEach(s => { spk[s.key] = s; });
    const dur = Math.max(d.duration_ms || 0, segs.length ? segs[segs.length - 1].t1 : 0) || 1;
    const src = d.audio || (location.protocol === 'file:' ? d.audio_local : (d.audio_api || d.audio_local)) || null;
    const secAt = new Map((d.sections || []).map((s, k) => [s.seg0, k]));
    root.classList.add('ap');
    root.innerHTML =
      '<div class="ap-top"><button class="ap-play" aria-label="Play">▶</button>' +
      '<span class="ap-clock"><span class="ap-cur">0:00</span> / ' + tc(dur) + '</span>' +
      '<select class="ap-rate" aria-label="Playback speed">' + [0.75, 1, 1.25, 1.5, 1.75, 2].map(r => '<option value="' + r + '"' + (r === 1 ? ' selected' : '') + '>' + r + '×</option>').join('') + '</select>' +
      '<span class="ap-now" aria-live="polite"></span></div>' +
      '<div class="ap-secs"></div><div class="ap-marks"></div>' +
      '<div class="ap-rail" tabindex="0" role="slider" aria-label="Position in the recording" aria-valuemin="0" aria-valuemax="' + Math.round(dur / 1000) + '" aria-valuenow="0"><canvas></canvas><div class="ap-tip" hidden></div></div>' +
      '<div class="ap-legend">' + (d.speakers || []).map(s => '<span><i style="background:' + s.color + '"></i>' + esc(s.name) + '</span>').join('') + '</div>' +
      '<div class="ap-tabs" role="tablist"><button role="tab" data-tab="t" aria-selected="true">Transcript</button><button role="tab" data-tab="i" aria-selected="false">Index</button>' +
      '<input class="ap-find" type="search" placeholder="Find in this recording" aria-label="Find in this recording"><span class="ap-found"></span>' +
      '<label class="ap-follow"><input type="checkbox" checked> Follow</label></div>' +
      '<div class="ap-body"><div class="ap-tx" role="tabpanel"></div><div class="ap-ix" role="tabpanel" hidden></div></div>' + (opts.media ? '' : '<audio preload="metadata"></audio>');
    const $ = s => root.querySelector(s);
    const audio = opts.media || $('audio'), rail = $('.ap-rail'), cv = rail.querySelector('canvas'), tip = rail.querySelector('.ap-tip');
    const body = $('.ap-body'), tx = $('.ap-tx'), ix = $('.ap-ix'), playBtn = $('.ap-play');
    let now = 0, active = -1, hits = [], hitAt = -1, userScroll = 0, hover = null;
    if (opts.media) { /* the caller set the source */ } else if (src) audio.src = src; else { playBtn.disabled = true; $('.ap-now').textContent = 'No audio here; the transcript still works.'; }

    const nameOf = k => (spk[k] && spk[k].name) || 'Unattributed';
    const colorOf = k => (spk[k] && spk[k].color) || '#9AA5B1';
    const markOf = s => (s.e && s.e !== 'Neutral' && L.emoji[s.e] ? '<span class="ap-emo" title="' + esc(s.e) + '">' + L.emoji[s.e] + '</span>' : '') +
      (s.v && L.events[s.v] ? '<span class="ap-emo" title="' + esc(s.v) + '">' + L.events[s.v] + '</span>' : '');

    // transcript: consecutive segments by one speaker form a turn; sections start new turns
    const html = [];
    let open = null;
    segs.forEach((s, i) => {
      const gap = i && s.t0 - segs[i - 1].t1 > 4000;
      if (secAt.has(i)) {
        if (open) { html.push('</p></div>'); open = null; }
        const k = secAt.get(i), sec = d.sections[k];
        html.push('<h4 class="ap-sec" data-t="' + sec.t0 + '"><span>' + (k + 1) + '</span>' + esc(sec.title) + '<time>' + tc(sec.t0) + '</time></h4>');
      }
      if (!open || open !== (s.s || '-') || gap) {
        if (open) html.push('</p></div>');
        open = s.s || '-';
        html.push('<div class="ap-turn" style="--sc:' + colorOf(s.s) + '"><div class="ap-who"><b>' + esc(nameOf(s.s)) + '</b><a href="#" class="ap-t" data-i="' + i + '">' + tc(s.t0) + '</a></div><p>');
      }
      html.push('<span class="ap-seg" data-i="' + i + '">' + markOf(s) + '<span class="ap-txt">' + esc(s.text) + '</span></span> ');
    });
    if (open) html.push('</p></div>');
    tx.innerHTML = html.join('') || '<p class="ap-empty">No transcript yet.</p>';
    const spans = Array.from(tx.querySelectorAll('.ap-seg'));

    // index: sections, speakers, named things, keywords, summary
    const talk = {};
    segs.forEach(s => { talk[s.s || '-'] = (talk[s.s || '-'] || 0) + (s.t1 - s.t0); });
    const tmax = Math.max(1, ...Object.values(talk));
    const sm = d.summary;
    // a summary item: a string (older summaries) or {text, who, t0}; one with a time plays from there
    const listItem = a => {
      const o = typeof a === 'string' ? { text: a } : (a || {});
      const who = o.who ? ' (' + esc(o.who) + ')' : '';
      return '<li>' + (o.t0 != null ? '<a href="#" data-t="' + (+o.t0) + '">' + esc(o.text || '') + '</a>' + who + '<time>' + tc(o.t0) + '</time>' : esc(o.text || '') + who) + '</li>';
    };
    ix.innerHTML = '<div class="ap-ixgrid">' +
      (sm ? '<section class="ap-wide"><h4>Summary</h4><p>' + esc(sm.summary) + '</p>' + [['key_points', 'Key points'], ['action_items', 'Follow-ups']].map(([k, title]) => (sm[k] || []).length ? '<h5>' + title + '</h5><ul class="ap-list">' + sm[k].map(listItem).join('') + '</ul>' : '').join('') + '</section>' : '') +
      '<section><h4>Sections</h4><ol class="ap-list">' + (d.sections || []).map(s => '<li><a href="#" data-t="' + s.t0 + '">' + esc(s.title) + '</a><time>' + tc(s.t0) + '</time></li>').join('') + '</ol></section>' +
      '<section><h4>Speakers</h4><p class="ap-hint">Select a name to jump to their next turn.</p>' + Object.keys(talk).sort((a, b) => talk[b] - talk[a]).map(k =>
        '<button class="ap-spk" data-s="' + esc(k) + '"><span><i style="background:' + colorOf(k === '-' ? null : k) + '"></i>' + esc(k === '-' ? 'Unattributed' : nameOf(k)) + '</span><b style="width:' + (100 * talk[k] / tmax).toFixed(1) + '%;background:' + colorOf(k === '-' ? null : k) + '"></b><small>' + tc(talk[k]) + '</small></button>').join('') + '</section>' +
      '<section><h4>Named things</h4><div class="ap-chips">' + (d.entities || []).map((e, k) => '<button data-e="' + k + '" title="' + esc(e.type.toLowerCase()) + '">' + esc(e.name) + '<small>' + e.segs.length + '</small></button>').join('') + '</div><div class="ap-ment"></div></section>' +
      '<section><h4>Keywords</h4><div class="ap-chips">' + (d.keywords || []).map(w => '<button data-k="' + esc(w[0]) + '">' + esc(w[0]) + '</button>').join('') + '</div></section></div>';

    // sections band and emotion/event marks above the rail
    $('.ap-secs').innerHTML = (d.sections || []).map((s, k) => '<button class="ap-sb" data-t="' + s.t0 + '" style="left:' + (100 * s.t0 / dur).toFixed(3) + '%;width:' + Math.max(0.4, 100 * (s.t1 - s.t0) / dur - 0.25).toFixed(3) + '%" title="' + esc((k + 1) + '. ' + s.title) + '"><b>' + (k + 1) + '</b> ' + esc(s.title) + '</button>').join('');
    function marks() {
      const w = rail.clientWidth || 600, out = [];
      let last = -99;
      segs.forEach((s, i) => {
        const g = (s.e && s.e !== 'Neutral' && L.emoji[s.e]) || (s.v && L.events[s.v]);
        if (!g) return;
        const x = s.t0 / dur * w;
        if (x - last < 16) return;
        last = x;
        out.push('<button class="ap-mk" data-i="' + i + '" style="left:' + x.toFixed(1) + 'px" title="' + esc((s.e && s.e !== 'Neutral' ? s.e : s.v) + ' at ' + tc(s.t0)) + '">' + g + '</button>');
      });
      $('.ap-marks').innerHTML = out.join('');
    }

    function segAt(t) {
      let lo = 0, hi = segs.length - 1, r = -1;
      while (lo <= hi) { const m = (lo + hi) >> 1; if (segs[m].t0 <= t) { r = m; lo = m + 1; } else hi = m - 1; }
      return r;
    }
    const css = v => getComputedStyle(root).getPropertyValue(v).trim();
    function draw() {
      const w = rail.clientWidth, h = rail.clientHeight, dpr = Math.min(2, window.devicePixelRatio || 1);
      if (!w || !h) return;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
      const ctx = cv.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const quiet = css('--ap-quiet') || '#C3CBD3', here = css('--ap-here') || '#FF5A1F', px = now / dur * w, env = d.envelope, mid = h - 3;
      for (let x = 0; x < w; x += 2) {
        const t = (x + 1) / w * dur, i = segAt(t), on = i >= 0 && t < segs[i].t1;
        let v;
        if (env && env.length) {
          const a = Math.floor(x / w * env.length), b = Math.max(a + 1, Math.floor((x + 2) / w * env.length));
          v = 0; for (let j = a; j < b && j < env.length; j++) v = Math.max(v, env[j]);
          v = v / 255;
        } else v = on ? 0.55 : 0.06;
        const bh = Math.max(1.5, v * (h - 8));
        ctx.globalAlpha = x <= px ? 1 : 0.5;
        ctx.fillStyle = on ? colorOf(segs[i].s) : quiet;
        ctx.fillRect(x, mid - bh, 1.4, bh);
      }
      ctx.globalAlpha = 1;
      ctx.fillStyle = here;
      for (const i of hits) ctx.fillRect(segs[i].t0 / dur * w, 0, 2, 5);
      ctx.fillRect(Math.max(0, px - 1), 0, 2, h);
      if (hover != null) { ctx.globalAlpha = 0.5; ctx.fillStyle = css('--ap-ink') || '#15202B'; ctx.fillRect(hover, 0, 1, h); ctx.globalAlpha = 1; }
    }

    function update(scrollIt) {
      $('.ap-cur').textContent = tc(now);
      rail.setAttribute('aria-valuenow', Math.round(now / 1000));
      const i = segAt(now);
      if (i !== active) {
        if (active >= 0 && spans[active]) spans[active].classList.remove('on');
        active = i;
        if (i >= 0 && spans[i]) {
          spans[i].classList.add('on');
          const follow = $('.ap-follow input').checked && Date.now() - userScroll > 5000;
          if ((follow || scrollIt) && !ix.hidden === false) {
            const r = spans[i].getBoundingClientRect(), br = body.getBoundingClientRect();
            if (r.top < br.top + 20 || r.bottom > br.bottom - 20) body.scrollTo({ top: body.scrollTop + r.top - br.top - br.height * 0.3, behavior: reduce ? 'auto' : 'smooth' });
          }
        }
      }
      draw();
    }
    function seek(ms, play) {
      now = Math.max(0, Math.min(dur, ms));
      if (src) { try { audio.currentTime = now / 1000; } catch (e) { /* metadata not loaded yet */ } if (play) audio.play().catch(() => {}); }
      update(true);
    }
    let raf = 0;
    const tick = () => { now = audio.currentTime * 1000; update(false); if (!audio.paused) raf = requestAnimationFrame(tick); };
    audio.addEventListener('play', () => { playBtn.textContent = '❚❚'; playBtn.setAttribute('aria-label', 'Pause'); cancelAnimationFrame(raf); raf = requestAnimationFrame(tick); });
    audio.addEventListener('pause', () => { playBtn.textContent = '▶'; playBtn.setAttribute('aria-label', 'Play'); });
    audio.addEventListener('timeupdate', () => { if (audio.paused) { now = audio.currentTime * 1000; update(false); } });
    audio.addEventListener('error', () => { if (src || opts.media) { playBtn.disabled = true; $('.ap-now').textContent = 'The ' + (opts.media ? 'video' : 'audio') + ' could not be loaded; the transcript still works.'; } });
    audio.addEventListener('loadedmetadata', () => { if (opts.start) seek(opts.start * 1000); });
    playBtn.addEventListener('click', () => { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); });
    $('.ap-rate').addEventListener('change', e => { audio.playbackRate = +e.target.value; });

    const railT = e => { const r = rail.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur; };
    let drag = false;
    rail.addEventListener('pointerdown', e => { drag = true; rail.setPointerCapture(e.pointerId); seek(railT(e)); });
    rail.addEventListener('pointermove', e => {
      const r = rail.getBoundingClientRect();
      if (drag) { seek(railT(e)); return; }
      if (e.pointerType !== 'mouse') return;
      hover = e.clientX - r.left;
      const t = railT(e), i = segAt(t);
      tip.hidden = false;
      tip.innerHTML = '<b>' + tc(t) + '</b>' + (i >= 0 && t < segs[i].t1 ? ' ' + esc(nameOf(segs[i].s)) + '<br>' + esc(segs[i].text.slice(0, 120)) + (segs[i].text.length > 120 ? '…' : '') : '');
      tip.style.left = Math.max(0, Math.min(r.width - tip.offsetWidth, hover - tip.offsetWidth / 2)) + 'px';
      draw();
    });
    rail.addEventListener('pointerup', () => { drag = false; });
    rail.addEventListener('pointerleave', () => { hover = null; tip.hidden = true; draw(); });
    rail.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); seek(now + (e.key === 'ArrowRight' ? 5000 : -5000)); }
    });
    root.addEventListener('keydown', e => {
      if (e.target.closest('input, textarea, select')) return;
      if (e.key === ' ' || e.key === 'k') { e.preventDefault(); if (src) playBtn.click(); }
      else if (e.key === 'j' || e.key === 'l') seek(now + (e.key === 'l' ? 10000 : -10000));
    });
    body.addEventListener('wheel', () => { userScroll = Date.now(); }, { passive: true });
    body.addEventListener('touchmove', () => { userScroll = Date.now(); }, { passive: true });
    root.addEventListener('click', e => {
      const t = e.target.closest('[data-t]'), sp = e.target.closest('.ap-seg'), tl = e.target.closest('.ap-t'), mk = e.target.closest('.ap-mk');
      if (e.target.closest('.ap-tabs button[data-tab]')) {
        const tab = e.target.closest('button').dataset.tab;
        root.querySelectorAll('.ap-tabs button[data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
        tx.hidden = tab !== 't'; ix.hidden = tab !== 'i';
        return;
      }
      if (t && !sp) { e.preventDefault(); seek(+t.dataset.t, !audio.paused); return; }
      if (tl) { e.preventDefault(); seek(segs[+tl.dataset.i].t0, !audio.paused); return; }
      if (mk) { seek(segs[+mk.dataset.i].t0, !audio.paused); return; }
      if (sp && !window.getSelection().toString()) { seek(segs[+sp.dataset.i].t0, !audio.paused); return; }
      const sb = e.target.closest('.ap-spk');
      if (sb) {
        const k = sb.dataset.s, want = k === '-' ? null : k;
        let j = segs.findIndex((s, i) => i > active && (s.s || null) === want && (i === 0 || (segs[i - 1].s || null) !== want));
        if (j < 0) j = segs.findIndex(s => (s.s || null) === want);
        if (j >= 0) { showTab('t'); seek(segs[j].t0, !audio.paused); }
        return;
      }
      const eb = e.target.closest('[data-e]');
      if (eb) {
        const en = d.entities[+eb.dataset.e];
        root.querySelectorAll('[data-e]').forEach(b => b.classList.toggle('on', b === eb));
        $('.ap-ment').innerHTML = '<ul class="ap-list">' + en.segs.slice(0, 40).map(i => '<li><a href="#" data-t="' + segs[i].t0 + '">' + tc(segs[i].t0) + '</a> ' + esc(nameOf(segs[i].s)) + ': ' + esc(segs[i].text.slice(0, 110)) + '…</li>').join('') + '</ul>';
        return;
      }
      const kb = e.target.closest('[data-k]');
      if (kb) { const f = $('.ap-find'); f.value = kb.dataset.k; find(f.value); showTab('t'); }
    });
    function showTab(tab) { root.querySelector('.ap-tabs button[data-tab="' + tab + '"]').click(); }

    function find(q) {
      q = (q || '').trim().toLowerCase();
      hits = [];
      spans.forEach((el, i) => {
        const txt = el.querySelector('.ap-txt'), text = segs[i].text, at = q.length > 1 ? text.toLowerCase().indexOf(q) : -1;
        el.classList.toggle('hit', at >= 0);
        if (at >= 0) {
          hits.push(i);
          let out = '', from = 0, low = text.toLowerCase(), k;
          while ((k = low.indexOf(q, from)) >= 0) { out += esc(text.slice(from, k)) + '<mark>' + esc(text.slice(k, k + q.length)) + '</mark>'; from = k + q.length; }
          txt.innerHTML = out + esc(text.slice(from));
        } else if (txt.firstElementChild) txt.textContent = text;
      });
      hitAt = -1;
      $('.ap-found').textContent = q.length > 1 ? (hits.length ? hits.length + ' found' : 'none found') : '';
      draw();
    }
    const findBox = $('.ap-find');
    let ft = 0;
    findBox.addEventListener('input', () => { clearTimeout(ft); ft = setTimeout(() => find(findBox.value), 150); });
    findBox.addEventListener('keydown', e => {
      if (e.key !== 'Enter' || !hits.length) return;
      e.preventDefault();
      hitAt = (hitAt + (e.shiftKey ? hits.length - 1 : 1)) % hits.length;
      $('.ap-found').textContent = (hitAt + 1) + ' of ' + hits.length;
      showTab('t');
      seek(segs[hits[hitAt]].t0, !audio.paused);
    });
    if (opts.messages) window.addEventListener('message', e => {
      const m = e.data || {};
      if (m.type === 'archive:seek') seek(+m.t * 1000, !!m.play);
      else if (m.type === 'archive:play' && src) audio.play().catch(() => {});
      else if (m.type === 'archive:pause') audio.pause();
    });
    if (window.ResizeObserver) new ResizeObserver(() => { marks(); draw(); }).observe(rail);
    marks(); draw();
    if (opts.start) seek(opts.start * 1000);
    return { seek, audio, element: root };
  }
  window.ArchivePlayer = { mount: mount };
})();
