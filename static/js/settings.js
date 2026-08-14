// 내 설정 화면 — /api/v1/users/me 만 사용한다.
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

function fillForm(data) {
  const prefs = data.preferences || {};
  const user = data.user || {};
  const limit = data.analysis_limit < 0 ? '무제한' : `${data.analysis_used_today} / ${data.analysis_limit}`;
  $('me-summary').textContent =
    `${user.username} · ${user.role === 'admin' ? '관리자' : '일반 사용자'}` +
    ` · 요금제 ${user.plan} · 오늘 종목분석 ${limit}`;

  $('pref-landing').value = prefs.landing_screen || 'stock-analysis';
  $('pref-min-score').value = prefs.candidate_min_score ?? '';
  const markets = prefs.candidate_markets || [];
  document.querySelectorAll('.pref-market').forEach((box) => {
    box.checked = markets.includes(box.value);
  });
}

function readForm() {
  const score = $('pref-min-score').value;
  return {
    landing_screen: $('pref-landing').value,
    candidate_min_score: score === '' ? null : Number(score),
    candidate_markets: Array.from(document.querySelectorAll('.pref-market:checked')).map((b) => b.value),
  };
}

async function load() {
  try {
    fillForm(await api('/api/v1/users/me'));
  } catch (err) {
    $('me-summary').textContent = err.message;
  }
}

$('save-prefs').addEventListener('click', async () => {
  $('prefs-status').textContent = '저장 중…';
  try {
    fillForm(await api('/api/v1/users/me/preferences', {
      method: 'PUT',
      body: JSON.stringify(readForm()),
    }));
    $('prefs-status').textContent = '저장했습니다.';
  } catch (err) {
    $('prefs-status').textContent = err.message;
  }
});

$('change-pw').addEventListener('click', async () => {
  $('pw-status').textContent = '변경 중…';
  try {
    await api('/api/v1/users/me/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: $('pw-current').value,
        new_password: $('pw-new').value,
      }),
    });
    $('pw-current').value = '';
    $('pw-new').value = '';
    $('pw-status').textContent = '비밀번호를 변경했습니다.';
  } catch (err) {
    $('pw-status').textContent = err.message;
  }
});

load();
