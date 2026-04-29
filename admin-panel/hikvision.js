(function () {
  if (!window.adminApp.ensureAuth()) return;
  window.adminApp.mountSidebar('hikvision');
  window.adminApp.bindLogout('btn-logout');

  var tbody = document.getElementById('hikvision-users-tbody');
  var errEl = document.getElementById('hikvision-users-error');
  var qInput = document.getElementById('hikvision-users-q');
  var appUsers = [];
  var mappingByEmployeeNo = {};

  function escapeHtml(s) {
    if (!s) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function load() {
    var q = (qInput.value || '').trim();
    errEl.style.display = 'none';
    errEl.textContent = '';
    tbody.innerHTML = '<tr><td colspan="6" class="loading">Загрузка…</td></tr>';
    Promise.all([
      window.adminApp.loadHikvisionUsers({ name: q || null }),
      window.adminApp.listHikvisionMappings(),
      window.adminApp.loadUsers()
    ]).then(function (results) {
        var devices = results[0] || [];
        var mappings = results[1] || [];
        appUsers = results[2] || [];
        mappingByEmployeeNo = {};
        mappings.forEach(function (m) {
          mappingByEmployeeNo[(m.camera_employee_no || '').trim()] = m;
        });

        var result = window.adminApp.dedupeHikvisionUsers(devices);
        if (result.errors.length) {
          errEl.textContent = result.errors.join(' | ');
          errEl.style.display = 'block';
        }
        document.getElementById('metric-cameras-users').textContent = String(result.users.length);
        document.getElementById('metric-cameras-total').textContent = String(result.camera_count);
        if (!result.users.length) {
          tbody.innerHTML = '<tr><td colspan="6" class="loading">Нет данных</td></tr>';
          return;
        }
        tbody.innerHTML = '';
        result.users.forEach(function (u) {
          var tr = document.createElement('tr');
          var employeeNo = (u.employee_no || '').trim();
          var currentMapping = mappingByEmployeeNo[employeeNo];
          var currentUserId = currentMapping ? currentMapping.app_user_id : null;

          var select = document.createElement('select');
          var emptyOpt = document.createElement('option');
          emptyOpt.value = '';
          emptyOpt.textContent = 'Не привязано';
          select.appendChild(emptyOpt);
          appUsers.forEach(function (au) {
            var opt = document.createElement('option');
            opt.value = String(au.id);
            opt.textContent = (au.display_name || au.email || ('ID ' + au.id)) + ' (' + (au.email || '-') + ')';
            if (currentUserId && Number(currentUserId) === Number(au.id)) opt.selected = true;
            select.appendChild(opt);
          });

          var saveBtn = document.createElement('button');
          saveBtn.className = 'btn btn-primary';
          saveBtn.type = 'button';
          saveBtn.textContent = 'Сохранить';
          saveBtn.onclick = function () {
            var selected = Number(select.value || 0);
            if (!employeeNo || !selected) {
              errEl.textContent = 'Выберите пользователя приложения для привязки';
              errEl.style.display = 'block';
              return;
            }
            saveBtn.disabled = true;
            saveBtn.textContent = '...';
            window.adminApp.upsertHikvisionMapping({
              camera_employee_no: employeeNo,
              app_user_id: selected,
              camera_name: (u.name || null)
            }).then(function () {
              saveBtn.textContent = 'Сохранено';
              setTimeout(function () { saveBtn.textContent = 'Сохранить'; saveBtn.disabled = false; }, 1000);
            }).catch(function (e) {
              errEl.textContent = e.message || 'Ошибка сохранения привязки';
              errEl.style.display = 'block';
              saveBtn.textContent = 'Сохранить';
              saveBtn.disabled = false;
            });
          };

          var resetBtn = document.createElement('button');
          resetBtn.className = 'btn';
          resetBtn.type = 'button';
          resetBtn.textContent = 'Сбросить';
          resetBtn.onclick = function () {
            if (!employeeNo) return;
            resetBtn.disabled = true;
            window.adminApp.deleteHikvisionMapping(employeeNo).then(function () {
              select.value = '';
              resetBtn.disabled = false;
            }).catch(function (e) {
              errEl.textContent = e.message || 'Ошибка удаления привязки';
              errEl.style.display = 'block';
              resetBtn.disabled = false;
            });
          };

          var actions = document.createElement('div');
          actions.className = 'role-cell';
          actions.appendChild(saveBtn);
          actions.appendChild(resetBtn);

          tr.innerHTML =
            '<td>' + escapeHtml((u.cameras || []).join(', ') || '-') + '</td>' +
            '<td>' + escapeHtml(employeeNo || '-') + '</td>' +
            '<td>' + escapeHtml(u.name || '-') + '</td>' +
            '<td>' + escapeHtml(u.department || '-') + '</td>' +
            '<td class="role-cell"></td>' +
            '<td class="role-cell"></td>';
          tr.querySelector('td:nth-child(5)').appendChild(select);
          tr.querySelector('td:nth-child(6)').appendChild(actions);
          tbody.appendChild(tr);
        });
      })
      .catch(function (e) {
        errEl.textContent = e.message || 'Не удалось загрузить пользователей с камер';
        errEl.style.display = 'block';
        document.getElementById('metric-cameras-users').textContent = '0';
        document.getElementById('metric-cameras-total').textContent = '0';
        tbody.innerHTML = '<tr><td colspan="6" class="loading">Ошибка</td></tr>';
      });
  }

  document.getElementById('btn-hikvision-users-refresh').onclick = load;
  qInput.onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); load(); } };
  load();
})();
