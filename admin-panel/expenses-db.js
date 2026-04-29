(function () {
  if (!window.adminApp.ensureAuth()) return;
  window.adminApp.mountSidebar('expenses-db');
  window.adminApp.bindLogout('btn-logout');

  var errEl = document.getElementById('page-error');
  var infoEl = document.getElementById('page-info');
  var btn = document.getElementById('btn-reset-db');
  var MAIN = 'Главный администратор';

  function showErr(msg) {
    errEl.textContent = msg;
    errEl.style.display = 'block';
    infoEl.style.display = 'none';
  }
  function showInfo(msg) {
    infoEl.textContent = msg;
    infoEl.style.display = 'block';
    errEl.style.display = 'none';
  }
  function hideMsgs() {
    errEl.style.display = 'none';
    infoEl.style.display = 'none';
  }

  window.adminApp.getMe().then(function (me) {
    var role = (me && me.role) ? String(me.role).trim() : '';
    if (role === MAIN) {
      btn.disabled = false;
      showInfo('Вы вошли как главный администратор. Сброс доступен.');
    } else {
      btn.disabled = true;
      showErr('Сброс базы доступен только главному администратору. Ваша роль: ' + (role || '—') + '.');
    }
  }).catch(function (e) {
    showErr(e.message || 'Не удалось проверить роль');
  });

  btn.addEventListener('click', function () {
    hideMsgs();
    var w =
      'Вы уверены? Будут безвозвратно удалены все данные модуля расходов в PostgreSQL.\n\n' +
      'Продолжить?';
    if (!window.confirm(w)) return;

    var phrase = window.prompt(
      'Для подтверждения введите точно: RESET_EXPENSES_DB',
      ''
    );
    if (phrase !== 'RESET_EXPENSES_DB') {
      if (phrase !== null) showErr('Фраза не совпала. Сброс отменён.');
      return;
    }

    btn.disabled = true;
    window.adminApp
      .resetExpensesDatabase()
      .then(function (data) {
        var msg = (data && data.message) ? data.message : 'Готово.';
        showInfo(msg);
      })
      .catch(function (e) {
        showErr(e.message || 'Ошибка сброса');
      })
      .then(function () {
        window.adminApp.getMe().then(function (me) {
          var role = (me && me.role) ? String(me.role).trim() : '';
          btn.disabled = role !== MAIN;
        }).catch(function () {
          btn.disabled = true;
        });
      });
  });
})();
