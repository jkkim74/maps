// 회원 관리 화면 — /api/v1/users (관리자 전용, 게이트가 차단한다).
'use strict';

const $ = (id) => document.getElementById(id);

async function api(path, options) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `요청 실패 (${res.status})`);
  return body;
}

function cell(text) {
  const td = document.createElement('td');
  td.textContent = text ?? '';
  return td;
}

function actionButton(label, handler) {
  const button = document.createElement('button');
  button.className = 'topbar-btn';
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function row(user) {
  const tr = document.createElement('tr');
  [user.id, user.username, user.display_name, user.role, user.status, user.plan,
   user.daily_analysis_limit ?? '기본',
   user.last_login_at ? user.last_login_at.slice(0, 16).replace('T', ' ') : '—',
  ].forEach((value) => tr.appendChild(cell(value)));

  const actions = document.createElement('td');
  const nextStatus = user.status === 'active' ? 'disabled' : 'active';
  actions.appendChild(actionButton(
    user.status === 'active' ? '비활성화' : '활성화',
    () => update(user.id, { status: nextStatus }),
  ));
  actions.appendChild(actionButton(
    user.role === 'admin' ? '일반으로' : '관리자로',
    () => update(user.id, { role: user.role === 'admin' ? 'user' : 'admin' }),
  ));
  actions.appendChild(actionButton('비밀번호 재발급', () => resetPassword(user.id)));
  tr.appendChild(actions);
  return tr;
}

async function load() {
  const body = $('user-rows');
  body.textContent = '';
  try {
    const users = await api('/api/v1/users');
    if (!users.length) {
      body.innerHTML = '<tr><td colspan="9">계정이 없습니다.</td></tr>';
      return;
    }
    users.forEach((user) => body.appendChild(row(user)));
  } catch (err) {
    body.innerHTML = `<tr><td colspan="9">${err.message}</td></tr>`;
  }
}

async function update(id, patch) {
  try {
    await api(`/api/v1/users/${id}`, { method: 'PUT', body: JSON.stringify(patch) });
    await load();
  } catch (err) {
    window.alert(err.message);
  }
}

async function resetPassword(id) {
  try {
    const result = await api(`/api/v1/users/${id}/reset-password`, { method: 'POST' });
    // 임시 비밀번호는 이 순간에만 보인다. 저장하지 않는다.
    window.alert(`${result.username} 임시 비밀번호: ${result.temporary_password}`);
  } catch (err) {
    window.alert(err.message);
  }
}

$('create-user').addEventListener('click', async () => {
  $('create-status').textContent = '생성 중…';
  const limit = $('new-limit').value;
  try {
    await api('/api/v1/users', {
      method: 'POST',
      body: JSON.stringify({
        username: $('new-username').value.trim(),
        password: $('new-password').value,
        display_name: $('new-display').value.trim() || null,
        role: $('new-role').value,
        daily_analysis_limit: limit === '' ? null : Number(limit),
      }),
    });
    $('new-username').value = '';
    $('new-password').value = '';
    $('new-display').value = '';
    $('new-limit').value = '';
    $('create-status').textContent = '계정을 만들었습니다.';
    await load();
  } catch (err) {
    $('create-status').textContent = err.message;
  }
});

load();
