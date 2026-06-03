function toggleCard(id) {
  var body = document.getElementById('body-' + id);
  if (body) {
    body.style.display =
      body.style.display === 'none' ? 'block' : 'none';
  }
}

function filterTests(type) {
  document.querySelectorAll('.eval-card').forEach(function(card) {
    var ok = type === 'all' || card.dataset.outcome === type;
    card.classList.toggle('hidden-card', !ok);
  });
  document.querySelectorAll('.controls button').forEach(function(btn) {
    btn.classList.remove('active');
  });
  var btn = document.getElementById('btn-' + type);
  if (btn) btn.classList.add('active');
}
