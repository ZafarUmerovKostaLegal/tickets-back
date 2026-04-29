(function () {
  if (!window.adminApp.ensureAuth()) return;
  window.adminApp.mountSidebar('users');
  window.adminApp.bindLogout('btn-logout');

  var allUsers = [];
  var tbody = document.getElementById('users-tbody');
  var errEl = document.getElementById('dashboard-error');
  var qInput = document.getElementById('users-q');

  function escapeHtml(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function saveRole(userId, role, btn) {
    btn.disabled = true;
    btn.textContent = '…';
    errEl.style.display = 'none';
    window.adminApp.setRole(userId, role)
      .then(function () {
        btn.textContent = 'Сохранено';
        setTimeout(function () { btn.textContent = 'Сохранить'; btn.disabled = false; }, 1500);
      })
      .catch(function (e) {
        errEl.textContent = e.message || 'Не удалось сохранить роль';
        errEl.style.display = 'block';
        btn.textContent = 'Сохранить';
        btn.disabled = false;
      });
  }

  function render() {
    var q = (qInput.value || '').trim().toLowerCase();
    var users = allUsers.filter(function (u) {
      if (!q) return true;
      var name = (u.display_name || '').toLowerCase();
      var email = (u.email || '').toLowerCase();
      return name.indexOf(q) >= 0 || email.indexOf(q) >= 0;
    });

    tbody.innerHTML = '';
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="loading">Нет пользователей по фильтру</td></tr>';
      return;
    }
    users.forEach(function (u) {
      var tr = document.createElement('tr');
      var avatar = u.picture
        ? '<img class="avatar" src="' + escapeHtml(u.picture) + '" alt="">'
        : '<span class="no-avatar">' + (u.display_name || u.email || '').charAt(0).toUpperCase() + '</span>';
      var name = (u.display_name || u.email || 'ID ' + u.id);
      var select = document.createElement('select');
      window.adminApp.ROLES.forEach(function (r) {
        var opt = document.createElement('option');
        opt.value = r;
        opt.textContent = r;
        if (r === u.role) opt.selected = true;
        select.appendChild(opt);
      });
      var btn = document.createElement('button');
      btn.className = 'btn btn-success';
      btn.textContent = 'Сохранить';
      btn.type = 'button';
      btn.onclick = function () { saveRole(u.id, select.value, btn); };
      tr.innerHTML = '<td>' + avatar + escapeHtml(name) + '</td><td>' + escapeHtml(u.email || '') + '</td><td class="role-cell"></td><td></td>';
      tr.querySelector('.role-cell').appendChild(select);
      tr.querySelector('td:last-child').appendChild(btn);
      tbody.appendChild(tr);
    });
  }

  function loadUsers() {
    window.adminApp.loadUsers()
      .then(function (users) {
        allUsers = users || [];
        render();
      })
      .catch(function (e) {
        tbody.innerHTML = '<tr><td colspan="4" class="loading">Ошибка: ' + escapeHtml(e.message) + '</td></tr>';
      });
  }

  document.getElementById('btn-users-refresh').onclick = loadUsers;
  qInput.oninput = render;
  qInput.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); render(); } };
  loadUsers();
})();
