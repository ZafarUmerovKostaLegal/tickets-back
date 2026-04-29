(function () {
  function _isPrivateLanHost(hostname) {
    if (!hostname) return false;
    if (hostname === 'localhost' || hostname === '127.0.0.1') return false;
    if (/^192\.168\.\d{1,3}\.\d{1,3}$/.test(hostname)) return true;
    if (/^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname)) return true;
    return /^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(hostname);
  }

  var API_BASE = (function () {
    var cfg =
      typeof window.ADMIN_API_BASE !== 'undefined' && window.ADMIN_API_BASE !== null
        ? String(window.ADMIN_API_BASE).trim()
        : '';
    if (cfg) return cfg.replace(/\/$/, '');
    var o = window.location;
    if (!o || !o.protocol || !o.hostname) return 'http://localhost:1234';
    var port = String(o.port || '');
    var gwPort =
      typeof window.ADMIN_GATEWAY_PORT !== 'undefined' && window.ADMIN_GATEWAY_PORT != null
        ? String(window.ADMIN_GATEWAY_PORT).trim()
        : '1234';
    var proto = o.protocol;
    var host = o.hostname;
    if (port === gwPort) {
      return o.origin ? o.origin.replace(/\/$/, '') : 'http://localhost:1234';
    }
    if (port === '8080' || port === '8081') {
      return proto + '//' + host + ':' + gwPort;
    }
    if (_isPrivateLanHost(host)) {
      return proto + '//' + host + ':' + gwPort;
    }
    return o.origin ? o.origin.replace(/\/$/, '') : 'http://localhost:1234';
  })();
  var TOKEN_KEY = 'admin_access_token';

  function _networkFailureMessage(url) {
    return (
      'Сервер недоступен (сеть/CORS). Запрос: ' + url +
        '. Проверьте: 1) запущен gateway (docker, порт ' +
        (typeof window.ADMIN_GATEWAY_PORT !== 'undefined' && window.ADMIN_GATEWAY_PORT
          ? window.ADMIN_GATEWAY_PORT
          : '1234') +
        '); 2) в config.js задайте window.ADMIN_API_BASE на URL gateway; ' +
        '3) в .env gateway: ADMIN_FRONTEND_URL = полный URL этой страницы (или CORS_ALLOW_PRIVATE_NETWORK=true в LAN); ' +
        '4) HTTPS-страница не может вызывать HTTP API.'
    );
  }

  function _fetchWithNetworkHint(url, init) {
    return fetch(url, init || {}).catch(function (e) {
      var msg = (e && e.message) ? String(e.message) : '';
      if (e instanceof TypeError || /failed to fetch|load failed|networkerror/i.test(msg)) {
        throw new Error(_networkFailureMessage(url));
      }
      throw e;
    });
  }

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function setToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
  }

  function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
  }

  function apiFetch(path, options) {
    options = options || {};
    var headers = options.headers || {};
    var token = getToken();
    if (token) headers['Authorization'] = 'Bearer ' + token;
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.body);
    }
    var url = API_BASE + path;
    return _fetchWithNetworkHint(url, {
      method: options.method || 'GET',
      headers: headers,
      body: options.body
    });
  }

  var ROLES = ['Сотрудник', 'IT отдел', 'Партнер', 'Администратор', 'Главный администратор', 'Офис менеджер'];

  function showError(el, msg) {
    var box = document.getElementById(el);
    if (!box) return;
    box.textContent = msg;
    box.classList.add('error-msg');
    box.style.display = 'block';
  }

  function hideError(el) {
    var box = document.getElementById(el);
    if (box) box.style.display = 'none';
  }

  window.adminApp = {
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    API_BASE: API_BASE,
    ROLES: ROLES,

    pingGatewayHealth: function () {
      var url = (API_BASE || '').replace(/\/$/, '') + '/health';
      return fetch(url, { method: 'GET' }).then(function (r) {
        return r.ok;
      }).catch(function () {
        return false;
      });
    },

    login: function (username, password) {
      var url = (API_BASE || '').replace(/\/$/, '') + '/api/v1/auth/admin/login';
      return _fetchWithNetworkHint(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username, password: password })
      }).then(function (r) {
        if (r.status === 401) {
          return r.json().then(function (d) { throw new Error(d.detail || 'Неверный логин или пароль'); });
        }
        if (r.status === 404) {
          throw new Error('Сервис входа не найден. Проверьте, что gateway запущен на ' + (API_BASE || '...') + ' и перезапустите контейнеры (docker-compose up -d --build gateway auth).');
        }
        if (r.status === 405) {
          throw new Error('405: запрос ушёл не на gateway (часто админка на :8080, а API на :1234). Проверьте config.js и пересоберите admin-panel; на сервере gateway: ADMIN_FRONTEND_URL=URL_админки для CORS.');
        }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            throw new Error(d.detail || d.message || 'Ошибка входа');
          });
        }
        return r.json().then(function (data) {
          setToken(data.access_token);
        });
      });
    },

    logoutUrl: function () {
      return API_BASE + '/api/v1/auth/azure/logout';
    },

    getMe: function () {
      return apiFetch('/api/v1/users/me').then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) {
          throw new Error(e.detail || 'Не удалось загрузить профиль');
        });
        return r.json();
      });
    },

    resetExpensesDatabase: function () {
      return apiFetch('/api/v1/admin/expenses-database/reset', {
        method: 'POST',
        body: { confirm: 'RESET_EXPENSES_DB' }
      }).then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (r.status === 403) {
          return r.json().catch(function () { return {}; }).then(function (e) {
            throw new Error(e.detail || 'Доступ запрещён — только главный администратор');
          });
        }
        if (r.status === 409) {
          return r.json().catch(function () { return {}; }).then(function (e) {
            throw new Error(e.detail || 'Сброс отключён на сервере');
          });
        }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (e) {
            throw new Error(e.detail || e.message || 'Ошибка сброса БД');
          });
        }
        return r.json();
      });
    },

    loadUsers: function () {
      return apiFetch('/api/v1/users?include_archived=false').then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (r.status === 403) {
          return Promise.reject(
            new Error(
              'Доступ запрещён. Список пользователей: Главный администратор, Администратор, Партнёр, IT отдел или Офис менеджер.'
            )
          );
        }
        if (!r.ok) return Promise.reject(new Error('Не удалось загрузить пользователей'));
        return r.json();
      });
    },

    setRole: function (userId, role) {
      return apiFetch('/api/v1/users/' + userId + '/role', {
        method: 'PATCH',
        body: { role: role }
      }).then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || 'Ошибка'); });
        return r.json();
      });
    },

    loadHikvisionUsers: function (filters) {
      filters = filters || {};
      var q = [];
      if (filters.name) q.push('name=' + encodeURIComponent(filters.name));
      if (filters.employee_no) q.push('employee_no=' + encodeURIComponent(filters.employee_no));
      var path = '/api/v1/attendance/hikvision/users' + (q.length ? ('?' + q.join('&')) : '');
      return apiFetch(path).then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (r.status === 403) return Promise.reject(new Error('Доступ запрещён.'));
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) {
          throw new Error(e.detail || 'Не удалось загрузить пользователей с камер');
        });
        return r.json();
      });
    },

    listHikvisionMappings: function () {
      return apiFetch('/api/v1/attendance/hikvision/mappings').then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) {
          throw new Error(e.detail || 'Не удалось загрузить привязки');
        });
        return r.json();
      });
    },

    upsertHikvisionMapping: function (payload) {
      return apiFetch('/api/v1/attendance/hikvision/mappings', {
        method: 'PUT',
        body: payload
      }).then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) {
          throw new Error(e.detail || 'Не удалось сохранить привязку');
        });
        return r.json();
      });
    },

    deleteHikvisionMapping: function (cameraEmployeeNo) {
      return apiFetch('/api/v1/attendance/hikvision/mappings/' + encodeURIComponent(cameraEmployeeNo), {
        method: 'DELETE'
      }).then(function (r) {
        if (r.status === 401) {
          clearToken();
          window.location.href = 'index.html';
          return Promise.reject(new Error('Unauthorized'));
        }
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (e) {
          throw new Error(e.detail || 'Не удалось удалить привязку');
        });
        return r.json();
      });
    },

    ensureAuth: function () {
      if (!getToken()) {
        window.location.href = 'index.html';
        return false;
      }
      return true;
    },

    ensureGuest: function () {
      if (getToken()) {
        window.location.href = 'dashboard.html';
        return false;
      }
      return true;
    },

    bindLogout: function (id) {
      var el = document.getElementById(id || 'btn-logout');
      if (!el) return;
      el.href = this.logoutUrl();
      el.onclick = function (e) {
        e.preventDefault();
        clearToken();
        window.location.href = API_BASE + '/api/v1/auth/azure/logout';
      };
    },

    mountSidebar: function (activePage) {
      var nav = document.getElementById('sidebar-nav');
      if (!nav) return;
      var items = [
        { id: 'dashboard', href: 'dashboard.html', label: 'Дашборд' },
        { id: 'users', href: 'users.html', label: 'Пользователи' },
        { id: 'expenses-db', href: 'expenses-db.html', label: 'База расходов' },
        { id: 'hikvision', href: 'hikvision.html', label: 'Камеры Hikvision' }
      ];
      nav.innerHTML = items.map(function (item) {
        var cls = item.id === activePage ? 'side-link active' : 'side-link';
        return '<a class="' + cls + '" href="' + item.href + '">' + item.label + '</a>';
      }).join('');
    },

    dedupeHikvisionUsers: function (devices) {
      var uniq = {};
      var cameraMap = {};
      var errors = [];
      (devices || []).forEach(function (d) {
        cameraMap[(d.camera_ip || '-')] = true;
        if (d.error) {
          errors.push('Камера ' + (d.camera_ip || '-') + ': ' + d.error);
          return;
        }
        (d.users || []).forEach(function (u) {
          var emp = (u.employee_no || '').trim();
          var nm = (u.name || '').trim();
          var key = emp ? ('emp:' + emp) : ('name:' + nm.toLowerCase());
          if (!key || key === 'name:') key = 'raw:' + JSON.stringify(u);
          if (!uniq[key]) {
            uniq[key] = {
              employee_no: emp || '-',
              name: nm || '-',
              department: (u.department || '').trim() || '-',
              cameras: {}
            };
          }
          if ((u.department || '').trim() && uniq[key].department === '-') {
            uniq[key].department = (u.department || '').trim();
          }
          uniq[key].cameras[(d.camera_ip || '-')] = true;
        });
      });
      var users = Object.keys(uniq).map(function (k) {
        var item = uniq[k];
        return {
          cameras: Object.keys(item.cameras),
          employee_no: item.employee_no,
          name: item.name,
          department: item.department
        };
      });
      return { users: users, camera_count: Object.keys(cameraMap).length, errors: errors };
    },

  };
})();
