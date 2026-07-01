import { useState } from 'react'
import { AuthError, login } from '../auth'

/** 로그인 화면 — 공유 비밀번호 인증. 성공 시 onSuccess 콜백. */
export function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(username, password)
      onSuccess()
    } catch (reason) {
      setError(reason instanceof AuthError ? reason.message : '로그인에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><span className="brand">MAPS</span><p>Mobile Operations</p></div>
        <label>아이디
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>비밀번호
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error ? <div className="login-error">{error}</div> : null}
        <button type="submit" className="login-btn" disabled={busy}>
          {busy ? '로그인 중…' : '로그인'}
        </button>
      </form>
    </div>
  )
}
