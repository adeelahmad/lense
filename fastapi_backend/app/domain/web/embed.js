(function () {
  var data = JSON.parse(document.getElementById('player-data').textContent);
  var t = parseFloat(new URLSearchParams(location.search).get('t') || '0') || 0;
  var el = document.getElementById('player');
  var player = window.ArchivePlayer.mount(el, data, { start: t, messages: true });
  // A share link's player reports its first play on each page load (the link's play count).
  var played = el.getAttribute('data-played');
  if (played && player && player.audio) {
    player.audio.addEventListener('play', function () {
      fetch(played, { method: 'POST', keepalive: true, credentials: 'omit' }).catch(function () {});
    }, { once: true });
  }
})();
