(function () {
  if (!window.adminApp.ensureAuth()) return;
  window.adminApp.mountSidebar('dashboard');
  window.adminApp.bindLogout('btn-logout');

  var rolesTotal = (window.adminApp.ROLES || []).length;
  document.getElementById('metric-roles-total').textContent = String(rolesTotal);

  Promise.all([
    window.adminApp.loadUsers(),
    window.adminApp.loadHikvisionUsers({})
  ]).then(function (results) {
    var users = results[0] || [];
    var hk = window.adminApp.dedupeHikvisionUsers(results[1] || []);
    document.getElementById('metric-users-total').textContent = String(users.length);
    document.getElementById('metric-cameras-users').textContent = String(hk.users.length);
    document.getElementById('metric-cameras-total').textContent = String(hk.camera_count);
  }).catch(function () {
  });
})();
