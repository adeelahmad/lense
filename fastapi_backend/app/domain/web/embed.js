(function () {
  var data = JSON.parse(document.getElementById('player-data').textContent);
  var t = parseFloat(new URLSearchParams(location.search).get('t') || '0') || 0;
  window.ArchivePlayer.mount(document.getElementById('player'), data, { start: t, messages: true });
})();
