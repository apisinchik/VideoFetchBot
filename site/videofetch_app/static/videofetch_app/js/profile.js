const profileButton = document.querySelector('button.profile');
let profileWindow = null;

if (profileButton) {
  profileButton.addEventListener('click', function () {
    if (!profileWindow) {
      profileWindow = document.createElement('div');
      profileWindow.className = 'profile_window';

      const status = document.createElement('span');
      status.className = 'auth';
      status.textContent = 'Гостевой режим';

      profileWindow.appendChild(status);
      profileButton.insertAdjacentElement('afterend', profileWindow);
    } else {
      profileWindow.remove();
      profileWindow = null;
    }
  });

  document.addEventListener('click', function (e) {
    if (
      profileWindow &&
      !profileWindow.contains(e.target) &&
      !profileButton.contains(e.target)
    ) {
      profileWindow.remove();
      profileWindow = null;
    }
  });
}
