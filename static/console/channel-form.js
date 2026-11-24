document.addEventListener('DOMContentLoaded', () => {
  const name = document.getElementById('id_tvg_name');
  const identifier = document.getElementById('id_tvg_id');
  if (!name || !identifier) return;
  // Follow the name while creating a channel, until the user supplies an ID.
  let following = !identifier.value;

  identifier.addEventListener('input', () => { following = !identifier.value; });
  name.addEventListener('input', () => {
    if (following || !identifier.value) {
      identifier.value = name.value.trim();

      following = true;
    }
  });
});
